from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import chess
import chess.pgn
import io

from database.database import get_db
from models.game import Game
from models.analysis import Analysis
from schemas.analysis import GameAnalysisHistory, AnalysisResponse
from engine.stockfish import bulk_analyze_async

router = APIRouter(prefix="/analysis", tags=["Game Analysis"])


def classify_move(cp_loss: int, is_best_move: bool, num_legal_moves: int) -> str:
    """
    Classify a move based on centipawn loss compared to the engine's best move.
    
    Thresholds (Chess.com-style):
      - forced:      only 1 legal move was available
      - best:        the played move matches the engine's top choice
      - excellent:   cp_loss <= 10  (negligible difference from best)
      - good:        cp_loss <= 50  (minor inaccuracy, still solid)
      - inaccuracy:  cp_loss <= 100
      - mistake:     cp_loss <= 300
      - blunder:     cp_loss > 300
    """
    if num_legal_moves <= 1:
        return "forced"
    if is_best_move:
        return "best"
    if cp_loss <= 10:
        return "excellent"
    if cp_loss <= 50:
        return "good"
    if cp_loss <= 100:
        return "inaccuracy"
    if cp_loss <= 300:
        return "mistake"
    return "blunder"


def _compute_cp_loss(prev_eval, curr_eval, prev_is_mate, curr_is_mate, white_moved: bool) -> int:
    """
    Compute centipawn loss from the mover's perspective.
    
    All scores are from White's perspective:
      - If White moved: cp_loss = prev_eval - curr_eval  (positive = White lost advantage)
      - If Black moved: cp_loss = curr_eval - prev_eval  (positive = Black lost advantage)
    
    Handles mate-score transitions with fixed large penalties/rewards.
    """
    # Both are normal (non-mate) scores
    if not prev_is_mate and not curr_is_mate:
        if white_moved:
            return prev_eval - curr_eval
        else:
            return curr_eval - prev_eval

    # Transition INTO a forced mate that didn't exist before
    if curr_is_mate and not prev_is_mate:
        # Did the mover allow a mate against themselves?
        if (white_moved and curr_eval < 0) or (not white_moved and curr_eval > 0):
            return 500  # Terrible: allowed forced mate against the mover
        else:
            return -200  # Great: found a forced mate for the mover

    # HAD a forced mate but lost it
    if prev_is_mate and not curr_is_mate:
        if (white_moved and prev_eval > 0) or (not white_moved and prev_eval < 0):
            return 400  # Threw away a winning forced mate
        else:
            return -100  # Escaped a losing mate line (opponent blundered)

    # Both are mate scores — compare mate distances
    if white_moved:
        return prev_eval - curr_eval
    else:
        return curr_eval - prev_eval


@router.post("/{game_id}", response_model=GameAnalysisHistory)
async def generate_game_analysis(game_id: int, db: AsyncSession = Depends(get_db)):
    """
    Parses a completed game, evaluates every position using Stockfish,
    classifies each move (best, good, inaccuracy, mistake, blunder),
    and stores the full evaluation history into the database.
    """
    # 1. Fetch the Game
    result = await db.execute(select(Game).filter(Game.id == game_id))
    game = result.scalars().first()
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
        
    if not game.pgn:
        raise HTTPException(status_code=400, detail="Game has no moves (empty PGN).")

    # 2. Parse the PGN and extract all positions, moves, and metadata
    pgn_io = io.StringIO(game.pgn)
    chess_game = chess.pgn.read_game(pgn_io)
    
    if not chess_game:
        raise HTTPException(status_code=400, detail="Invalid PGN string.")
        
    board = chess_game.board()
    fens = [board.fen()]
    moves_played = [None]            # No move for the starting position
    move_colors = [None]             # Who made the move to reach this ply
    legal_move_counts = [len(list(board.legal_moves))]
    
    for move in chess_game.mainline_moves():
        move_colors.append("white" if board.turn == chess.WHITE else "black")
        moves_played.append(move.uci())
        legal_move_counts.append(len(list(board.legal_moves)))  # choices available BEFORE pushing
        board.push(move)
        fens.append(board.fen())
        
    # 3. Evaluate all positions with Stockfish
    try:
        evals = await bulk_analyze_async(fens, time_limit=0.15)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stockfish engine error: {e}")

    # 4. Classify each move by comparing consecutive evaluations
    classifications = [None]  # No classification for the starting position (ply 0)
    
    for i in range(1, len(evals)):
        prev_eval = evals[i - 1]["score"]
        curr_eval = evals[i]["score"]
        prev_is_mate = evals[i - 1]["mate"]
        curr_is_mate = evals[i]["mate"]
        white_moved = (move_colors[i] == "white")
        
        # Centipawn loss from the mover's perspective
        cp_loss = _compute_cp_loss(prev_eval, curr_eval, prev_is_mate, curr_is_mate, white_moved)
        
        # Compare the actual move to the engine's best move for the source position
        best_move_at_source = evals[i - 1].get("best_move")
        actual_move = moves_played[i]
        is_best = (actual_move == best_move_at_source) if best_move_at_source and actual_move else False
        
        # Number of legal moves at the source position (where the move was made from)
        num_legal = legal_move_counts[i]
        
        classification = classify_move(max(0, cp_loss), is_best, num_legal)
        classifications.append(classification)

    # 5. Clear any existing analysis for this game (idempotent re-analysis)
    await db.execute(Analysis.__table__.delete().where(Analysis.game_id == game.id))

    # 6. Save the evaluation graph + classifications to the database
    analysis_records = []
    for ply, eval_data in enumerate(evals):
        record = Analysis(
            game_id=game.id,
            ply=ply,
            fen=eval_data["fen"],
            score=eval_data["score"],
            is_mate=eval_data["mate"],
            best_move=eval_data["best_move"],
            move_played=moves_played[ply] if ply < len(moves_played) else None,
            classification=classifications[ply] if ply < len(classifications) else None,
        )
        db.add(record)
        analysis_records.append(record)
        
    await db.commit()
    
    # 7. Refresh records from DB to get generated IDs, then return
    response_evals = []
    for record in analysis_records:
        await db.refresh(record)
        response_evals.append(AnalysisResponse(
            id=record.id,
            game_id=record.game_id,
            ply=record.ply,
            fen=record.fen,
            score=record.score,
            is_mate=record.is_mate,
            best_move=record.best_move,
            move_played=record.move_played,
            classification=record.classification,
            created_at=record.created_at
        ))
        
    return GameAnalysisHistory(game_id=game.id, evaluations=response_evals)

@router.get("/{game_id}", response_model=GameAnalysisHistory)
async def get_game_analysis(game_id: int, db: AsyncSession = Depends(get_db)):
    """
    Retrieve the previously generated evaluation graph for a game.
    """
    result = await db.execute(
        select(Analysis).filter(Analysis.game_id == game_id).order_by(Analysis.ply.asc())
    )
    records = result.scalars().all()
    
    if not records:
        raise HTTPException(status_code=404, detail="Analysis not found for this game. Please hit the POST endpoint first.")
        
    return GameAnalysisHistory(game_id=game_id, evaluations=records)

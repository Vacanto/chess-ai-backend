from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import chess
import chess.pgn
import io
from pydantic import BaseModel

from database.database import get_db
from models.game import Game
from models.analysis import Analysis
from schemas.analysis import GameAnalysisHistory, AnalysisResponse
from engine.stockfish import bulk_analyze_async

router = APIRouter(prefix="/analysis", tags=["Game Analysis"])

class PGNAnalysisRequest(BaseModel):
    pgn: str
    time_limit: float = 0.15

def get_material_value(board: chess.Board) -> int:
    """Returns total material value on the board (centipawns)."""
    values = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900}
    val = 0
    for pt, v in values.items():
        val += len(board.pieces(pt, chess.WHITE)) * v
        val -= len(board.pieces(pt, chess.BLACK)) * v
    return val

def classify_move(
    cp_loss: int, 
    played_move: str,
    best_line: dict, 
    second_best_line: dict = None,
    material_before: int = 0,
    material_after: int = 0,
    white_moved: bool = True
) -> str:
    """
    Advanced move classification logic.
    """
    best_move = best_line.get("best_move")
    is_best_move = (played_move == best_move)
    best_eval = best_line["score"]
    
    # --- Brilliant Move Detection ---
    # 1. Sacrifice check
    material_diff = (material_after - material_before) if white_moved else (material_before - material_after)
    is_sacrifice = material_diff <= -200 # Lost at least a minor piece (approx)
    
    # 2. Stability & Quality check
    if is_sacrifice and cp_loss <= 30:
        # Check if it remains winning or improves
        if (white_moved and best_eval > 200) or (not white_moved and best_eval < -200) or (cp_loss <= 0):
            return "Brilliant"

    # --- Great Move Detection ---
    if is_best_move and second_best_line:
        second_best_eval = second_best_line["score"]
        eval_gap = abs(best_eval - second_best_eval)
        # Only move that preserves advantage
        if eval_gap >= 120 and cp_loss <= 10:
            return "Great Move"

    # --- Standard Classification ---
    if is_best_move:
        return "Best"
    if cp_loss <= 20:
        return "Excellent"
    if cp_loss <= 50:
        return "Good"
    if cp_loss <= 100:
        return "Inaccuracy"
    if cp_loss <= 300:
        return "Mistake"
    return "Blunder"


def _compute_cp_loss(best_eval: int, played_eval: int, white_moved: bool) -> int:
    """
    Compute centipawn loss: abs(best_eval - played_eval)
    Stable classification for both sides.
    """
    if white_moved:
        return max(0, best_eval - played_eval)
    else:
        return max(0, played_eval - best_eval)


import math
from schemas.analysis import AnalysisSummary

def get_accuracy(cp_loss: int) -> float:
    """exp(-0.035 * cp_loss) * 100"""
    return max(0.0, min(100.0, math.exp(-0.035 * cp_loss) * 100.0))

@router.post("/{game_id}", response_model=GameAnalysisHistory)
async def generate_game_analysis(game_id: int, debug: bool = False, db: AsyncSession = Depends(get_db)):
    """
    Parses a completed game, evaluates every position using Stockfish,
    classifies each move, and computes accuracy and summary stats.
    """
    result = await db.execute(select(Game).filter(Game.id == game_id))
    game = result.scalars().first()
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
        
    if not game.pgn:
        raise HTTPException(status_code=400, detail="Game has no moves (empty PGN).")

    pgn_io = io.StringIO(game.pgn)
    chess_game = chess.pgn.read_game(pgn_io)
    
    if not chess_game:
        raise HTTPException(status_code=400, detail="Invalid PGN string.")
        
    board = chess_game.board()
    fens = [board.fen()]
    moves_played = [None]
    move_colors = [None]
    material_history = [get_material_value(board)]
    
    for move in chess_game.mainline_moves():
        move_colors.append("white" if board.turn == chess.WHITE else "black")
        moves_played.append(move.uci())
        board.push(move)
        fens.append(board.fen())
        material_history.append(get_material_value(board))
        
    try:
        # bulk_analyze_async now returns detailed data including multipv
        evals = await bulk_analyze_async(fens)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stockfish engine error: {e}")

    processed_evals = []
    
    # Stats counters
    stats = {
        "white": {"acc": [], "weight": [], "blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0},
        "black": {"acc": [], "weight": [], "blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0}
    }

    # ply 0 is starting position (no move)
    processed_evals.append(AnalysisResponse(
        id=0, game_id=game.id, ply=0, fen=evals[0]["fen"],
        score=evals[0]["score"], is_mate=abs(evals[0]["score"]) >= 25000,
        best_move=evals[0]["best_move"], move_played=None, classification=None,
        accuracy=100.0, depth_used=evals[0]["depth_used"], cache_hit=evals[0]["cache_hit"]
    ))

    for i in range(1, len(evals)):
        # Data for the move made from position i-1 to reach position i
        white_moved = (move_colors[i] == "white")
        side_key = "white" if white_moved else "black"
        
        best_line = evals[i - 1]["multipv"][0]
        second_best_line = evals[i - 1]["multipv"][1] if len(evals[i - 1]["multipv"]) > 1 else None
        
        # After move eval
        played_eval = evals[i]["score"]
        best_eval = best_line["score"]
        
        cp_loss = _compute_cp_loss(best_eval, played_eval, white_moved)
        acc = get_accuracy(cp_loss)
        
        # Weighted accuracy logic
        weight = 1.0
        if i < 10: weight = 0.5
        elif cp_loss > 100: weight = 1.5
        
        stats[side_key]["acc"].append(acc)
        stats[side_key]["weight"].append(weight)
        
        classification = classify_move(
            cp_loss, moves_played[i], best_line, second_best_line,
            material_history[i-1], material_history[i], white_moved
        )
        
        # Update counters
        if classification == "Blunder": stats[side_key]["blunders"] += 1
        elif classification == "Mistake": stats[side_key]["mistakes"] += 1
        elif classification == "Inaccuracy": stats[side_key]["inaccuracies"] += 1
        elif classification == "Great Move": stats[side_key]["great"] += 1
        elif classification == "Brilliant": stats[side_key]["brilliant"] += 1

        processed_evals.append(AnalysisResponse(
            id=i, game_id=game.id, ply=i, fen=evals[i]["fen"],
            score=evals[i]["score"], is_mate=abs(evals[i]["score"]) >= 25000,
            best_move=evals[i - 1]["best_move"], move_played=moves_played[i],
            classification=classification, accuracy=acc,
            best_move_eval=best_eval, played_move_eval=played_eval, cp_loss=cp_loss,
            depth_used=evals[i]["depth_used"], cache_hit=evals[i]["cache_hit"],
            multipv_lines=evals[i-1]["multipv"] if debug else None
        ))

    # Compute final weighted accuracies
    def weighted_avg(side):
        if not stats[side]["acc"]: return 0.0
        total_w = sum(stats[side]["weight"])
        if total_w == 0: return 0.0
        return sum(a * w for a, w in zip(stats[side]["acc"], stats[side]["weight"])) / total_w

    summary = AnalysisSummary(
        accuracy_white=weighted_avg("white"), accuracy_black=weighted_avg("black"),
        blunders_white=stats["white"]["blunders"], blunders_black=stats["black"]["blunders"],
        mistakes_white=stats["white"]["mistakes"], mistakes_black=stats["black"]["mistakes"],
        inaccuracies_white=stats["white"]["inaccuracies"], inaccuracies_black=stats["black"]["inaccuracies"],
        great_moves_white=stats["white"]["great"], great_moves_black=stats["black"]["great"],
        brilliant_moves_white=stats["white"]["brilliant"], brilliant_moves_black=stats["black"]["brilliant"]
    )

    # Persistence (idempotent)
    await db.execute(Analysis.__table__.delete().where(Analysis.game_id == game.id))
    for res in processed_evals:
        db.add(Analysis(
            game_id=game.id, ply=res.ply, fen=res.fen, score=res.score,
            is_mate=res.is_mate, best_move=res.best_move, move_played=res.move_played,
            classification=res.classification, accuracy=int(res.accuracy),
            cp_loss=res.cp_loss, best_move_eval=res.best_move_eval,
            played_move_eval=res.played_move_eval, depth_used=res.depth_used
        ))
    await db.commit()
    
    return GameAnalysisHistory(game_id=game.id, evaluations=processed_evals, summary=summary)

@router.post("/pgn", response_model=GameAnalysisHistory)
async def analyze_pgn(req: PGNAnalysisRequest, debug: bool = False):
    """
    Parses a PGN string natively and returns step-by-step analysis without saving to the DB.
    """
    if not req.pgn:
        raise HTTPException(status_code=400, detail="Empty PGN provided.")
        
    pgn_io = io.StringIO(req.pgn)
    chess_game = chess.pgn.read_game(pgn_io)
    if not chess_game:
        raise HTTPException(status_code=400, detail="Invalid PGN string.")
        
    board = chess_game.board()
    fens = [board.fen()]
    moves_played = [None]
    move_colors = [None]
    material_history = [get_material_value(board)]
    
    for move in chess_game.mainline_moves():
        move_colors.append("white" if board.turn == chess.WHITE else "black")
        moves_played.append(move.uci())
        board.push(move)
        fens.append(board.fen())
        material_history.append(get_material_value(board))
        
    try:
        evals = await bulk_analyze_async(fens)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stockfish engine error: {e}")

    processed_evals = []
    stats = {
        "white": {"acc": [], "weight": [], "blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0},
        "black": {"acc": [], "weight": [], "blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0}
    }

    processed_evals.append(AnalysisResponse(
        id=0, game_id=0, ply=0, fen=evals[0]["fen"],
        score=evals[0]["score"], is_mate=abs(evals[0]["score"]) >= 25000,
        best_move=evals[0]["best_move"], move_played=None, classification=None,
        accuracy=100.0, depth_used=evals[0]["depth_used"], cache_hit=evals[0]["cache_hit"]
    ))

    for i in range(1, len(evals)):
        white_moved = (move_colors[i] == "white")
        side_key = "white" if white_moved else "black"
        best_line = evals[i - 1]["multipv"][0]
        second_best_line = evals[i - 1]["multipv"][1] if len(evals[i - 1]["multipv"]) > 1 else None
        played_eval = evals[i]["score"]
        best_eval = best_line["score"]
        cp_loss = _compute_cp_loss(best_eval, played_eval, white_moved)
        acc = get_accuracy(cp_loss)
        
        weight = 1.0
        if i < 10: weight = 0.5
        elif cp_loss > 100: weight = 1.5
        
        stats[side_key]["acc"].append(acc)
        stats[side_key]["weight"].append(weight)
        
        classification = classify_move(
            cp_loss, moves_played[i], best_line, second_best_line,
            material_history[i-1], material_history[i], white_moved
        )
        
        if classification == "Blunder": stats[side_key]["blunders"] += 1
        elif classification == "Mistake": stats[side_key]["mistakes"] += 1
        elif classification == "Inaccuracy": stats[side_key]["inaccuracies"] += 1
        elif classification == "Great Move": stats[side_key]["great"] += 1
        elif classification == "Brilliant": stats[side_key]["brilliant"] += 1

        processed_evals.append(AnalysisResponse(
            id=i, game_id=0, ply=i, fen=evals[i]["fen"],
            score=evals[i]["score"], is_mate=abs(evals[i]["score"]) >= 25000,
            best_move=evals[i - 1]["best_move"], move_played=moves_played[i],
            classification=classification.capitalize(), accuracy=acc,
            best_move_eval=best_eval, played_move_eval=played_eval, cp_loss=cp_loss,
            depth_used=evals[i]["depth_used"], cache_hit=evals[i]["cache_hit"],
            multipv_lines=evals[i-1]["multipv"] if debug else None
        ))

    def weighted_avg(side):
        if not stats[side]["acc"]: return 0.0
        total_w = sum(stats[side]["weight"])
        if total_w == 0: return 0.0
        return sum(a * w for a, w in zip(stats[side]["acc"], stats[side]["weight"])) / total_w

    summary = AnalysisSummary(
        accuracy_white=weighted_avg("white"), accuracy_black=weighted_avg("black"),
        blunders_white=stats["white"]["blunders"], blunders_black=stats["black"]["blunders"],
        mistakes_white=stats["white"]["mistakes"], mistakes_black=stats["black"]["mistakes"],
        inaccuracies_white=stats["white"]["inaccuracies"], inaccuracies_black=stats["black"]["inaccuracies"],
        great_moves_white=stats["white"]["great"], great_moves_black=stats["black"]["great"],
        brilliant_moves_white=stats["white"]["brilliant"], brilliant_moves_black=stats["black"]["brilliant"]
    )
    
    return GameAnalysisHistory(game_id=0, evaluations=processed_evals, summary=summary)

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

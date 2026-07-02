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
from schemas.analysis import (
    GameAnalysisHistory, 
    AnalysisResponse, 
    AnalysisSummary,
    GameReviewResponse,
    ReviewStep,
    ReviewGuessRequest,
    ReviewGuessResponse
)
from engine.stockfish import bulk_analyze_async, analyze_position_async
from services.openings import detect_opening, is_book_move

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

CLASSIFICATION_SYMBOLS = {
    "Brilliant": "!!",
    "Great Move": "!",
    "Best": "",
    "Excellent": "",
    "Good": "",
    "Book": "📖",
    "Inaccuracy": "?!",
    "Mistake": "?",
    "Blunder": "??",
    "Forced": "",
}

def format_score(score: int, is_mate: bool) -> str:
    """Formats the centipawn score into a human-readable string (e.g. +1.50, -0.20, #M3)."""
    if is_mate:
        mate_dist = round((30000 - abs(score)) / 100)
        mate_in = max(1, mate_dist)
        if score > 0:
            return f"#M{mate_in}"
        else:
            return f"#-M{mate_in}"
    else:
        pawns = score / 100.0
        if pawns > 0:
            return f"+{pawns:.2f}"
        elif pawns < 0:
            return f"{pawns:.2f}"
        else:
            return "0.00"

def classify_move(
    cp_loss: int, 
    played_move: str,
    best_line: dict, 
    second_best_line: dict = None,
    material_before: int = 0,
    material_after: int = 0,
    white_moved: bool = True,
    is_forced: bool = False,
    is_book: bool = False
) -> str:
    """
    Advanced move classification logic.
    """
    if is_forced:
        return "Forced"
    if is_book:
        return "Book"

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

def get_accuracy(cp_loss: int) -> float:
    """exp(-0.035 * cp_loss) * 100"""
    return max(0.0, min(100.0, math.exp(-0.035 * cp_loss) * 100.0))

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
    is_forced_list = [False]
    
    for move in chess_game.mainline_moves():
        is_forced_list.append(board.legal_moves.count() == 1)
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
        "white": {"acc": [], "weight": [], "cp_loss": [], "blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0},
        "black": {"acc": [], "weight": [], "cp_loss": [], "blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0}
    }

    # Format starter score
    init_score = evals[0]["score"]
    init_mate = abs(init_score) >= 25000
    init_fmt_score = format_score(init_score, init_mate)

    processed_evals.append(AnalysisResponse(
        id=0, game_id=0, ply=0, fen=evals[0]["fen"],
        score=init_score, is_mate=init_mate,
        best_move=evals[0]["best_move"], move_played=None, classification=None,
        accuracy=100.0, depth_used=evals[0]["depth_used"], cache_hit=evals[0]["cache_hit"],
        symbol=None, pv=None, formatted_score=init_fmt_score
    ))

    for i in range(1, len(evals)):
        white_moved = (move_colors[i] == "white")
        side_key = "white" if white_moved else "black"
        
        best_line = evals[i - 1]["multipv"][0]
        second_best_line = evals[i - 1]["multipv"][1] if len(evals[i - 1]["multipv"]) > 1 else None
        
        played_eval = evals[i]["score"]
        best_eval = best_line["score"]
        
        cp_loss = _compute_cp_loss(best_eval, played_eval, white_moved)
        
        # Check Book and Forced move status
        is_book = is_book_move(moves_played[1:i+1])
        is_forced = is_forced_list[i]
        
        classification = classify_move(
            cp_loss, moves_played[i], best_line, second_best_line,
            material_history[i-1], material_history[i], white_moved,
            is_forced=is_forced, is_book=is_book
        )
        
        if classification in ("Book", "Forced"):
            cp_loss = 0
            acc = 100.0
        else:
            acc = get_accuracy(cp_loss)
        
        weight = 1.0
        if i < 10: weight = 0.5
        elif cp_loss > 100: weight = 1.5
        
        stats[side_key]["acc"].append(acc)
        stats[side_key]["weight"].append(weight)
        stats[side_key]["cp_loss"].append(cp_loss)
        
        if classification == "Blunder": stats[side_key]["blunders"] += 1
        elif classification == "Mistake": stats[side_key]["mistakes"] += 1
        elif classification == "Inaccuracy": stats[side_key]["inaccuracies"] += 1
        elif classification == "Great Move": stats[side_key]["great"] += 1
        elif classification == "Brilliant": stats[side_key]["brilliant"] += 1

        pv_str = " ".join(best_line.get("pv", [])) if best_line.get("pv") else None
        is_mate = abs(played_eval) >= 25000
        fmt_score = format_score(played_eval, is_mate)
        symbol = CLASSIFICATION_SYMBOLS.get(classification)

        processed_evals.append(AnalysisResponse(
            id=i, game_id=0, ply=i, fen=evals[i]["fen"],
            score=played_eval, is_mate=is_mate,
            best_move=evals[i - 1]["best_move"], move_played=moves_played[i],
            classification=classification, accuracy=acc,
            best_move_eval=best_eval, played_move_eval=played_eval, cp_loss=cp_loss,
            depth_used=evals[i]["depth_used"], cache_hit=evals[i]["cache_hit"],
            multipv_lines=evals[i-1]["multipv"] if debug else None,
            pv=pv_str, symbol=symbol, formatted_score=fmt_score
        ))

    def weighted_avg(side):
        if not stats[side]["acc"]: return 0.0
        total_w = sum(stats[side]["weight"])
        if total_w == 0: return 0.0
        return sum(a * w for a, w in zip(stats[side]["acc"], stats[side]["weight"])) / total_w

    acpl_white = sum(stats["white"]["cp_loss"]) / len(stats["white"]["cp_loss"]) if stats["white"]["cp_loss"] else 0.0
    acpl_black = sum(stats["black"]["cp_loss"]) / len(stats["black"]["cp_loss"]) if stats["black"]["cp_loss"] else 0.0

    summary = AnalysisSummary(
        accuracy_white=weighted_avg("white"), accuracy_black=weighted_avg("black"),
        blunders_white=stats["white"]["blunders"], blunders_black=stats["black"]["blunders"],
        mistakes_white=stats["white"]["mistakes"], mistakes_black=stats["black"]["mistakes"],
        inaccuracies_white=stats["white"]["inaccuracies"], inaccuracies_black=stats["black"]["inaccuracies"],
        great_moves_white=stats["white"]["great"], great_moves_black=stats["black"]["great"],
        brilliant_moves_white=stats["white"]["brilliant"], brilliant_moves_black=stats["black"]["brilliant"],
        acpl_white=acpl_white, acpl_black=acpl_black
    )

    opening_name, opening_eco = detect_opening(fens)
    
    return GameAnalysisHistory(
        game_id=0,
        evaluations=processed_evals,
        summary=summary,
        opening_name=opening_name,
        opening_eco=opening_eco
    )

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
    is_forced_list = [False]
    
    for move in chess_game.mainline_moves():
        # Check if the move is forced BEFORE pushing it to the board
        is_forced_list.append(board.legal_moves.count() == 1)
        
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
        "white": {"acc": [], "weight": [], "cp_loss": [], "blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0},
        "black": {"acc": [], "weight": [], "cp_loss": [], "blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0}
    }

    # Format starter score
    init_score = evals[0]["score"]
    init_mate = abs(init_score) >= 25000
    init_fmt_score = format_score(init_score, init_mate)

    # ply 0 is starting position (no move)
    processed_evals.append(AnalysisResponse(
        id=0, game_id=game.id, ply=0, fen=evals[0]["fen"],
        score=init_score, is_mate=init_mate,
        best_move=evals[0]["best_move"], move_played=None, classification=None,
        accuracy=100.0, depth_used=evals[0]["depth_used"], cache_hit=evals[0]["cache_hit"],
        symbol=None, pv=None, formatted_score=init_fmt_score
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
        
        # Check Book and Forced move status
        is_book = is_book_move(moves_played[1:i+1])
        is_forced = is_forced_list[i]
        
        classification = classify_move(
            cp_loss, moves_played[i], best_line, second_best_line,
            material_history[i-1], material_history[i], white_moved,
            is_forced=is_forced, is_book=is_book
        )
        
        # Override loss & accuracy for Book and Forced moves
        if classification in ("Book", "Forced"):
            cp_loss = 0
            acc = 100.0
        else:
            acc = get_accuracy(cp_loss)
        
        # Weighted accuracy logic
        weight = 1.0
        if i < 10: weight = 0.5
        elif cp_loss > 100: weight = 1.5
        
        stats[side_key]["acc"].append(acc)
        stats[side_key]["weight"].append(weight)
        stats[side_key]["cp_loss"].append(cp_loss)
        
        # Update counters
        if classification == "Blunder": stats[side_key]["blunders"] += 1
        elif classification == "Mistake": stats[side_key]["mistakes"] += 1
        elif classification == "Inaccuracy": stats[side_key]["inaccuracies"] += 1
        elif classification == "Great Move": stats[side_key]["great"] += 1
        elif classification == "Brilliant": stats[side_key]["brilliant"] += 1

        # PV line (best continuation)
        pv_str = " ".join(best_line.get("pv", [])) if best_line.get("pv") else None
        
        # Formatted score
        is_mate = abs(played_eval) >= 25000
        fmt_score = format_score(played_eval, is_mate)
        
        # Symbol
        symbol = CLASSIFICATION_SYMBOLS.get(classification)

        processed_evals.append(AnalysisResponse(
            id=i, game_id=game.id, ply=i, fen=evals[i]["fen"],
            score=played_eval, is_mate=is_mate,
            best_move=evals[i - 1]["best_move"], move_played=moves_played[i],
            classification=classification, accuracy=acc,
            best_move_eval=best_eval, played_move_eval=played_eval, cp_loss=cp_loss,
            depth_used=evals[i]["depth_used"], cache_hit=evals[i]["cache_hit"],
            multipv_lines=evals[i-1]["multipv"] if debug else None,
            pv=pv_str, symbol=symbol, formatted_score=fmt_score
        ))

    # Compute final weighted accuracies and ACPL
    def weighted_avg(side):
        if not stats[side]["acc"]: return 0.0
        total_w = sum(stats[side]["weight"])
        if total_w == 0: return 0.0
        return sum(a * w for a, w in zip(stats[side]["acc"], stats[side]["weight"])) / total_w

    acpl_white = sum(stats["white"]["cp_loss"]) / len(stats["white"]["cp_loss"]) if stats["white"]["cp_loss"] else 0.0
    acpl_black = sum(stats["black"]["cp_loss"]) / len(stats["black"]["cp_loss"]) if stats["black"]["cp_loss"] else 0.0

    summary = AnalysisSummary(
        accuracy_white=weighted_avg("white"), accuracy_black=weighted_avg("black"),
        blunders_white=stats["white"]["blunders"], blunders_black=stats["black"]["blunders"],
        mistakes_white=stats["white"]["mistakes"], mistakes_black=stats["black"]["mistakes"],
        inaccuracies_white=stats["white"]["inaccuracies"], inaccuracies_black=stats["black"]["inaccuracies"],
        great_moves_white=stats["white"]["great"], great_moves_black=stats["black"]["great"],
        brilliant_moves_white=stats["white"]["brilliant"], brilliant_moves_black=stats["black"]["brilliant"],
        acpl_white=acpl_white, acpl_black=acpl_black
    )

    # Detect opening
    opening_name, opening_eco = detect_opening(fens)

    # Persistence (idempotent)
    await db.execute(Analysis.__table__.delete().where(Analysis.game_id == game.id))
    for res in processed_evals:
        db.add(Analysis(
            game_id=game.id, ply=res.ply, fen=res.fen, score=res.score,
            is_mate=res.is_mate, best_move=res.best_move, move_played=res.move_played,
            classification=res.classification, accuracy=int(res.accuracy),
            cp_loss=res.cp_loss, best_move_eval=res.best_move_eval,
            played_move_eval=res.played_move_eval, depth_used=res.depth_used,
            pv=res.pv, symbol=res.symbol, formatted_score=res.formatted_score
        ))
    await db.commit()
    
    return GameAnalysisHistory(
        game_id=game.id,
        evaluations=processed_evals,
        summary=summary,
        opening_name=opening_name,
        opening_eco=opening_eco
    )

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
        
    # Compute summary dynamically
    white_records = [r for r in records if r.ply % 2 == 1]
    black_records = [r for r in records if r.ply % 2 == 0 and r.ply > 0]
    
    stats = {
        "white": {"blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0},
        "black": {"blunders": 0, "mistakes": 0, "inaccuracies": 0, "great": 0, "brilliant": 0}
    }
    
    for r in records:
        if r.ply == 0:
            continue
        side_key = "white" if r.ply % 2 == 1 else "black"
        if r.classification == "Blunder": stats[side_key]["blunders"] += 1
        elif r.classification == "Mistake": stats[side_key]["mistakes"] += 1
        elif r.classification == "Inaccuracy": stats[side_key]["inaccuracies"] += 1
        elif r.classification == "Great Move": stats[side_key]["great"] += 1
        elif r.classification == "Brilliant": stats[side_key]["brilliant"] += 1
        
    def weighted_avg(records_list):
        if not records_list:
            return 0.0
        total_w = 0.0
        weighted_sum = 0.0
        for r in records_list:
            weight = 1.0
            if r.ply < 10:
                weight = 0.5
            elif r.cp_loss is not None and r.cp_loss > 100:
                weight = 1.5
            weighted_sum += r.accuracy * weight
            total_w += weight
        return weighted_sum / total_w if total_w > 0 else 0.0
        
    acpl_white = sum(r.cp_loss for r in white_records if r.cp_loss is not None) / len(white_records) if white_records else 0.0
    acpl_black = sum(r.cp_loss for r in black_records if r.cp_loss is not None) / len(black_records) if black_records else 0.0
    
    summary = AnalysisSummary(
        accuracy_white=weighted_avg(white_records),
        accuracy_black=weighted_avg(black_records),
        blunders_white=stats["white"]["blunders"],
        blunders_black=stats["black"]["blunders"],
        mistakes_white=stats["white"]["mistakes"],
        mistakes_black=stats["black"]["mistakes"],
        inaccuracies_white=stats["white"]["inaccuracies"],
        inaccuracies_black=stats["black"]["inaccuracies"],
        great_moves_white=stats["white"]["great"],
        great_moves_black=stats["black"]["great"],
        brilliant_moves_white=stats["white"]["brilliant"],
        brilliant_moves_black=stats["black"]["brilliant"],
        acpl_white=acpl_white,
        acpl_black=acpl_black
    )
    
    # Detect opening
    fens = [r.fen for r in records]
    opening_name, opening_eco = detect_opening(fens)
    
    # Map to schema output
    evaluations_response = []
    for r in records:
        evaluations_response.append(AnalysisResponse.model_validate(r))
        
    return GameAnalysisHistory(
        game_id=game_id,
        evaluations=evaluations_response,
        summary=summary,
        opening_name=opening_name,
        opening_eco=opening_eco
    )


@router.get("/{game_id}/review", response_model=GameReviewResponse)
async def get_game_review(game_id: int, db: AsyncSession = Depends(get_db)):
    """
    Retrieves mistake positions from the database analysis in chronological order.
    """
    # 1. Fetch analysis records ordered by ply
    result = await db.execute(
        select(Analysis).filter(Analysis.game_id == game_id).order_by(Analysis.ply.asc())
    )
    records = result.scalars().all()
    
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Analysis not found for this game. Please run analysis first."
        )
        
    steps = []
    # Loop over plies to find mistakes (ply > 0)
    for r in records:
        if r.ply == 0:
            continue
            
        if r.classification in ("Blunder", "Mistake", "Inaccuracy"):
            # The position before the mistake was at ply - 1
            prev_record = next((prev for prev in records if prev.ply == r.ply - 1), None)
            if not prev_record:
                continue
                
            player_color = "white" if r.ply % 2 == 1 else "black"
            steps.append(ReviewStep(
                ply=r.ply,
                fen_before=prev_record.fen,
                move_played=r.move_played,
                classification=r.classification,
                best_move=r.best_move,
                best_move_score=r.best_move_eval if r.best_move_eval is not None else r.score,
                played_move_score=r.played_move_eval if r.played_move_eval is not None else r.score,
                player_color=player_color
            ))
            
    return GameReviewResponse(game_id=game_id, steps=steps)


@router.post("/{game_id}/review/guess", response_model=ReviewGuessResponse)
async def guess_review_move(
    game_id: int,
    req: ReviewGuessRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Validates a player's guessed move for a mistake position.
    If correct or close to best move (<= 50 CP loss), returns success.
    """
    # 1. Load the analysis record for the mistake ply
    result = await db.execute(
        select(Analysis)
        .filter(Analysis.game_id == game_id, Analysis.ply == req.ply)
    )
    record = result.scalars().first()
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Analysis record not found for ply {req.ply}."
        )
        
    if record.classification not in ("Blunder", "Mistake", "Inaccuracy"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ply {req.ply} was not classified as a mistake in the game."
        )
        
    # 2. Get the position before the move (from ply - 1)
    prev_result = await db.execute(
        select(Analysis)
        .filter(Analysis.game_id == game_id, Analysis.ply == req.ply - 1)
    )
    prev_record = prev_result.scalars().first()
    
    if not prev_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Preceding position not found for ply {req.ply - 1}."
        )
        
    # 3. Validate user's guess legality in the preceding position
    board = chess.Board(prev_record.fen)
    try:
        move = chess.Move.from_uci(req.guess_move)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid move format: {req.guess_move}"
        )
        
    if move not in board.legal_moves:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Guessed move {req.guess_move} is illegal in this position."
        )
        
    # 4. If guess matches the best move directly, it's correct!
    best_move_val = record.best_move
    best_move_score = record.best_move_eval if record.best_move_eval is not None else record.score
    
    if req.guess_move == best_move_val:
        return ReviewGuessResponse(
            correct=True,
            guessed_move_score=best_move_score,
            best_move_score=best_move_score,
            difference=0,
            classification="Best",
            message="Correct! You found the best move."
        )
        
    # 5. Evaluate the guessed move on the fly using Stockfish
    board.push(move)
    analysis = await analyze_position_async(board.fen(), time_limit=0.15)
    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to evaluate guess with Stockfish."
        )
        
    guessed_move_score = analysis["score"]
    
    # Compute centipawn loss of guess compared to the best move
    white_moved = (req.ply % 2 == 1)
    loss = _compute_cp_loss(best_move_score, guessed_move_score, white_moved)
    
    # Classify the guess quality
    if loss <= 0:
        correct = True
        classification = "Best"
        message = "Correct! You found the best move."
    elif loss <= 30:
        correct = True
        classification = "Excellent"
        message = "Excellent move! That is also correct."
    elif loss <= 50:
        correct = True
        classification = "Good"
        message = "Good move! That is also acceptable."
    elif loss <= 100:
        correct = False
        classification = "Inaccuracy"
        message = "Incorrect. That move is an inaccuracy."
    elif loss <= 300:
        correct = False
        classification = "Mistake"
        message = "Incorrect. That move is a mistake."
    else:
        correct = False
        classification = "Blunder"
        message = "Incorrect. That move is a blunder!"
        
    return ReviewGuessResponse(
        correct=correct,
        guessed_move_score=guessed_move_score,
        best_move_score=best_move_score,
        difference=loss,
        classification=classification,
        message=message
    )

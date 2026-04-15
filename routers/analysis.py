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

@router.post("/{game_id}", response_model=GameAnalysisHistory)
async def generate_game_analysis(game_id: int, db: AsyncSession = Depends(get_db)):
    """
    Parses a completed game, evaluates every move using Stockfish,
    and stores the evaluation history graph into the database.
    """
    # 1. Fetch the Game
    result = await db.execute(select(Game).filter(Game.id == game_id))
    game = result.scalars().first()
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
        
    if not game.pgn:
        raise HTTPException(status_code=400, detail="Game has no moves (empty PGN).")

    # 2. Extract all FENs from the start to the end of the game
    pgn_io = io.StringIO(game.pgn)
    chess_game = chess.pgn.read_game(pgn_io)
    
    if not chess_game:
        raise HTTPException(status_code=400, detail="Invalid PGN string.")
        
    board = chess_game.board()
    fens = [board.fen()] # initial position
    
    # Replay game to gather all intermediate FENs
    for move in chess_game.mainline_moves():
        board.push(move)
        fens.append(board.fen())
        
    # 3. Process all FENs asynchronously in a single Stockfish boot
    try:
        # Give 0.1s to 0.5s per move. For long games, we keep it brief.
        evals = await bulk_analyze_async(fens, time_limit=0.1)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stockfish engine error: {e}")

    # 4. Clear any existing analysis for this game (idempotent design)
    await db.execute(Analysis.__table__.delete().where(Analysis.game_id == game.id))

    # 5. Save the new evaluation graph to the Database
    analysis_records = []
    for ply, eval_data in enumerate(evals):
        record = Analysis(
            game_id=game.id,
            ply=ply,
            fen=eval_data["fen"],
            score=eval_data["score"],
            is_mate=eval_data["mate"],
            best_move=eval_data["best_move"]
        )
        db.add(record)
        analysis_records.append(record)
        
    await db.commit()
    
    # 6. Fetch them back to return securely (or just map them manually)
    # We will just map them since they are already available in memory to save a query
    response_evals = []
    for record in analysis_records:
        await db.refresh(record) # get the IDs generated
        response_evals.append(AnalysisResponse(
            id=record.id,
            game_id=record.game_id,
            ply=record.ply,
            fen=record.fen,
            score=record.score,
            is_mate=record.is_mate,
            best_move=record.best_move,
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

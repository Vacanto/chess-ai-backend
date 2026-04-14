from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.database import get_db
from models.game import Game
from schemas.game import GameCreate, GameResponse, GameUpdatePGN, GameUpdateStatus

router = APIRouter(prefix="/games", tags=["Games"])

@router.post("/", response_model=GameResponse, status_code=status.HTTP_201_CREATED)
async def start_game(game_in: GameCreate, db: AsyncSession = Depends(get_db)):
    """Start a new chess game."""
    new_game = Game(
        white_player=game_in.white_player,
        black_player=game_in.black_player,
        pgn="",
        status="in_progress"
    )
    db.add(new_game)
    await db.commit()
    await db.refresh(new_game)
    return new_game

@router.get("/{game_id}", response_model=GameResponse)
async def get_game(game_id: int, db: AsyncSession = Depends(get_db)):
    """Retrieve an existing game by its ID."""
    result = await db.execute(select(Game).filter(Game.id == game_id))
    game = result.scalars().first()
    
    if not game:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
        
    return game

@router.put("/{game_id}/pgn", response_model=GameResponse)
async def update_pgn(game_id: int, pgn_data: GameUpdatePGN, db: AsyncSession = Depends(get_db)):
    """Update the PGN tracking for an active game."""
    result = await db.execute(select(Game).filter(Game.id == game_id))
    game = result.scalars().first()
    
    if not game:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
        
    game.pgn = pgn_data.pgn
    await db.commit()
    await db.refresh(game)
    return game

@router.put("/{game_id}/status", response_model=GameResponse)
async def update_status(game_id: int, status_data: GameUpdateStatus, db: AsyncSession = Depends(get_db)):
    """Update the status of an active game (e.g., mark as completed)."""
    result = await db.execute(select(Game).filter(Game.id == game_id))
    game = result.scalars().first()
    
    if not game:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
        
    game.status = status_data.status
    await db.commit()
    await db.refresh(game)
    return game

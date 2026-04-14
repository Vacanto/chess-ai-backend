from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

# Schema for creating a new game
class GameCreate(BaseModel):
    white_player: Optional[str] = None
    black_player: Optional[str] = None

# Schema for appending moves to a game
class GameUpdatePGN(BaseModel):
    pgn: str

# Schema for modifying game status
class GameUpdateStatus(BaseModel):
    status: str  # e.g., 'completed', 'abandoned'

# Schema for returning the Game object
class GameResponse(BaseModel):
    id: int
    white_player: Optional[str] = None
    black_player: Optional[str] = None
    pgn: str
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # This allows Pydantic to read data from SQLAlchemy models
    model_config = ConfigDict(from_attributes=True)

# Schema for submitting a chess move
class MoveRequest(BaseModel):
    move: str  # e.g., "e2e4" (UCI format)

# Schema for validation response
class MoveResponse(BaseModel):
    valid: bool
    new_fen: str
    pgn: str


from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime

class AnalysisResponse(BaseModel):
    id: int
    game_id: int
    ply: int
    fen: str
    score: int
    is_mate: bool
    best_move: Optional[str] = None
    move_played: Optional[str] = None
    classification: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class GameAnalysisHistory(BaseModel):
    game_id: int
    evaluations: List[AnalysisResponse]

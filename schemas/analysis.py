import math
from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Any
from datetime import datetime

class AnalysisSummary(BaseModel):
    accuracy_white: float
    accuracy_black: float
    blunders_white: int
    blunders_black: int
    mistakes_white: int
    mistakes_black: int
    inaccuracies_white: int
    inaccuracies_black: int
    great_moves_white: int
    great_moves_black: int
    brilliant_moves_white: int
    brilliant_moves_black: int

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
    accuracy: float = 0.0
    
    # Debug information
    best_move_eval: Optional[int] = None
    played_move_eval: Optional[int] = None
    cp_loss: Optional[int] = None
    depth_used: Optional[int] = None
    cache_hit: bool = False
    multipv_lines: Optional[List[Any]] = None
    
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class GameAnalysisHistory(BaseModel):
    game_id: int
    evaluations: List[AnalysisResponse]
    summary: Optional[AnalysisSummary] = None

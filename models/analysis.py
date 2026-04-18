from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.sql import func
from .base import Base

class Analysis(Base):
    __tablename__ = "analysis"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    ply = Column(Integer, nullable=False, index=True) # Move index (0 = start pos, 1 = white first move...)
    fen = Column(String, nullable=False)
    score = Column(Integer, nullable=False) # Advantage in centipawns (from white's perspective)
    is_mate = Column(Boolean, default=False)
    accuracy = Column(Integer, default=0) # Move accuracy (0-100)
    cp_loss = Column(Integer, default=0) # Centipawn loss compared to best move
    best_move_eval = Column(Integer, nullable=True) # Score of best move at this ply
    played_move_eval = Column(Integer, nullable=True) # Score of played move at this ply
    depth_used = Column(Integer, default=0) # Engine depth used for this specific position
    
    best_move = Column(String, nullable=True) # The engine's recommended move from this position
    move_played = Column(String, nullable=True) # The actual UCI move that was played (None for starting position)
    classification = Column(String, nullable=True) # Move quality: best, excellent, good, inaccuracy, mistake, blunder, forced
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

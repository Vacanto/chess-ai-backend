from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.sql import func
from .base import Base

class Analysis(Base):
    __tablename__ = "analysis"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    ply = Column(Integer, nullable=False, index=True) # Move index (0 = start pos, 1 = white first move...)
    fen = Column(String, nullable=False)
    score = Column(Integer, nullable=False) # Advantage in centipawns
    is_mate = Column(Boolean, default=False)
    best_move = Column(String, nullable=True) # The recommended AI response
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

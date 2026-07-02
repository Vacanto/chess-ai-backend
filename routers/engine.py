import asyncio
import random
import chess
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from engine.stockfish import get_best_move_async, analyze_position_async

router = APIRouter(tags=["AI Engine"])

class AIMoveRequest(BaseModel):
    fen: str
    difficulty: int = 5  # Difficulty range 1 to 10
    time_limit: float = 0.5  # fallback if difficulty mapping is not used
    delay: bool = True  # whether to simulate thinking delay

class AIAnalyzeRequest(BaseModel):
    fen: str
    time_limit: float = 0.5

# Mapping from 1-10 difficulty to (skill_level, depth, time_limit, blunder_chance)
AI_LEVELS = {
    1: (0, 1, 0.05, 0.25),   # Very Easy: Skill 0, Depth 1, 25% Blunder
    2: (2, 2, 0.05, 0.15),   # Easy: Skill 2, Depth 2, 15% Blunder
    3: (4, 3, 0.05, 0.10),   # Easy: Skill 4, Depth 3, 10% Blunder
    4: (6, 4, 0.10, 0.05),   # Easy: Skill 6, Depth 4, 5% Blunder
    5: (9, 6, 0.15, 0.00),   # Medium: Skill 9, Depth 6
    6: (12, 8, 0.20, 0.00),  # Medium: Skill 12, Depth 8
    7: (15, 10, 0.25, 0.00), # Medium: Skill 15, Depth 10
    8: (17, 12, 0.30, 0.00), # Medium: Skill 17, Depth 12
    9: (20, 16, 0.50, 0.00), # Hard: Skill 20, Depth 16
    10: (20, 20, 1.00, 0.00) # Hardest: Skill 20, Depth 20
}

@router.post("/ai-move")
async def ai_move(req: AIMoveRequest):
    try:
        difficulty = max(1, min(10, req.difficulty))
        skill_level, depth, time_limit, blunder_chance = AI_LEVELS[difficulty]
        
        # Decide if AI commits a blunder for low levels (1-4)
        if blunder_chance > 0 and random.random() < blunder_chance:
            try:
                board = chess.Board(req.fen)
                if board.legal_moves:
                    best_move = random.choice(list(board.legal_moves)).uci()
                else:
                    best_move = None
            except Exception:
                best_move = None
        else:
            best_move = None
            
        # If not a blunder or blunder choice failed, ask Stockfish
        if not best_move:
            best_move = await get_best_move_async(
                req.fen, 
                time_limit=time_limit, 
                skill_level=skill_level, 
                depth=depth
            )
            
        if not best_move:
            raise HTTPException(status_code=400, detail="Could not calculate move")
            
        if req.delay:
            # Simulate a natural human-like thinking delay (1.2 to 2.8 seconds)
            thinking_time = random.uniform(1.2, 2.8)
            await asyncio.sleep(thinking_time)
            
        return {"move": best_move}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/engine/analyze")
async def engine_analyze(req: AIAnalyzeRequest):
    try:
        analysis = await analyze_position_async(req.fen, time_limit=req.time_limit)
        return analysis
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

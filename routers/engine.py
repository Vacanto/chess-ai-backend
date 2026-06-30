import asyncio
import random
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from engine.stockfish import get_best_move_async, analyze_position_async

router = APIRouter(tags=["AI Engine"])

class AIMoveRequest(BaseModel):
    fen: str
    difficulty: int = 15  # default time limit mapping or depth
    time_limit: float = 0.5  # default to 0.5 seconds for calculations
    delay: bool = True  # whether to simulate thinking delay

class AIAnalyzeRequest(BaseModel):
    fen: str
    time_limit: float = 0.5

@router.post("/ai-move")
async def ai_move(req: AIMoveRequest):
    try:
        # Calculate the move using stockfish
        best_move = await get_best_move_async(req.fen, time_limit=req.time_limit)
        if not best_move:
            raise HTTPException(status_code=400, detail="Could not calculate move")
            
        if req.delay:
            # Simulate a natural human-like thinking delay (1.2 to 2.8 seconds)
            # so the frontend timer ticks down
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

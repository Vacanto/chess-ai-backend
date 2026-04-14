from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from engine.stockfish import get_best_move_async, analyze_position_async

router = APIRouter(tags=["AI Engine"])

class AIMoveRequest(BaseModel):
    fen: str
    difficulty: int = 15  # default time limit mapping or depth
    time_limit: float = 0.5  # default to 0.5 seconds for calculations

class AIAnalyzeRequest(BaseModel):
    fen: str
    time_limit: float = 0.5

@router.post("/ai-move")
async def ai_move(req: AIMoveRequest):
    try:
        # Increase time_limit slightly for harder difficulties if you like
        # Currently just using time_limit provided
        best_move = await get_best_move_async(req.fen, time_limit=req.time_limit)
        if not best_move:
            raise HTTPException(status_code=400, detail="Could not calculate move")
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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import chess
import chess.engine
import os
from pathlib import Path

app = FastAPI(title="Chess AI API")

# Allow requests from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stockfish configuration
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import chess
import chess.engine

app = FastAPI(title="Chess AI API")

# ✅ Allow all origins (for mobile + frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ STOCKFISH (FINAL FIX)
try:
    engine_path = "./stockfish"   # 👈 IMPORTANT (your bundled binary)
    engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    print(f"✓ Stockfish loaded from: {engine_path}")
except Exception as e:
    print(f"✗ Error loading Stockfish: {e}")
    engine = None


# ─────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────

class FenRequest(BaseModel):
    fen: str
    think_time: float = 1.5


class MoveResponse(BaseModel):
    from_square: str
    to_square: str
    move: str


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.get("/")
def read_root():
    return {
        "name": "Chess AI API",
        "version": "1.0",
        "endpoints": {
            "health": "/health",
            "ai_move": "/ai-move (POST)"
        }
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok" if engine else "error",
        "engine_loaded": engine is not None,
        "message": "API is running" if engine else "Stockfish engine not loaded"
    }


@app.post("/ai-move", response_model=MoveResponse)
def ai_move(request: FenRequest):

    if not engine:
        raise HTTPException(
            status_code=503,
            detail="Stockfish engine not loaded"
        )

    try:
        board = chess.Board(request.fen)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid FEN: {str(e)}"
        )

    try:
        result = engine.play(board, chess.engine.Limit(time=1.5))

        if not result.move:
            raise HTTPException(
                status_code=400,
                detail="No legal moves available"
            )

        move = result.move

        return MoveResponse(
            from_square=chess.square_name(move.from_square),
            to_square=chess.square_name(move.to_square),
            move=str(move)
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error calculating move: {str(e)}"
        )


# ─────────────────────────────────────────────
# CLEANUP
# ─────────────────────────────────────────────

@app.on_event("shutdown")
def shutdown_event():
    if engine:
        try:
            engine.quit()
            print("✓ Stockfish engine closed")
        except Exception as e:
            print(f"Error closing engine: {e}")


# ─────────────────────────────────────────────
# LOCAL RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
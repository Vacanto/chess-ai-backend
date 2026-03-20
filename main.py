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
def get_engine_path():
    """Get Stockfish path based on the operating system"""
    
    windows_paths = [
        r"C:\Users\ANTO CHARLES\Downloads\stockfish-windows-x86-64-avx2\stockfish\stockfish-windows-x86-64-avx2.exe",
        r"C:\Program Files\stockfish\stockfish.exe",
        r"stockfish",
    ]
    
    unix_paths = [
        "/usr/bin/stockfish",
        "/usr/local/bin/stockfish",
        "stockfish",
    ]
    
    env_path = os.getenv("STOCKFISH_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    
    for path in windows_paths:
        if Path(path).exists():
            return path
    
    for path in unix_paths:
        if Path(path).exists():
            return path
    
    return "stockfish"

# Initialize engine
try:
    engine_path = get_engine_path()
    engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    print(f"✓ Stockfish loaded from: {engine_path}")
except Exception as e:
    print(f"✗ Error loading Stockfish: {e}")
    print("Make sure Stockfish is installed and the path is correct.")
    engine = None

# Request/Response models
class FenRequest(BaseModel):
    fen: str
    think_time: float = 1.5

class MoveResponse(BaseModel):
    from_square: str
    to_square: str
    move: str

@app.get("/health")
def health_check():
    """Check if the API is running and engine is loaded"""
    return {
        "status": "ok" if engine else "error",
        "engine_loaded": engine is not None,
        "message": "API is running" if engine else "Stockfish engine not loaded"
    }

@app.post("/ai-move", response_model=MoveResponse)
def ai_move(request: FenRequest):
    """Get AI move for the given position"""
    
    if not engine:
        raise HTTPException(
            status_code=503,
            detail="Stockfish engine not loaded. Make sure it's installed."
        )
    
    try:
        board = chess.Board(request.fen)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid FEN: {str(e)}"
        )
    
    try:
        # Always use 1.5 seconds for AI thinking
        result = engine.play(board, chess.engine.Limit(time=1.5))
        
        if not result.move:
            raise HTTPException(
                status_code=400,
                detail="No legal moves available"
            )
        
        move = result.move
        from_square = chess.square_name(move.from_square)
        to_square = chess.square_name(move.to_square)
        
        return MoveResponse(
            from_square=from_square,
            to_square=to_square,
            move=f"{from_square}{to_square}"
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error calculating move: {str(e)}"
        )

@app.on_event("shutdown")
def shutdown_event():
    """Clean up engine on shutdown"""
    if engine:
        try:
            engine.quit()
            print("✓ Stockfish engine closed gracefully")
        except Exception as e:
            print(f"Error closing engine: {e}")

@app.get("/")
def read_root():
    """Root endpoint"""
    return {
        "name": "Chess AI API",
        "version": "1.0",
        "endpoints": {
            "health": "/health",
            "ai_move": "/ai-move (POST)"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

import asyncio
import chess
import chess.engine
import os
import stat

# Default to ./stockfish at root, or use environment variable if provided
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCKFISH_PATH = os.getenv("STOCKFISH_PATH", os.path.join(BASE_DIR, "stockfish"))

# On Windows locally, if stockfish.exe exists, use that.
if os.name == 'nt' and not STOCKFISH_PATH.endswith('.exe'):
    test_path = STOCKFISH_PATH + ".exe"
    if os.path.exists(test_path):
        STOCKFISH_PATH = test_path

# Utility to ensure Stockfish has executable permissions (vital for Linux deployments like Render)
def ensure_executable(path):
    if os.path.exists(path):
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC)

async def get_best_move_async(fen: str, time_limit: float = 0.1):
    ensure_executable(STOCKFISH_PATH)
    board = chess.Board(fen)
    transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
    try:
        result = await engine.play(board, chess.engine.Limit(time=time_limit))
        return result.move.uci() if result.move else None
    finally:
        await engine.quit()

async def analyze_position_async(fen: str, time_limit: float = 0.1):
    ensure_executable(STOCKFISH_PATH)
    board = chess.Board(fen)
    transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
    try:
        info = await engine.analyse(board, chess.engine.Limit(time=time_limit))
        
        # safely extract the score from white's perspective
        score = info["score"].white()
        is_mate = score.is_mate()
        # If it's mate, score.score() is None, mate() returns moves to mate
        score_val = score.score() if not is_mate else (100000 if score.mate() > 0 else -100000)
        
        return {
            "score": score_val,
            "mate": is_mate,
            "pv": [m.uci() for m in info.get("pv", [])]
        }
    finally:
        await engine.quit()

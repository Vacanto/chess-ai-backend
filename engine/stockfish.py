import asyncio
import chess
import chess.engine
import os
import stat
import shutil
import urllib.request
import tarfile

# Default to ./stockfish at root, or use environment variable if provided
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCKFISH_PATH = os.getenv("STOCKFISH_PATH", os.path.join(BASE_DIR, "stockfish"))

# On Windows locally, if stockfish.exe exists, use that.
if os.name == 'nt' and not STOCKFISH_PATH.endswith('.exe'):
    test_path = STOCKFISH_PATH + ".exe"
    if os.path.exists(test_path):
        STOCKFISH_PATH = test_path

def setup_stockfish():
    global STOCKFISH_PATH
    
    # On Windows, just return the path (assuming local exe is fine)
    if os.name == 'nt':
        return STOCKFISH_PATH
        
    # We want a path where we have full permissions
    workable_path = "/tmp/stockfish_bin"
    
    if os.path.exists(workable_path) and os.access(workable_path, os.X_OK):
        STOCKFISH_PATH = workable_path
        return STOCKFISH_PATH
        
    # If the user provided stockfish via Render Secret File (often read-only/no-exec)
    if os.path.exists(STOCKFISH_PATH):
        try:
            shutil.copy2(STOCKFISH_PATH, workable_path)
            os.chmod(workable_path, os.stat(workable_path).st_mode | stat.S_IEXEC)
            STOCKFISH_PATH = workable_path
            return STOCKFISH_PATH
        except Exception as e:
            print(f"Error copying/chmodding existing stockfish: {e}")
            
    # If it doesn't exist or we failed to copy it, download it
    print("Downloading stockfish for linux environment...")
    url = "https://github.com/official-stockfish/Stockfish/releases/download/sf_16.1/stockfish-ubuntu-x86-64-avx2.tar"
    tar_path = "/tmp/sf.tar"
    try:
        urllib.request.urlretrieve(url, tar_path)
        with tarfile.open(tar_path) as tar:
            for member in tar.getmembers():
                if "stockfish-ubuntu-x86-64" in member.name and member.isfile():
                    # Extract to workable_path directly
                    with tar.extractfile(member) as source, open(workable_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                    break
        os.remove(tar_path)
        os.chmod(workable_path, os.stat(workable_path).st_mode | stat.S_IEXEC)
        STOCKFISH_PATH = workable_path
    except Exception as e:
        print(f"Failed to download stockfish: {e}")
        
    return STOCKFISH_PATH

# Initialize stockfish path properly
STOCKFISH_PATH = setup_stockfish()

def _extract_score(info):
    """
    Safely extract score, mate flag, and best move from engine analysis info.
    Returns (score_centipawns: int, is_mate: bool, best_move: str | None).
    """
    score = info["score"].white()
    is_mate = score.is_mate()

    if is_mate:
        mate_in = score.mate()
        # Mate(0) means the side to move is already checkmated
        if mate_in == 0:
            score_val = -100000
        else:
            score_val = 100000 if mate_in > 0 else -100000
    else:
        score_val = score.score(default=0)

    pv = [m.uci() for m in info.get("pv", [])]
    best_move = pv[0] if pv else None

    return score_val, is_mate, best_move


async def get_best_move_async(fen: str, time_limit: float = 0.1):
    board = chess.Board(fen)
    transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
    try:
        result = await engine.play(board, chess.engine.Limit(time=time_limit))
        return result.move.uci() if result.move else None
    finally:
        await engine.quit()

async def analyze_position_async(fen: str, time_limit: float = 0.1):
    board = chess.Board(fen)
    transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
    try:
        # Handle terminal positions without calling the engine
        if board.is_game_over():
            if board.is_checkmate():
                s = -100000 if board.turn == chess.WHITE else 100000
                return {"score": s, "mate": True, "pv": []}
            else:
                return {"score": 0, "mate": False, "pv": []}

        info = await engine.analyse(board, chess.engine.Limit(time=time_limit))
        score_val, is_mate, _ = _extract_score(info)

        return {
            "score": score_val,
            "mate": is_mate,
            "pv": [m.uci() for m in info.get("pv", [])]
        }
    finally:
        await engine.quit()

async def bulk_analyze_async(fens: list[str], time_limit: float = 0.1):
    """
    Evaluates a list of FENs using a single engine instance.
    Returns a list of dicts with the analysis for each FEN.
    Handles terminal positions (checkmate/stalemate) gracefully.
    """
    transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
    results = []
    
    try:
        for fen in fens:
            board = chess.Board(fen)
            
            # Handle terminal positions without querying the engine
            if board.is_game_over():
                if board.is_checkmate():
                    # The side to move is checkmated
                    score_val = -100000 if board.turn == chess.WHITE else 100000
                    results.append({
                        "fen": fen,
                        "score": score_val,
                        "mate": True,
                        "best_move": None
                    })
                else:
                    # Stalemate or other draw
                    results.append({
                        "fen": fen,
                        "score": 0,
                        "mate": False,
                        "best_move": None
                    })
                continue

            info = await engine.analyse(board, chess.engine.Limit(time=time_limit))
            score_val, is_mate, best_move = _extract_score(info)
            
            results.append({
                "fen": fen,
                "score": score_val,
                "mate": is_mate,
                "best_move": best_move
            })
            
    finally:
        await engine.quit()
        
    return results

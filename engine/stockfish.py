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

# Limit concurrent engine instances
ENGINE_SEMAPHORE = asyncio.Semaphore(2)

# On Windows locally, if stockfish.exe exists, use that.
if os.name == 'nt' and not STOCKFISH_PATH.endswith('.exe'):
    test_path = STOCKFISH_PATH + ".exe"
    if os.path.exists(test_path):
        STOCKFISH_PATH = test_path

def setup_stockfish():
    global STOCKFISH_PATH
    
    # On Windows, verify if the local exe exists; if not, download the official release
    if os.name == 'nt':
        exe_path = STOCKFISH_PATH if STOCKFISH_PATH.endswith('.exe') else STOCKFISH_PATH + ".exe"
        if os.path.exists(exe_path):
            STOCKFISH_PATH = exe_path
            return STOCKFISH_PATH
            
        print("Stockfish not found on Windows. Downloading Windows build...")
        url = "https://github.com/official-stockfish/Stockfish/releases/download/sf_17/stockfish-windows-x86-64-avx2.zip"
        zip_path = os.path.join(BASE_DIR, "sf.zip")
        try:
            import zipfile
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for member in zip_ref.namelist():
                    if member.endswith(".exe") and "stockfish" in member.lower():
                        with zip_ref.open(member) as source, open(exe_path, "wb") as target:
                            shutil.copyfileobj(source, target)
                        break
            os.remove(zip_path)
            STOCKFISH_PATH = exe_path
            print(f"Downloaded and extracted Stockfish to: {exe_path}")
        except Exception as e:
            print(f"Failed to download stockfish fallback for Windows: {e}")
            
        return STOCKFISH_PATH
        
    # We want a path where we have full permissions
    workable_path = "/tmp/stockfish_bin"
    
    # 1. Check if we already have it in /tmp (previous run)
    if os.path.exists(workable_path) and os.access(workable_path, os.X_OK):
        STOCKFISH_PATH = workable_path
        return STOCKFISH_PATH
        
    # 2. Check the provided path or common Render paths
    possible_paths = [
        STOCKFISH_PATH,
        os.path.join(BASE_DIR, "stockfish", "stockfish-ubuntu-x86-64-avx2"),
        os.path.join(BASE_DIR, "stockfish")
    ]
    
    for p in possible_paths:
        if os.path.exists(p):
            if os.path.isdir(p):
                binary_guess = os.path.join(p, "stockfish-ubuntu-x86-64-avx2")
                if os.path.exists(binary_guess):
                    p = binary_guess
                else:
                    continue
            
            try:
                shutil.copy2(p, workable_path)
                os.chmod(workable_path, os.stat(workable_path).st_mode | stat.S_IEXEC)
                STOCKFISH_PATH = workable_path
                print(f"Using Stockfish binary found at: {p}")
                return STOCKFISH_PATH
            except Exception as e:
                print(f"Error copying/chmodding stockfish from {p}: {e}")
            
    # 3. Fallback download
    print("Fallback: Downloading stockfish for linux environment...")
    url = "https://github.com/official-stockfish/Stockfish/releases/download/sf_17/stockfish-ubuntu-x86-64-avx2.tar"
    tar_path = "/tmp/sf.tar"
    try:
        urllib.request.urlretrieve(url, tar_path)
        with tarfile.open(tar_path) as tar:
            for member in tar.getmembers():
                if "stockfish-ubuntu-x86-64" in member.name and member.isfile():
                    with tar.extractfile(member) as source, open(workable_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                    break
        os.remove(tar_path)
        os.chmod(workable_path, os.stat(workable_path).st_mode | stat.S_IEXEC)
        STOCKFISH_PATH = workable_path
    except Exception as e:
        print(f"Failed to download stockfish fallback: {e}")
        
    return STOCKFISH_PATH

# Initialize stockfish path properly
STOCKFISH_PATH = setup_stockfish()

# Constants
MATE_SCORE = 30000
FEN_CACHE = {}  # In-memory FEN cache

async def get_adaptive_depth(board: chess.Board) -> int:
    depth = 14
    if board.is_check():
        depth += 2
    
    piece_count = len(board.piece_map())
    if piece_count <= 10:
        depth += 2
        
    forcing_moves = any(board.is_capture(m) or board.gives_check(m) for m in board.legal_moves)
    if forcing_moves:
        depth += 2
        
    return min(depth, 20)

def normalize_score(score, board_turn):
    sc = score.white()
    if sc.is_mate():
        mate_in = sc.mate()
        if mate_in == 0:
            return -MATE_SCORE if board_turn == chess.WHITE else MATE_SCORE
        
        cp = MATE_SCORE - abs(mate_in) * 100
        return cp if mate_in > 0 else -cp
    return sc.score() if sc.score() is not None else 0

def _extract_analysis(info, board):
    if not isinstance(info, list):
        info = [info]
        
    lines = []
    for entry in info:
        score_cp = normalize_score(entry["score"], board.turn)
        pv = [m.uci() for m in entry.get("pv", [])]
        lines.append({
            "score": score_cp,
            "pv": pv,
            "best_move": pv[0] if pv else None
        })
    return lines

async def _analyze_position_internal(engine, board: chess.Board, depth: int, multipv: int = 1):
    fen = board.fen()
    cache_key = f"{fen}_{depth}_{multipv}"
    
    if cache_key in FEN_CACHE:
        return FEN_CACHE[cache_key], True

    if board.is_game_over():
        if board.is_checkmate():
            s = -MATE_SCORE if board.turn == chess.WHITE else MATE_SCORE
            res = [{"score": s, "pv": [], "best_move": None}]
        else:
            res = [{"score": 0, "pv": [], "best_move": None}]
        FEN_CACHE[cache_key] = res
        return res, False

    info = await engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
    res = _extract_analysis(info, board)
    
    FEN_CACHE[cache_key] = res
    return res, False

async def get_best_move_async(fen: str, time_limit: float = 0.5):
    """
    Public helper for single best move requests (used by routers/engine.py).
    """
    async with ENGINE_SEMAPHORE:
        transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
        try:
            board = chess.Board(fen)
            result = await engine.play(board, chess.engine.Limit(time=time_limit))
            return result.move.uci() if result.move else None
        finally:
            await engine.quit()

async def analyze_position_async(fen: str, time_limit: float = 0.5):
    """
    Public helper for single position analysis (used by routers/engine.py).
    """
    async with ENGINE_SEMAPHORE:
        transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
        try:
            board = chess.Board(fen)
            info = await engine.analyse(board, chess.engine.Limit(time=time_limit))
            res = _extract_analysis(info, board)
            return res[0] if res else None
        finally:
            await engine.quit()

async def bulk_analyze_async(fens: list[str], debug: bool = False):
    async with ENGINE_SEMAPHORE:
        transport, engine = await chess.engine.popen_uci(STOCKFISH_PATH)
        results = []
        
        try:
            prev_eval = 0
            for fen in fens:
                board = chess.Board(fen)
                
                # PASS 1
                pass1_depth = 12
                pass1_multipv = 3
                analysis_p1, cache_hit_p1 = await _analyze_position_internal(engine, board, pass1_depth, pass1_multipv)
                
                best_line = analysis_p1[0]
                current_eval = best_line.get("score", 0)
                
                # PASS 2
                is_decided = abs(current_eval) > 500
                diff_from_prev = abs(current_eval - prev_eval)
                is_blunder = diff_from_prev > 300
                
                if is_decided or is_blunder:
                    analysis_p2, cache_hit_p2 = analysis_p1[:1], cache_hit_p1
                    adaptive_depth = pass1_depth
                else:
                    adaptive_depth = await get_adaptive_depth(board)
                    analysis_p2, cache_hit_p2 = await _analyze_position_internal(engine, board, adaptive_depth, multipv=1)
                
                prev_eval = current_eval

                # ✅ FIXED INDENTATION (inside loop)
                results.append({
                    "fen": fen,
                    "score": analysis_p2[0]["score"],
                    "best_move": analysis_p2[0]["best_move"],
                    "multipv": analysis_p1,
                    "depth_used": adaptive_depth,
                    "cache_hit": cache_hit_p2,
                    "is_forcing": any(board.is_capture(m) or board.gives_check(m) for m in board.legal_moves)
                })
        
        finally:
            await engine.quit()
        
    return results
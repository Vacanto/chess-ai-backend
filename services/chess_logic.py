import chess
import chess.pgn
import io
from typing import Tuple

def process_move(pgn_str: str, uci_move: str) -> Tuple[bool, str, str]:
    """
    Takes an existing PGN string and a new UCI move.
    Returns:
        valid (bool): Whether the move was legal.
        new_fen (str): The FEN of the board after the move (or current FEN if invalid).
        new_pgn (str): The updated PGN string holding the new move.
    """
    # 1. Load the game from the PGN string
    if not pgn_str:
        game = chess.pgn.Game()
    else:
        pgn_io = io.StringIO(pgn_str)
        game = chess.pgn.read_game(pgn_io)
        if game is None:
            game = chess.pgn.Game()

    # 2. Replay all moves to get to the current board state
    board = game.end().board()
    
    # 3. Check if the user's move is legal
    try:
        move = chess.Move.from_uci(uci_move)
    except ValueError:
        return False, board.fen(), pgn_str
        
    if move not in board.legal_moves:
        return False, board.fen(), pgn_str

    # 4. Apply the move to the board and to the game node
    node = game.end()
    node = node.add_main_variation(move)
    board.push(move)

    # 5. Export the updated game back to PGN format
    exporter = chess.pgn.StringExporter(headers=False, variations=False, comments=False)
    new_pgn_str = game.accept(exporter)

    return True, board.fen(), new_pgn_str


def get_pgn_from_moves(moves: list[str]) -> str:
    """
    Reconstructs a PGN moves sequence string from a list of UCI moves.
    """
    game = chess.pgn.Game()
    node = game
    board = chess.Board()
    for m in moves:
        try:
            move = chess.Move.from_uci(m)
            if move in board.legal_moves:
                node = node.add_main_variation(move)
                board.push(move)
            else:
                break
        except ValueError:
            break
            
    exporter = chess.pgn.StringExporter(headers=False, variations=False, comments=False)
    return game.accept(exporter)

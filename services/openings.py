import json
import os
from typing import Dict, List, Tuple, Set, Optional

# Global cache for openings
OPENINGS_CACHE: Dict[str, Dict] = {}
BOOK_PREFIXES: Set[Tuple[str, ...]] = set()

def load_openings() -> Tuple[Dict[str, Dict], Set[Tuple[str, ...]]]:
    """Loads the openings database into memory if not already loaded."""
    global OPENINGS_CACHE, BOOK_PREFIXES
    if OPENINGS_CACHE:
        return OPENINGS_CACHE, BOOK_PREFIXES
        
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    openings_path = os.path.join(base_dir, "database", "openings.json")
    
    if os.path.exists(openings_path):
        try:
            with open(openings_path, "r", encoding="utf-8") as f:
                openings_data = json.load(f)
                for entry in openings_data:
                    epd = entry.get("epd")
                    if epd:
                        epd_clean = " ".join(epd.strip().split(" ")[:4])
                        OPENINGS_CACHE[epd_clean] = entry
                    
                    uci_str = entry.get("uci")
                    if uci_str:
                        moves = tuple(uci_str.strip().split(" "))
                        # Add all prefixes of this move sequence to the book prefixes set
                        for length in range(1, len(moves) + 1):
                            BOOK_PREFIXES.add(moves[:length])
        except Exception as e:
            print(f"Error loading openings database: {e}")
            
    return OPENINGS_CACHE, BOOK_PREFIXES

# Trigger load on import to populate caches
load_openings()

def detect_opening(fens: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Given a list of FENs/EPDs in chronological order of a game,
    returns (opening_name, opening_eco) of the longest matching prefix.
    """
    openings, _ = load_openings()
    if not openings:
        return None, None
        
    for i in range(len(fens) - 1, -1, -1):
        fen = fens[i]
        epd = " ".join(fen.strip().split(" ")[:4])
        if epd in openings:
            entry = openings[epd]
            return entry.get("name"), entry.get("eco")
            
    return None, None

def is_book_move(moves_sequence: List[str]) -> bool:
    """
    Given a list of UCI moves played in the game so far,
    checks if this sequence is a prefix of any opening in the database.
    """
    _, book_prefixes = load_openings()
    if not moves_sequence or moves_sequence[0] is None:
        return False
    return tuple(moves_sequence) in book_prefixes

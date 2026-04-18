from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, List, Set
import json
import chess

router = APIRouter()

class RoomManager:
    def __init__(self):
        # room_id -> { "players": set(WebSocket), "board": chess.Board, "history": list }
        self.rooms: Dict[str, Dict] = {}

    async def connect(self, room_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.rooms:
            self.rooms[room_id] = {
                "players": set(),
                "board": chess.Board(),
                "history": []
            }
        self.rooms[room_id]["players"].add(websocket)
        
        # Send current state on join
        room = self.rooms[room_id]
        await websocket.send_json({
            "type": "init",
            "fen": room["board"].fen(),
            "history": room["history"]
        })

    def disconnect(self, room_id: str, websocket: WebSocket):
        if room_id in self.rooms:
            self.rooms[room_id]["players"].remove(websocket)
            if not self.rooms[room_id]["players"]:
                del self.rooms[room_id]

    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.rooms:
            disconnected = set()
            for player in self.rooms[room_id]["players"]:
                try:
                    await player.send_json(message)
                except Exception:
                    disconnected.add(player)
            
            for d in disconnected:
                self.rooms[room_id]["players"].remove(d)

manager = RoomManager()

@router.websocket("/ws/game/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    await manager.connect(room_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "move":
                room = manager.rooms[room_id]
                board = room["board"]
                try:
                    move = chess.Move.from_uci(message["move"])
                    if move in board.legal_moves:
                        board.push(move)
                        room["history"].append(message["move"])
                        
                        await manager.broadcast(room_id, {
                            "type": "move",
                            "move": message["move"],
                            "fen": board.fen(),
                            "turn": "white" if board.turn == chess.WHITE else "black"
                        })
                    else:
                        await websocket.send_json({"type": "error", "message": "Illegal move"})
                except ValueError:
                    await websocket.send_json({"type": "error", "message": "Invalid move format"})
                    
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
    except Exception as e:
        print(f"WS error: {e}")
        manager.disconnect(room_id, websocket)

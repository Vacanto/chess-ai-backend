from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, List, Set, Optional
import json
import chess

router = APIRouter()

class RoomManager:
    def __init__(self):
        # room_id -> {
        #     "players": set(WebSocket),
        #     "white_player": WebSocket (or None),
        #     "white_username": str (or None),
        #     "black_player": WebSocket (or None),
        #     "black_username": str (or None),
        #     "board": chess.Board,
        #     "history": list,
        #     "draw_offer_by": str (or None)
        # }
        self.rooms: Dict[str, Dict] = {}

    async def connect(self, room_id: str, websocket: WebSocket, username: Optional[str] = None):
        await websocket.accept()
        if room_id not in self.rooms:
            self.rooms[room_id] = {
                "players": set(),
                "white_player": None,
                "white_username": None,
                "black_player": None,
                "black_username": None,
                "board": chess.Board(),
                "history": [],
                "draw_offer_by": None
            }
        
        room = self.rooms[room_id]
        room["players"].add(websocket)

        # Determine player role
        role = "spectator"
        
        # If username is provided, match existing slot or claim an empty slot
        if username:
            if room["white_username"] == username:
                room["white_player"] = websocket
                role = "white"
            elif room["black_username"] == username:
                room["black_player"] = websocket
                role = "black"
            elif not room["white_player"] and not room["white_username"]:
                room["white_player"] = websocket
                room["white_username"] = username
                role = "white"
            elif not room["black_player"] and not room["black_username"]:
                room["black_player"] = websocket
                room["black_username"] = username
                role = "black"
        else:
            # Fallback to connection order if username is not provided
            if not room["white_player"]:
                room["white_player"] = websocket
                role = "white"
            elif not room["black_player"]:
                room["black_player"] = websocket
                role = "black"

        # Send initial state and assigned role
        await websocket.send_json({
            "type": "init",
            "fen": room["board"].fen(),
            "history": room["history"],
            "role": role,
            "white_username": room["white_username"],
            "black_username": room["black_username"],
            "turn": "white" if room["board"].turn == chess.WHITE else "black"
        })

        # Broadcast status update to all in the room
        await self.broadcast(room_id, {
            "type": "status",
            "message": f"Player '{username or 'Spectator'}' connected as {role}",
            "role": role,
            "username": username,
            "online_players": {
                "white": room["white_player"] is not None,
                "black": room["black_player"] is not None
            }
        })

    def disconnect(self, room_id: str, websocket: WebSocket):
        if room_id in self.rooms:
            room = self.rooms[room_id]
            room["players"].remove(websocket)
            
            # Clear socket reference but keep username for potential reconnection
            if room["white_player"] == websocket:
                room["white_player"] = None
                role = "white"
            elif room["black_player"] == websocket:
                room["black_player"] = None
                role = "black"
            else:
                role = "spectator"
                
            # If room is completely empty, delete it
            if not room["players"]:
                del self.rooms[room_id]
            else:
                # Notify remaining players about disconnect
                asyncio = __import__("asyncio")
                loop = asyncio.get_event_loop()
                loop.create_task(self.broadcast(room_id, {
                    "type": "status",
                    "message": f"Player ({role}) disconnected",
                    "role": role,
                    "online_players": {
                        "white": room["white_player"] is not None,
                        "black": room["black_player"] is not None
                    }
                }))

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
async def websocket_endpoint(websocket: WebSocket, room_id: str, username: Optional[str] = None):
    await manager.connect(room_id, websocket, username)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            room = manager.rooms.get(room_id)
            if not room:
                break
                
            board = room["board"]
            role = "white" if websocket == room["white_player"] else "black" if websocket == room["black_player"] else "spectator"
            
            # 1. HANDLE MOVE
            if message["type"] == "move":
                if role == "spectator":
                    await websocket.send_json({"type": "error", "message": "Spectators cannot make moves"})
                    continue
                    
                is_white_turn = board.turn == chess.WHITE
                if (role == "white" and not is_white_turn) or (role == "black" and is_white_turn):
                    await websocket.send_json({"type": "error", "message": "It is not your turn"})
                    continue
                    
                try:
                    move = chess.Move.from_uci(message["move"])
                    if move in board.legal_moves:
                        board.push(move)
                        room["history"].append(message["move"])
                        
                        # Reset draw offer on new move
                        room["draw_offer_by"] = None
                        
                        # Check game-over conditions
                        game_over_data = None
                        if board.is_checkmate():
                            winner = "white" if is_white_turn else "black"
                            game_over_data = {
                                "type": "game_over",
                                "reason": "checkmate",
                                "winner": winner,
                                "message": f"Checkmate! {winner.capitalize()} wins."
                            }
                        elif board.is_stalemate():
                            game_over_data = {
                                "type": "game_over",
                                "reason": "stalemate",
                                "message": "Draw by stalemate."
                            }
                        elif board.is_insufficient_material():
                            game_over_data = {
                                "type": "game_over",
                                "reason": "insufficient_material",
                                "message": "Draw due to insufficient material."
                            }
                        elif board.is_fivefold_repetition() or board.is_repetition(3):
                            game_over_data = {
                                "type": "game_over",
                                "reason": "repetition",
                                "message": "Draw by repetition."
                            }
                        elif board.is_seventyfive_moves() or board.is_fifty_moves():
                            game_over_data = {
                                "type": "game_over",
                                "reason": "fifty_moves",
                                "message": "Draw by fifty-move rule."
                            }
                            
                        # Broadcast move
                        await manager.broadcast(room_id, {
                            "type": "move",
                            "move": message["move"],
                            "fen": board.fen(),
                            "turn": "white" if board.turn == chess.WHITE else "black"
                        })
                        
                        # If game is over, broadcast game_over event
                        if game_over_data:
                            await manager.broadcast(room_id, game_over_data)
                    else:
                        await websocket.send_json({"type": "error", "message": "Illegal move"})
                except ValueError:
                    await websocket.send_json({"type": "error", "message": "Invalid move format"})

            # 2. HANDLE RESIGNATION
            elif message["type"] == "resign":
                if role == "spectator":
                    await websocket.send_json({"type": "error", "message": "Spectators cannot resign"})
                    continue
                winner = "black" if role == "white" else "white"
                await manager.broadcast(room_id, {
                    "type": "game_over",
                    "reason": "resignation",
                    "winner": winner,
                    "message": f"{role.capitalize()} resigned. {winner.capitalize()} wins!"
                })

            # 3. HANDLE DRAW OFFER
            elif message["type"] == "offer_draw":
                if role == "spectator":
                    await websocket.send_json({"type": "error", "message": "Spectators cannot offer draws"})
                    continue
                room["draw_offer_by"] = role
                await manager.broadcast(room_id, {
                    "type": "draw_offered",
                    "by": role,
                    "message": f"{role.capitalize()} offered a draw."
                })

            # 4. HANDLE DRAW RESPONSE
            elif message["type"] == "respond_draw":
                if role == "spectator":
                    await websocket.send_json({"type": "error", "message": "Spectators cannot respond to draw offers"})
                    continue
                if not room["draw_offer_by"]:
                    await websocket.send_json({"type": "error", "message": "No active draw offer"})
                    continue
                if room["draw_offer_by"] == role:
                    await websocket.send_json({"type": "error", "message": "You cannot accept your own draw offer"})
                    continue
                
                accepted = message.get("accepted", False)
                room["draw_offer_by"] = None
                
                if accepted:
                    await manager.broadcast(room_id, {
                        "type": "game_over",
                        "reason": "draw_agreement",
                        "message": "Draw by agreement."
                    })
                else:
                    await manager.broadcast(room_id, {
                        "type": "draw_declined",
                        "message": f"Draw offer declined by {role}."
                    })

            # 5. HANDLE IN-GAME CHAT
            elif message["type"] == "chat":
                sender_name = room["white_username"] if role == "white" else room["black_username"] if role == "black" else "Spectator"
                if not sender_name:
                    sender_name = role.capitalize()
                await manager.broadcast(room_id, {
                    "type": "chat",
                    "sender": role,
                    "username": sender_name,
                    "message": message["message"]
                })
                    
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
    except Exception as e:
        print(f"WS error: {e}")
        manager.disconnect(room_id, websocket)


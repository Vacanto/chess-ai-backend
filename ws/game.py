from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Dict, List, Set, Optional
import json
import chess
import io
import chess.pgn

from database.database import AsyncSessionLocal
from models.game import Game
from services.chess_logic import get_pgn_from_moves
from sqlalchemy import select, update

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

    async def connect(self, room_id: str, websocket: WebSocket, passcode: Optional[str] = None, username: Optional[str] = None):
        await websocket.accept()
        
        # 1. Database lookup for DB-backed game
        db_game = None
        try:
            game_id = int(room_id)
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Game).filter(Game.id == game_id))
                db_game = result.scalars().first()
        except ValueError:
            pass # room_id is not a valid integer database ID
            
        # 2. Initialize room if not exists
        if room_id not in self.rooms:
            # Reconstruct board state and history from DB PGN if available
            board = chess.Board()
            history = []
            if db_game and db_game.pgn:
                try:
                    pgn_io = io.StringIO(db_game.pgn)
                    parsed_game = chess.pgn.read_game(pgn_io)
                    if parsed_game:
                        temp_board = parsed_game.board()
                        for move in parsed_game.mainline_moves():
                            history.append(move.uci())
                            temp_board.push(move)
                        board = temp_board
                except Exception as e:
                    print(f"Error loading PGN from DB for room {room_id}: {e}")
                    
            self.rooms[room_id] = {
                "players": set(),
                "white_player": None,
                "white_username": None,
                "black_player": None,
                "black_username": None,
                "board": board,
                "history": history,
                "draw_offer_by": None
            }
        
        room = self.rooms[room_id]
        room["players"].add(websocket)

        # 3. Determine player role using passcodes (if DB-backed game)
        role = "spectator"
        
        if db_game:
            if passcode == db_game.white_passcode:
                room["white_player"] = websocket
                room["white_username"] = username or db_game.white_player or "Guest (White)"
                role = "white"
            elif passcode == db_game.black_passcode:
                room["black_player"] = websocket
                room["black_username"] = username or db_game.black_player or "Guest (Black)"
                role = "black"
        else:
            # Fallback to connection order / username matching if not DB-backed
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
        display_name = username or ("Guest" if role != "spectator" else "Spectator")
        await self.broadcast(room_id, {
            "type": "status",
            "message": f"Player '{display_name}' connected as {role}",
            "role": role,
            "username": display_name,
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
                import asyncio
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
async def websocket_endpoint(
    websocket: WebSocket, 
    room_id: str, 
    passcode: Optional[str] = None, 
    username: Optional[str] = None
):
    await manager.connect(room_id, websocket, passcode, username)
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
                            
                        # Update DB if it is a database game
                        try:
                            game_id = int(room_id)
                            new_pgn = get_pgn_from_moves(room["history"])
                            async with AsyncSessionLocal() as session:
                                update_values = {"pgn": new_pgn}
                                if game_over_data:
                                    update_values["status"] = "completed"
                                    
                                await session.execute(
                                    update(Game)
                                    .where(Game.id == game_id)
                                    .values(**update_values)
                                )
                                await session.commit()
                        except ValueError:
                            pass # not a DB-backed game
                        except Exception as e:
                            print(f"Failed to save move to DB: {e}")
                            
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
                
                try:
                    game_id = int(room_id)
                    async with AsyncSessionLocal() as session:
                        await session.execute(update(Game).where(Game.id == game_id).values(status="completed"))
                        await session.commit()
                except ValueError:
                    pass
                except Exception as e:
                    print(f"Failed to update resignation status in DB: {e}")

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
                    try:
                        game_id = int(room_id)
                        async with AsyncSessionLocal() as session:
                            await session.execute(update(Game).where(Game.id == game_id).values(status="completed"))
                            await session.commit()
                    except ValueError:
                        pass
                    except Exception as e:
                        print(f"Failed to update draw status in DB: {e}")

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

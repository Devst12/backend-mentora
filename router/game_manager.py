import asyncio
import time
import logging
from typing import Optional, List
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCQGame")

router = APIRouter(prefix="/mcq", tags=["Game Manager"])

# --- MODELS ---
class CreateSession(BaseModel):
    playerLimit: int
    questionTime: int = 20 
    questions: Optional[List[dict]] = None 

# --- GAME MANAGER ---
class GameManager:
    def __init__(self):
        self.active_connections: dict[str, set[WebSocket]] = {} 
        self.sessions = {}

    async def connect(self, websocket: WebSocket, sid: str):
        await websocket.accept()
        if sid not in self.active_connections:
            self.active_connections[sid] = set()
        self.active_connections[sid].add(websocket)

    def disconnect(self, websocket: WebSocket, sid: str):
        if sid in self.active_connections:
            self.active_connections[sid].discard(websocket)

    async def broadcast(self, sid: str, message: dict):
        if sid in self.active_connections:
            for connection in list(self.active_connections[sid]):
                try: 
                    await connection.send_json(message)
                except: 
                    self.active_connections[sid].discard(connection)

    async def broadcast_players_update(self, sid: str):
        session = self.sessions.get(sid)
        if not session: return
        await self.broadcast(sid, {
            "type": "INIT", 
            "payload": {
                "state": session["state"], 
                "players": list(session["players"].values()),
                "config": session["config"]
            }
        })

    def create_session(self, payload: CreateSession):
        sid = uuid4().hex[:6].upper()
        final_questions = payload.questions if payload.questions else []
        
        if not final_questions:
            final_questions = [{"id": "fallback", "text": "No questions found in PDF", "options": ["A", "B", "C", "D"], "correct": "A"}]

        self.sessions[sid] = {
            "id": sid,
            "config": payload.dict(),
            "state": "WAITING", 
            "players": {}, 
            "questions": final_questions, 
            "current_q_index": -1,
            "current_answers": {}, 
            "timer_end": 0,
            "current_q_payload": None
        }
        return sid

    def get_session(self, sid):
        return self.sessions.get(sid)

    def update_config(self, sid, new_player_limit):
        session = self.sessions.get(sid)
        if not session: return False
        if session["state"] != "WAITING": return False
        session["config"]["playerLimit"] = new_player_limit
        return True

    def join_player(self, sid, name, role, uid=None):
        session = self.sessions.get(sid)
        if not session: return None
        final_uid = uid if uid else str(uuid4())[:8]
        active_players = [p for p in session["players"].values() if p["role"] == "player"]
        current_player_count = len(active_players)
        limit = session["config"]["playerLimit"]

        if final_uid in session["players"]:
            session["players"][final_uid]["name"] = name
            user_data = session["players"][final_uid]
        else:
            final_role = role
            if session["state"] != "WAITING": final_role = "spectator"
            
            if role == "player" and current_player_count >= limit: final_role = "spectator"

            user_data = {"id": final_uid, "name": name, "score": 0, "role": final_role, "last_answer": None}
            session["players"][final_uid] = user_data

        return user_data

    def leave_player(self, sid, uid):
        session = self.sessions.get(sid)
        if not session: return False
        if uid in session["players"]:
            del session["players"][uid]
            return True
        return False

    def toggle_user_role(self, sid, uid):
        session = self.sessions.get(sid)
        if not session or session["state"] != "WAITING": return None
        
        if uid in session["players"]:
            current_role = session["players"][uid]["role"]
            new_role = "spectator" if current_role == "player" else "player"
            
            if new_role == "player":
                active_players = [p for p in session["players"].values() if p["role"] == "player"]
                limit = session["config"]["playerLimit"]
                
                if len(active_players) >= limit:
                    raise HTTPException(status_code=403, detail=f"Contestant limit reached ({limit}). Remove a player first.")
            
            session["players"][uid]["role"] = new_role
            return session["players"][uid]
        return None

    def cancel_session(self, sid):
        session = self.sessions.get(sid)
        if not session: return False
        session["state"] = "CANCELLED"
        return True

    def submit_answer(self, sid, uid, answer):
        session = self.sessions.get(sid)
        if not session or session["state"] != "QUESTION": return False
        
        player = session["players"].get(uid)
        if not player or player["role"] != "player": return False

        if uid in session["current_answers"]: return False

        session["current_answers"][uid] = answer
        player["last_answer"] = "answered"
        return True

    async def start_next_question(self, sid):
        session = self.sessions.get(sid)
        if not session: return
        
        session["current_q_index"] += 1
        
        if session["current_q_index"] >= len(session["questions"]):
            session["state"] = "FINISHED"
            session["current_q_payload"] = None
            all_players = list(session["players"].values())
            active_players = [p for p in all_players if p["role"] == "player"]
            
            if active_players:
                max_score = max(p["score"] for p in active_players)
                winners = [p for p in active_players if p["score"] == max_score]
            else:
                winners = []

            await self.broadcast(sid, {
                "type": "GAME_OVER", 
                "payload": sorted(all_players, key=lambda x: x['score'], reverse=True),
                "winners": winners,
                "questions": session["questions"]
            })
            return

        q = session["questions"][session["current_q_index"]]
        session["state"] = "QUESTION"
        session["current_answers"] = {}
        for p in session["players"].values(): p["last_answer"] = None

        session["timer_end"] = time.time() + session["config"]["questionTime"]
        payload = {
            "id": q["id"], 
            "text": q["text"], 
            "options": q["options"], 
            "q_num": session["current_q_index"] + 1, 
            "total": len(session["questions"]),
            "endTime": session["timer_end"],
            "correct": q["correct"]
        }
        session["current_q_payload"] = payload
        
        await self.broadcast_players_update(sid) 
        await self.broadcast(sid, {"type": "NEW_QUESTION", "payload": payload})
        
        asyncio.create_task(self.question_timer(sid, session["config"]["questionTime"], q["id"]))

    async def question_timer(self, sid, duration, qid):
        await asyncio.sleep(duration)
        await self.process_results(sid, qid)

    async def process_results(self, sid, qid):
        session = self.sessions.get(sid)
        if not session or session["state"] != "QUESTION": return

        q = session["questions"][session["current_q_index"]]
        for uid, answer in session["current_answers"].items():
            if uid in session["players"] and answer == q["correct"]:
                session["players"][uid]["score"] += 10

        session["state"] = "LEADERBOARD"
        session["current_q_payload"] = None
        break_end = time.time() + 10
        
        await self.broadcast_players_update(sid) 
        
        await self.broadcast(sid, {
            "type": "ROUND_RESULT", 
            "correct_answer": q["correct"],
            "leaderboard": sorted(session["players"].values(), key=lambda x: x['score'], reverse=True),
            "break_end": break_end
        })
        
        await asyncio.sleep(10) 
        await self.start_next_question(sid)

# --- GLOBAL INSTANCE ---
game_manager = GameManager()

# --- ROUTES ---
@router.post("/session/create")
async def create_session(payload: CreateSession):
    sid = game_manager.create_session(payload)
    return {"sessionId": sid}

@router.post("/session/{sid}/join")
async def join_session(sid: str, payload: dict):
    user = game_manager.join_player(sid, payload["name"], payload.get("role", "player"), payload.get("uid"))
    if not user: raise HTTPException(404, "Session not found")
    await game_manager.broadcast_players_update(sid)
    return user

@router.post("/session/{sid}/update-config")
async def update_session_config(sid: str, payload: dict):
    new_limit = payload.get("playerLimit")
    if not new_limit: raise HTTPException(400, "playerLimit required")
    success = game_manager.update_config(sid, int(new_limit))
    if not success: raise HTTPException(400, "Cannot update: Game already started")
    await game_manager.broadcast_players_update(sid)
    return {"status": "updated", "newLimit": int(new_limit)}

@router.post("/session/{sid}/leave")
async def leave_session(sid: str, payload: dict):
    uid = payload.get("uid")
    if not uid: raise HTTPException(400, "UID required")
    success = game_manager.leave_player(sid, uid)
    if success:
        await game_manager.broadcast_players_update(sid)
        return {"status": "left"}
    raise HTTPException(404, "Player not found")

@router.post("/session/{sid}/cancel")
async def cancel_session(sid: str):
    success = game_manager.cancel_session(sid)
    if success:
        await game_manager.broadcast(sid, {"type": "SESSION_CANCELLED"})
        return {"status": "cancelled"}
    raise HTTPException(400, "Cannot cancel: Game already started")

@router.post("/session/{sid}/toggle-role/{uid}")
async def toggle_role(sid: str, uid: str):
    try:
        user = game_manager.toggle_user_role(sid, uid)
        if not user: raise HTTPException(404, "Cannot toggle role (started or not found)")
        await game_manager.broadcast_players_update(sid)
        return user
    except HTTPException as e:
        raise e

@router.post("/session/{sid}/start")
async def start_game(sid: str):
    await game_manager.start_next_question(sid)
    return {"status": "started"}

@router.websocket("/ws/{sid}/{uid}")
async def websocket_endpoint(websocket: WebSocket, sid: str, uid: str):
    await game_manager.connect(websocket, sid)
    try:
        session = game_manager.get_session(sid)
        if session:
            await game_manager.broadcast_players_update(sid)

            await websocket.send_json({
                "type": "INIT", 
                "payload": {
                    "state": session["state"], 
                    "players": list(session["players"].values()),
                    "config": session["config"]
                }
            })
            
            if session["state"] == "QUESTION" and session["current_q_payload"]:
                await websocket.send_json({
                    "type": "CURRENT_QUESTION",
                    "payload": session["current_q_payload"]
                })
            
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "SUBMIT_ANSWER":
                    game_manager.submit_answer(sid, uid, data.get("answer"))
                    
    except WebSocketDisconnect:
        game_manager.disconnect(websocket, sid)
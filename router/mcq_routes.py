from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from uuid import uuid4
import asyncio
import time

router = APIRouter(prefix="/mcq", tags=["MCQ"])

class CreateSession(BaseModel):
    playerLimit: int
    mcqCount: int
    questionTime: int = 20 

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

    # FIX: Added missing create_session
    def create_session(self, payload: CreateSession):
        sid = uuid4().hex[:6].upper()
        mock_questions = [
            {"id": "q1", "text": "What is the capital of France?", "options": ["Berlin", "Madrid", "Paris", "Rome"], "correct": "Paris"},
            {"id": "q2", "text": "Which language is used for React?", "options": ["Python", "Java", "JavaScript", "C#"], "correct": "JavaScript"},
            {"id": "q3", "text": "What is 5 + 3?", "options": ["5", "8", "10", "15"], "correct": "8"},
        ]
        self.sessions[sid] = {
            "id": sid,
            "config": payload.dict(),
            "state": "WAITING", 
            "players": {}, 
            "questions": mock_questions,
            "current_q_index": -1,
            "current_answers": {}, 
            "timer_end": 0
        }
        return sid

    # FIX: Added missing get_session
    def get_session(self, sid):
        return self.sessions.get(sid)

    def join_player(self, sid, name, role, uid=None):
        session = self.sessions.get(sid)
        if not session: return None
        
        final_uid = uid if uid else str(uuid4())[:8]
        
        # STRICTOR REGULATION: Force spectator if game started
        final_role = role
        if session["state"] != "WAITING":
            final_role = "spectator"

        user_data = {"id": final_uid, "name": name, "score": 0, "role": final_role, "last_answer": None}
        session["players"][final_uid] = user_data
        return user_data

    # FIX: Added missing toggle_user_role
    def toggle_user_role(self, sid, uid):
        session = self.sessions.get(sid)
        if not session or session["state"] != "WAITING": return None
        if uid in session["players"]:
            current = session["players"][uid]["role"]
            session["players"][uid]["role"] = "spectator" if current == "player" else "player"
            return session["players"][uid]
        return None

    # FIX: Added missing submit_answer
    def submit_answer(self, sid, uid, answer):
        session = self.sessions.get(sid)
        if not session or session["state"] != "QUESTION": return False
        
        player = session["players"].get(uid)
        if not player or player["role"] != "player": return False

        session["current_answers"][uid] = answer
        player["last_answer"] = "answered"
        return True

    async def start_next_question(self, sid):
        session = self.sessions.get(sid)
        if not session: return
        
        session["current_q_index"] += 1
        
        if session["current_q_index"] >= len(session["questions"]):
            session["state"] = "FINISHED"
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
        
        await self.broadcast(sid, {
            "type": "NEW_QUESTION", 
            "payload": {
                "id": q["id"], "text": q["text"], "options": q["options"], 
                "q_num": session["current_q_index"] + 1, "total": len(session["questions"]),
                "endTime": session["timer_end"]
            }
        })
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
        sorted_players = sorted(session["players"].values(), key=lambda x: x['score'], reverse=True)
        
        await self.broadcast(sid, {
            "type": "ROUND_RESULT", 
            "correct_answer": q["correct"],
            "leaderboard": sorted_players
        })
        
        await asyncio.sleep(8) 
        await self.start_next_question(sid)

game_manager = GameManager()

# ─────────── API ENDPOINTS ───────────

@router.post("/session/create")
async def create_session(payload: CreateSession):
    sid = game_manager.create_session(payload)
    return {"sessionId": sid}

@router.post("/session/{sid}/join")
async def join_session(sid: str, payload: dict):
    user = game_manager.join_player(sid, payload["name"], payload.get("role", "player"), payload.get("uid"))
    if not user: raise HTTPException(404, "Session not found")
    return user

@router.post("/session/{sid}/toggle-role/{uid}")
async def toggle_role(sid: str, uid: str):
    user = game_manager.toggle_user_role(sid, uid)
    if not user: raise HTTPException(404, "Cannot toggle role (started or not found)")
    session = game_manager.get_session(sid)
    await game_manager.broadcast(sid, {"type": "INIT", "payload": {"state": session["state"], "players": list(session["players"].values())}})
    return user

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
            await websocket.send_json({"type": "INIT", "payload": {"state": session["state"], "players": list(session["players"].values())}})
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "SUBMIT_ANSWER":
                game_manager.submit_answer(sid, uid, data.get("answer"))
                await game_manager.broadcast(sid, {"type": "USER_ANSWERED", "uid": uid})
    except WebSocketDisconnect:
        game_manager.disconnect(websocket, sid)
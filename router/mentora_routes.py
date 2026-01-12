from fastapi import APIRouter, HTTPException, status
from pymongo import MongoClient
from bson.objectid import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel
import os

router = APIRouter()

mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db = client["MentoraDB"]
questions_col = db["questions"]
answers_col = db["answers"]

class QuestionCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    tags: Optional[List[str]] = []

class AnswerCreate(BaseModel):
    text: str

class CommentCreate(BaseModel):
    text: str

def serialize_doc(doc):
    if not doc:
        return None

    doc["_id"] = str(doc["_id"])

    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()

    if "comments" in doc:
        for c in doc["comments"]:
            if "_id" in c:
                c["_id"] = str(c["_id"])
            if isinstance(c.get("created_at"), datetime):
                c["created_at"] = c["created_at"].isoformat()

    return doc

# ────────────────── Questions ──────────────────
@router.get("/MentoraQ/questions")
def get_questions():
    data = list(questions_col.find().sort("_id", -1))
    return [serialize_doc(q) for q in data]

@router.post("/MentoraQ/questions", status_code=201)
def create_question(q: QuestionCreate):
    new_q = {
        "title": q.title,
        "description": q.description,
        "tags": q.tags,
        "created_at": datetime.now(timezone.utc),
        "votes": 0,
        "comments": []
    }
    res = questions_col.insert_one(new_q)
    return serialize_doc(questions_col.find_one({"_id": res.inserted_id}))

@router.get("/MentoraQ/questions/{id}")
def get_question(id: str):
    try:
        oid = ObjectId(id)
    except InvalidId:
        raise HTTPException(400, "Invalid ID")

    q = questions_col.find_one({"_id": oid})
    if not q:
        raise HTTPException(404, "Question not found")
    return serialize_doc(q)

# ────────────────── Answers ──────────────────
@router.get("/MentoraQ/questions/{id}/answers")
def get_answers(id: str):
    data = list(answers_col.find({"question_id": id}))
    return [serialize_doc(a) for a in data]

@router.post("/MentoraQ/questions/{id}/answers")
def post_answer(id: str, a: AnswerCreate):
    try:
        ObjectId(id)
    except InvalidId:
        raise HTTPException(400, "Invalid ID")

    new_a = {
        "question_id": id,
        "text": a.text,
        "votes": 0,
        "comments": [],
        "created_at": datetime.now(timezone.utc)
    }
    answers_col.insert_one(new_a)
    return {"msg": "Answer added"}

# ────────────────── Votes ──────────────────
@router.post("/MentoraQ/vote/{type}/{id}/{value}")
def vote(type: str, id: str, value: int):
    col = questions_col if type == "question" else answers_col
    try:
        oid = ObjectId(id)
    except InvalidId:
        raise HTTPException(400, "Invalid ID")

    col.update_one({"_id": oid}, {"$inc": {"votes": value}})
    return {"msg": "Voted"}

# ────────────────── Comments ──────────────────
@router.post("/MentoraQ/comment/{type}/{id}")
def comment(type: str, id: str, c: CommentCreate):
    col = questions_col if type == "question" else answers_col
    try:
        oid = ObjectId(id)
    except InvalidId:
        raise HTTPException(400, "Invalid ID")

    col.update_one(
        {"_id": oid},
        {"$push": {
            "comments": {
                "_id": ObjectId(),
                "text": c.text,
                "created_at": datetime.now(timezone.utc)
            }
        }}
    )
    return {"msg": "Comment added"}

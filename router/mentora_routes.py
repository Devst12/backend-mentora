from fastapi import APIRouter, HTTPException, status
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel
import os

router = APIRouter()

# DB Setup
mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
try:
    client = MongoClient(mongo_uri)
    db = client["MentoraDB"]
    questions_col = db["questions"]
    answers_col = db["answers"]
    print("✅ Mentora: Connected to MongoDB.")
except Exception as e:
    print(f"❌ Mentora DB Error: {e}")

# Models
class QuestionCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    tags: Optional[List[str]] = []

# Helpers
def serialize_doc(doc):
    if not doc: return None
    doc["_id"] = str(doc["_id"])
    if isinstance(doc.get("created_at"), datetime):
        doc["created_at"] = doc["created_at"].isoformat()
    return doc

# Routes
@router.get('/MentoraQ/questions')
def get_questions():
    try:
        questions = list(questions_col.find().sort("_id", -1))
        return [serialize_doc(q) for q in questions]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post('/backend/MentoraQ/questions', status_code=status.HTTP_201_CREATED)
def create_question(question: QuestionCreate):
    try:
        new_q = {
            "title": question.title,
            "description": question.description,
            "tags": question.tags,
            "created_at": datetime.now(timezone.utc),
            "votes": 0,
            "comments": []
        }
        result = questions_col.insert_one(new_q)
        return serialize_doc(questions_col.find_one({"_id": result.inserted_id}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/backend/MentoraQ/questions/{id}')
def get_question_detail(id: str):
    try:
        oid = ObjectId(id)
        q = questions_col.find_one({"_id": oid})
        if not q: raise HTTPException(404, "Not found")
        q.setdefault("votes", 0)
        q.setdefault("comments", [])
        return serialize_doc(q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/MentoraQ/questions/{id}/answers')
def get_answers(id: str):
    try:
        answers = list(answers_col.find({"questionId": id}))
        return [serialize_doc(a) for a in answers]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
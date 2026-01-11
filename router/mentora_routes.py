from fastapi import APIRouter, HTTPException, status
from pymongo import MongoClient
from bson.objectid import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel
import os

router = APIRouter()

# -------------------------
# MongoDB Setup
# -------------------------
mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
try:
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client["MentoraDB"]
    questions_col = db["questions"]
    answers_col = db["answers"]
    print("✅ Mentora: Connected to MongoDB.")
except Exception as e:
    print(f"❌ Mentora DB Error: {e}")

# -------------------------
# Pydantic Models
# -------------------------
class QuestionCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    tags: Optional[List[str]] = []

# -------------------------
# Helper: Serialize Mongo Document
# -------------------------
def serialize_doc(doc):
    if not doc:
        return None
    doc_copy = doc.copy()  # avoid mutating original
    doc_copy["_id"] = str(doc_copy["_id"])
    if isinstance(doc_copy.get("created_at"), datetime):
        doc_copy["created_at"] = doc_copy["created_at"].isoformat()
    # Serialize comments if they exist
    if "comments" in doc_copy and isinstance(doc_copy["comments"], list):
        for c in doc_copy["comments"]:
            if "_id" in c:
                c["_id"] = str(c["_id"])
            if "created_at" in c and isinstance(c["created_at"], datetime):
                c["created_at"] = c["created_at"].isoformat()
    return doc_copy

# -------------------------
# Routes
# -------------------------

# Get all questions
@router.get("/MentoraQ/questions")
def get_questions():
    try:
        questions = list(questions_col.find().sort("_id", -1))
        return [serialize_doc(q) for q in questions]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Create a new question
@router.post("/MentoraQ/questions", status_code=status.HTTP_201_CREATED)
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

# Get question details
@router.get("/MentoraQ/questions/{id}")
def get_question_detail(id: str):
    try:
        try:
            oid = ObjectId(id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid question ID")

        q = questions_col.find_one({"_id": oid})
        if not q:
            raise HTTPException(status_code=404, detail="Question not found")

        q.setdefault("votes", 0)
        q.setdefault("comments", [])
        return serialize_doc(q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get answers for a question
@router.get("/MentoraQ/questions/{id}/answers")
def get_answers(id: str):
    try:
        try:
            qid = ObjectId(id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid question ID")

        # Fetch all answers linked to this question
        answers = list(answers_col.find({"questionId": qid}))
        return [serialize_doc(a) for a in answers]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

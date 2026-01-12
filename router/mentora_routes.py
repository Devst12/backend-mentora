from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from datetime import datetime, timezone

from database import questions_col
from auth import get_current_user_email
from models import QuestionCreate, AnswerCreate, CommentCreate
from utils import serialize

router = APIRouter(prefix="/MentoraQ")


# ─────────── CREATE QUESTION ───────────
@router.post("/questions")
def create_question(q: QuestionCreate, email=Depends(get_current_user_email)):
    question = {
        "title": q.title,
        "description": q.description,
        "tags": q.tags,
        "author_email": email,

        "votes": 0,
        "likes": 0,
        "dislikes": 0,
        "liked_by": [],
        "disliked_by": [],

        "answers": [],
        "comments": [],
        "accepted_answer_id": None,
        "created_at": datetime.now(timezone.utc),
    }

    res = questions_col.insert_one(question)
    return serialize(questions_col.find_one({"_id": res.inserted_id}))


# ─────────── GET ALL QUESTIONS ───────────
@router.get("/questions")
def get_questions():
    return [serialize(q) for q in questions_col.find().sort("_id", -1)]


# ─────────── GET SINGLE QUESTION ───────────
@router.get("/questions/{qid}")
def get_question(qid: str):
    q = questions_col.find_one({"_id": ObjectId(qid)})
    if not q:
        raise HTTPException(404, "Question not found")
    return serialize(q)


# ─────────── GET ANSWERS ───────────
@router.get("/questions/{qid}/answers")
def get_answers(qid: str):
    q = questions_col.find_one({"_id": ObjectId(qid)})
    if not q:
        raise HTTPException(404, "Question not found")
    return q.get("answers", [])


# ─────────── ADD ANSWER ───────────
@router.post("/questions/{qid}/answer")
def add_answer(qid: str, a: AnswerCreate, email=Depends(get_current_user_email)):
    answer = {
        "_id": ObjectId(),
        "text": a.text,
        "author_email": email,

        "votes": 0,
        "likes": 0,
        "dislikes": 0,
        "liked_by": [],
        "disliked_by": [],
        "helpful": 0,
        "not_helpful": 0,

        "comments": [],
        "created_at": datetime.now(timezone.utc),
    }

    questions_col.update_one(
        {"_id": ObjectId(qid)},
        {"$push": {"answers": answer}}
    )

    return {"msg": "Answer added"}


# ─────────── COMMENT QUESTION ───────────
@router.post("/questions/{qid}/comment")
def comment_question(qid: str, c: CommentCreate, email=Depends(get_current_user_email)):
    comment = {
        "_id": ObjectId(),
        "text": c.text,
        "author_email": email,
        "likes": 0,
        "dislikes": 0,
        "created_at": datetime.now(timezone.utc),
    }

    questions_col.update_one(
        {"_id": ObjectId(qid)},
        {"$push": {"comments": comment}}
    )

    return {"msg": "Comment added"}


# ─────────── COMMENT ANSWER ───────────
@router.post("/questions/{qid}/answer/{aid}/comment")
def comment_answer(qid: str, aid: str, c: CommentCreate, email=Depends(get_current_user_email)):
    comment = {
        "_id": ObjectId(),
        "text": c.text,
        "author_email": email,
        "likes": 0,
        "dislikes": 0,
        "created_at": datetime.now(timezone.utc),
    }

    questions_col.update_one(
        {"_id": ObjectId(qid), "answers._id": ObjectId(aid)},
        {"$push": {"answers.$.comments": comment}}
    )

    return {"msg": "Comment added"}


# ─────────── ACCEPT ANSWER ───────────
@router.post("/questions/{qid}/accept/{aid}")
def accept_answer(qid: str, aid: str, email=Depends(get_current_user_email)):
    q = questions_col.find_one({"_id": ObjectId(qid)})

    if q["author_email"] != email:
        raise HTTPException(403, "Only owner can accept answer")

    questions_col.update_one(
        {"_id": ObjectId(qid)},
        {"$set": {"accepted_answer_id": ObjectId(aid)}}
    )

    return {"msg": "Answer accepted"}

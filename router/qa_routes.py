from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pymongo import MongoClient
from pydantic import BaseModel, Field, BeforeValidator
from typing import List, Optional, Annotated
from datetime import datetime
from bson import ObjectId
import os
from dotenv import load_dotenv
from jose import jwt, JWTError

load_dotenv()

router = APIRouter(prefix="/api", tags=["Q&A"])

mongo_uri = os.getenv("MONGODB_URI")
SECRET = os.getenv("NEXTAUTH_SECRET")
ALGORITHM = "HS256"

if not SECRET or not SECRET.strip():
    import sys
    print("ERROR: NEXTAUTH_SECRET environment variable is not set or is empty!", file=sys.stderr)
    print("Please set NEXTAUTH_SECRET in your .env file or environment variables.", file=sys.stderr)
    raise ValueError("NEXTAUTH_SECRET environment variable is required. Please set it in your .env file.")

client = MongoClient(mongo_uri)
db = client["mentora"]
questions_col = db["questions"]
answers_col = db["answers"]
comments_col = db["comments"]
users_col = db["appUsers"]

PyObjectId = Annotated[str, BeforeValidator(str)]

# ==========================================
# AUTH HELPERS
# ==========================================

def get_current_user_email(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    
    token = parts[1]
    
    if not SECRET or not SECRET.strip():
        raise HTTPException(
            status_code=500, 
            detail="Server configuration error: NEXTAUTH_SECRET not set. Please configure NEXTAUTH_SECRET in your .env file."
        )
    
    try:
        payload = jwt.decode(token, SECRET.strip(), algorithms=[ALGORITHM], options={"verify_aud": False})
        email = payload.get("email") or payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")
        return email
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {str(e)}")

# ==========================================
# MODELS
# ==========================================

class QuestionCreate(BaseModel):
    title: str = Field(..., max_length=200)
    description: str  # HTML content
    tags: str = Field(default="", max_length=500)  # Comma-separated tags

class QuestionResponse(BaseModel):
    id: str = Field(alias="_id")
    title: str
    description: str
    tags: str
    authorId: str
    upvotes: int
    downvotes: int
    createdAt: datetime
    answerCount: int = 0
    userVote: Optional[str] = None  # "upvote", "downvote", or None
    
    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat(), ObjectId: str}
        
    @classmethod
    def from_mongo(cls, data: dict):
        """Helper to convert MongoDB document to response"""
        if "_id" in data:
            data["_id"] = str(data["_id"])
        return cls(**data)

class AnswerCreate(BaseModel):
    questionId: str
    content: str  # HTML content

class AnswerResponse(BaseModel):
    id: str = Field(alias="_id")
    questionId: str
    authorId: str
    content: str
    usefulCount: int
    notUsefulCount: int
    accepted: bool
    createdAt: datetime
    userVote: Optional[str] = None  # "useful", "notUseful", or None
    
    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat(), ObjectId: str}

class CommentCreate(BaseModel):
    parentType: str  # "question" or "answer"
    parentId: str
    content: str  # HTML content

class CommentResponse(BaseModel):
    id: str = Field(alias="_id")
    parentType: str
    parentId: str
    authorId: str
    content: str
    likes: int
    dislikes: int
    createdAt: datetime
    userVote: Optional[str] = None  # "like", "dislike", or None
    
    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat(), ObjectId: str}

class VoteRequest(BaseModel):
    voteType: str  # "upvote" or "downvote"

# ==========================================
# QUESTION ROUTES
# ==========================================

@router.post("/questions", status_code=201)
def create_question(
    question: QuestionCreate,
    user_email: str = Depends(get_current_user_email)
):
    """Create a new question"""
    new_question = {
        "title": question.title,
        "description": question.description,
        "tags": question.tags,
        "authorId": user_email,
        "upvotes": 0,
        "downvotes": 0,
        "createdAt": datetime.now()
    }
    result = questions_col.insert_one(new_question)
    
    # Add 50 points for asking question
    from services.contribution_service import ContributionService
    ContributionService.add_points(user_email, 50, "Asked question")
    
    # Check badges
    from services.badge_service import BadgeService
    BadgeService.check_and_assign_badges(user_email)
    
    question_doc = questions_col.find_one({"_id": result.inserted_id})
    question_doc["_id"] = str(question_doc["_id"])
    return QuestionResponse(**question_doc)

@router.get("/questions")
def get_questions(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    search: Optional[str] = None,
    tag: Optional[str] = None
):
    """Get all questions with pagination"""
    skip = (page - 1) * limit
    query = {}
    
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
            {"tags": {"$regex": search, "$options": "i"}}
        ]
    
    if tag:
        query["tags"] = {"$regex": tag, "$options": "i"}
    
    total = questions_col.count_documents(query)
    questions = list(questions_col.find(query).sort("createdAt", -1).skip(skip).limit(limit))
    
    # Add answer count for each question and ensure _id is converted to string
    result_questions = []
    for q in questions:
        answer_count = answers_col.count_documents({"questionId": str(q["_id"])})
        q["answerCount"] = answer_count
        # Convert ObjectId to string
        q["_id"] = str(q["_id"])
        result_questions.append(QuestionResponse(**q))
    
    return {
        "questions": result_questions,
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": (total + limit - 1) // limit
    }

@router.get("/questions/{question_id}")
def get_question(question_id: str, authorization: Optional[str] = Header(None)):
    """Get a single question with answers"""
    if not question_id or question_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid question ID")
    
    if not ObjectId.is_valid(question_id):
        raise HTTPException(status_code=400, detail="Invalid question ID format")
    
    question = questions_col.find_one({"_id": ObjectId(question_id)})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Get user vote status if authenticated
    user_vote = None
    if authorization:
        try:
            parts = authorization.split(" ")
            if len(parts) == 2 and parts[0] == "Bearer":
                token = parts[1]
                if SECRET:
                    payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM], options={"verify_aud": False})
                    user_email = payload.get("email") or payload.get("sub")
                    if user_email:
                        votes = question.get("votes", {})
                        user_vote = votes.get(user_email)
        except (JWTError, Exception):
            pass  # If token invalid, just continue without user vote
    
    # Get all answers for this question
    answers = list(answers_col.find({"questionId": question_id}).sort("createdAt", 1))
    
    # Ensure _id is converted to string for proper serialization
    question["_id"] = str(question["_id"])
    question["userVote"] = user_vote
    
    # Get user vote status for answers if authenticated
    result_answers = []
    for a in answers:
        answer_id_str = str(a["_id"])
        a["_id"] = answer_id_str
        a["id"] = answer_id_str  # Explicitly set id field for frontend
        answer_user_vote = None
        if authorization:
            try:
                parts = authorization.split(" ")
                if len(parts) == 2 and parts[0] == "Bearer":
                    token = parts[1]
                    if SECRET:
                        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM], options={"verify_aud": False})
                        user_email = payload.get("email") or payload.get("sub")
                        if user_email:
                            votes = a.get("votes", {})
                            answer_user_vote = votes.get(user_email)
            except (JWTError, Exception):
                pass
        a["userVote"] = answer_user_vote
        result_answers.append(AnswerResponse(**a))
    
    return {
        "question": QuestionResponse(**question),
        "answers": result_answers
    }

@router.post("/questions/{question_id}/vote")
def vote_question(
    question_id: str,
    vote: VoteRequest,
    user_email: str = Depends(get_current_user_email)
):
    """Vote on a question (upvote or downvote) - like/unlike system"""
    if not question_id or question_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid question ID")
    
    if not ObjectId.is_valid(question_id):
        raise HTTPException(status_code=400, detail="Invalid question ID format")
    
    question = questions_col.find_one({"_id": ObjectId(question_id)})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Initialize votes tracking if not exists
    if "votes" not in question:
        question["votes"] = {}
    
    votes = question.get("votes", {})
    current_vote = votes.get(user_email)  # "upvote", "downvote", or None
    
    # Like/Unlike logic
    if vote.voteType == "upvote":
        if current_vote == "upvote":
            # Toggle: remove upvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"upvotes": -1},
                    "$unset": {f"votes.{user_email}": ""}
                }
            )
        elif current_vote == "downvote":
            # Switch from downvote to upvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"upvotes": 1, "downvotes": -1},
                    "$set": {f"votes.{user_email}": "upvote"}
                }
            )
        else:
            # New upvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"upvotes": 1},
                    "$set": {f"votes.{user_email}": "upvote"}
                }
            )
    elif vote.voteType == "downvote":
        if current_vote == "downvote":
            # Toggle: remove downvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"downvotes": -1},
                    "$unset": {f"votes.{user_email}": ""}
                }
            )
        elif current_vote == "upvote":
            # Switch from upvote to downvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"upvotes": -1, "downvotes": 1},
                    "$set": {f"votes.{user_email}": "downvote"}
                }
            )
        else:
            # New downvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"downvotes": 1},
                    "$set": {f"votes.{user_email}": "downvote"}
                }
            )
    else:
        raise HTTPException(status_code=400, detail="Invalid vote type")
    
    updated = questions_col.find_one({"_id": ObjectId(question_id)})
    if updated:
        updated["_id"] = str(updated["_id"])
        updated["userVote"] = updated.get("votes", {}).get(user_email)
        return QuestionResponse(**updated)
    raise HTTPException(status_code=404, detail="Question not found after update")

# ==========================================
# ANSWER ROUTES
# ==========================================

@router.post("/answers", status_code=201)
def create_answer(
    answer: AnswerCreate,
    user_email: str = Depends(get_current_user_email)
):
    """Create a new answer"""
    if not ObjectId.is_valid(answer.questionId):
        raise HTTPException(status_code=400, detail="Invalid question ID")
    
    question = questions_col.find_one({"_id": ObjectId(answer.questionId)})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    new_answer = {
        "questionId": answer.questionId,
        "authorId": user_email,
        "content": answer.content,
        "usefulCount": 0,
        "notUsefulCount": 0,
        "accepted": False,
        "createdAt": datetime.now()
    }
    result = answers_col.insert_one(new_answer)
    
    # Add 50 points for answering
    from services.contribution_service import ContributionService
    ContributionService.add_points(user_email, 50, "Answered question")
    
    # Check badges
    from services.badge_service import BadgeService
    BadgeService.check_and_assign_badges(user_email)
    
    answer_doc = answers_col.find_one({"_id": result.inserted_id})
    answer_id_str = str(answer_doc["_id"])
    answer_doc["_id"] = answer_id_str
    answer_doc["id"] = answer_id_str  # Explicitly set id field for frontend
    answer_doc["userVote"] = None
    return AnswerResponse(**answer_doc)

@router.post("/answers/{answer_id}/accept")
def accept_answer(
    answer_id: str,
    user_email: str = Depends(get_current_user_email)
):
    """Accept an answer (only question owner can accept)"""
    if not answer_id or answer_id == "undefined" or answer_id == "null":
        raise HTTPException(status_code=400, detail="Invalid answer ID: ID is missing or undefined")
    
    if not ObjectId.is_valid(answer_id):
        raise HTTPException(status_code=400, detail=f"Invalid answer ID format: {answer_id}")
    
    answer = answers_col.find_one({"_id": ObjectId(answer_id)})
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    
    # Ensure questionId is valid
    if not answer.get("questionId"):
        raise HTTPException(status_code=400, detail="Answer missing questionId")
    
    if not ObjectId.is_valid(answer["questionId"]):
        raise HTTPException(status_code=400, detail="Invalid questionId in answer")
    
    question = questions_col.find_one({"_id": ObjectId(answer["questionId"])})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Only question owner can accept answers
    if question["authorId"] != user_email:
        raise HTTPException(status_code=403, detail="Only question owner can accept answers")
    
    # Mark answer as accepted (multiple accepted answers allowed)
    answers_col.update_one(
        {"_id": ObjectId(answer_id)},
        {"$set": {"accepted": True}}
    )
    
    # Add 100 points to answer author
    from services.contribution_service import ContributionService
    ContributionService.add_points(answer["authorId"], 100, "Answer accepted")
    ContributionService.increment_field(answer["authorId"], "acceptedAnswersCount", 1)
    
    # Check badges
    from services.badge_service import BadgeService
    BadgeService.check_and_assign_badges(answer["authorId"])
    
    updated = answers_col.find_one({"_id": ObjectId(answer_id)})
    if not updated:
        raise HTTPException(status_code=404, detail="Answer not found after update")
    answer_id_str = str(updated["_id"])
    updated["_id"] = answer_id_str
    updated["id"] = answer_id_str  # Explicitly set id field for frontend
    # Get user vote status for the current user
    updated["userVote"] = updated.get("votes", {}).get(user_email)
    return AnswerResponse(**updated)

@router.post("/answers/{answer_id}/useful")
def mark_answer_useful(
    answer_id: str,
    vote: VoteRequest,
    user_email: str = Depends(get_current_user_email)
):
    """Mark answer as useful or not useful - like/unlike system"""
    if not answer_id or answer_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid answer ID")
    
    if not ObjectId.is_valid(answer_id):
        raise HTTPException(status_code=400, detail="Invalid answer ID format")
    
    answer = answers_col.find_one({"_id": ObjectId(answer_id)})
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    
    # Initialize votes tracking if not exists
    if "votes" not in answer:
        answer["votes"] = {}
    
    votes = answer.get("votes", {})
    current_vote = votes.get(user_email)  # "useful", "notUseful", or None
    
    # Like/Unlike logic
    if vote.voteType == "useful":
        if current_vote == "useful":
            # Toggle: remove useful vote
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"usefulCount": -1},
                    "$unset": {f"votes.{user_email}": ""}
                }
            )
        elif current_vote == "notUseful":
            # Switch from notUseful to useful
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"usefulCount": 1, "notUsefulCount": -1},
                    "$set": {f"votes.{user_email}": "useful"}
                }
            )
        else:
            # New useful vote
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"usefulCount": 1},
                    "$set": {f"votes.{user_email}": "useful"}
                }
            )
    elif vote.voteType == "notUseful":
        if current_vote == "notUseful":
            # Toggle: remove notUseful vote
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"notUsefulCount": -1},
                    "$unset": {f"votes.{user_email}": ""}
                }
            )
        elif current_vote == "useful":
            # Switch from useful to notUseful
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"usefulCount": -1, "notUsefulCount": 1},
                    "$set": {f"votes.{user_email}": "notUseful"}
                }
            )
        else:
            # New notUseful vote
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"notUsefulCount": 1},
                    "$set": {f"votes.{user_email}": "notUseful"}
                }
            )
    else:
        raise HTTPException(status_code=400, detail="Invalid vote type")
    
    updated = answers_col.find_one({"_id": ObjectId(answer_id)})
    if updated:
        answer_id_str = str(updated["_id"])
        updated["_id"] = answer_id_str
        updated["id"] = answer_id_str  # Explicitly set id field for frontend
        updated["userVote"] = updated.get("votes", {}).get(user_email)
        return AnswerResponse(**updated)
    raise HTTPException(status_code=404, detail="Answer not found after update")

# ==========================================
# COMMENT ROUTES
# ==========================================

@router.post("/comments", status_code=201)
def create_comment(
    comment: CommentCreate,
    user_email: str = Depends(get_current_user_email)
):
    """Create a comment on a question or answer"""
    if comment.parentType not in ["question", "answer"]:
        raise HTTPException(status_code=400, detail="parentType must be 'question' or 'answer'")
    
    # Verify parent exists
    if comment.parentType == "question":
        if not ObjectId.is_valid(comment.parentId):
            raise HTTPException(status_code=400, detail="Invalid question ID")
        parent = questions_col.find_one({"_id": ObjectId(comment.parentId)})
    else:
        if not ObjectId.is_valid(comment.parentId):
            raise HTTPException(status_code=400, detail="Invalid answer ID")
        parent = answers_col.find_one({"_id": ObjectId(comment.parentId)})
    
    if not parent:
        raise HTTPException(status_code=404, detail="Parent not found")
    
    new_comment = {
        "parentType": comment.parentType,
        "parentId": comment.parentId,
        "authorId": user_email,
        "content": comment.content,
        "likes": 0,
        "dislikes": 0,
        "createdAt": datetime.now()
    }
    result = comments_col.insert_one(new_comment)
    
    comment_doc = comments_col.find_one({"_id": result.inserted_id})
    if comment_doc:
        comment_doc["_id"] = str(comment_doc["_id"])
        comment_doc["userVote"] = None
        return CommentResponse(**comment_doc)
    raise HTTPException(status_code=500, detail="Failed to create comment")

@router.get("/comments")
def get_comments(
    parentType: str = Query(..., description="'question' or 'answer'"),
    parentId: str = Query(..., description="ID of parent question or answer"),
    authorization: Optional[str] = Header(None)
):
    """Get comments for a question or answer"""
    comments = list(comments_col.find({
        "parentType": parentType,
        "parentId": parentId
    }).sort("createdAt", 1))
    
    # Get user vote status for comments if authenticated
    user_email = None
    if authorization:
        try:
            parts = authorization.split(" ")
            if len(parts) == 2 and parts[0] == "Bearer":
                token = parts[1]
                if SECRET:
                    payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM], options={"verify_aud": False})
                    user_email = payload.get("email") or payload.get("sub")
        except (JWTError, Exception):
            pass
    
    result_comments = []
    for c in comments:
        c["_id"] = str(c["_id"])
        comment_user_vote = None
        if user_email:
            votes = c.get("votes", {})
            comment_user_vote = votes.get(user_email)
        c["userVote"] = comment_user_vote
        result_comments.append(CommentResponse(**c))
    
    return result_comments

@router.post("/comments/{comment_id}/like")
def like_comment(
    comment_id: str,
    user_email: str = Depends(get_current_user_email)
):
    """Like a comment - like/unlike system"""
    if not comment_id or comment_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid comment ID")
    
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(status_code=400, detail="Invalid comment ID format")
    
    comment = comments_col.find_one({"_id": ObjectId(comment_id)})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Initialize votes tracking if not exists
    if "votes" not in comment:
        comment["votes"] = {}
    
    votes = comment.get("votes", {})
    current_vote = votes.get(user_email)  # "like", "dislike", or None
    
    if current_vote == "like":
        # Toggle: remove like
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"likes": -1},
                "$unset": {f"votes.{user_email}": ""}
            }
        )
    elif current_vote == "dislike":
        # Switch from dislike to like
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"likes": 1, "dislikes": -1},
                "$set": {f"votes.{user_email}": "like"}
            }
        )
    else:
        # New like
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"likes": 1},
                "$set": {f"votes.{user_email}": "like"}
            }
        )
    
    updated = comments_col.find_one({"_id": ObjectId(comment_id)})
    updated["_id"] = str(updated["_id"])
    updated["userVote"] = updated.get("votes", {}).get(user_email)
    return CommentResponse(**updated)

@router.post("/comments/{comment_id}/dislike")
def dislike_comment(
    comment_id: str,
    user_email: str = Depends(get_current_user_email)
):
    """Dislike a comment - like/unlike system"""
    if not comment_id or comment_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid comment ID")
    
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(status_code=400, detail="Invalid comment ID format")
    
    comment = comments_col.find_one({"_id": ObjectId(comment_id)})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Initialize votes tracking if not exists
    if "votes" not in comment:
        comment["votes"] = {}
    
    votes = comment.get("votes", {})
    current_vote = votes.get(user_email)  # "like", "dislike", or None
    
    if current_vote == "dislike":
        # Toggle: remove dislike
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"dislikes": -1},
                "$unset": {f"votes.{user_email}": ""}
            }
        )
    elif current_vote == "like":
        # Switch from like to dislike
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"likes": -1, "dislikes": 1},
                "$set": {f"votes.{user_email}": "dislike"}
            }
        )
    else:
        # New dislike
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"dislikes": 1},
                "$set": {f"votes.{user_email}": "dislike"}
            }
        )
    
    updated = comments_col.find_one({"_id": ObjectId(comment_id)})
    updated["_id"] = str(updated["_id"])
    updated["userVote"] = updated.get("votes", {}).get(user_email)
    return CommentResponse(**updated)

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
        "upvotedBy": [],
        "downvotedBy": [],
        "createdAt": datetime.now()
    }
    result = questions_col.insert_one(new_question)
    
    # Add 50 points for asking question and increment question count
    from services.contribution_service import ContributionService
    ContributionService.add_points(user_email, 50, "Asked question")
    ContributionService.increment_field(user_email, "askQuestionCount", 1)
    
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
                        upvoted_by = question.get("upvotedBy", [])
                        downvoted_by = question.get("downvotedBy", [])
                        if user_email in upvoted_by:
                            user_vote = "upvote"
                        elif user_email in downvoted_by:
                            user_vote = "downvote"
                        else:
                            user_vote = None
        except (JWTError, Exception):
            pass  
    
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
        a["id"] = answer_id_str  
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
                            useful_by = a.get("usefulBy", [])
                            not_useful_by = a.get("notUsefulBy", [])
                            if user_email in useful_by:
                                answer_user_vote = "useful"
                            elif user_email in not_useful_by:
                                answer_user_vote = "notUseful"
                            else:
                                answer_user_vote = None
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
    """Vote on a question (upvote or downvote) - one vote per user using arrays"""
    if not question_id or question_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid question ID")
    
    if not ObjectId.is_valid(question_id):
        raise HTTPException(status_code=400, detail="Invalid question ID format")
    
    question = questions_col.find_one({"_id": ObjectId(question_id)})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Initialize arrays if not exists
    upvoted_by = question.get("upvotedBy", [])
    downvoted_by = question.get("downvotedBy", [])
    
    is_upvoted = user_email in upvoted_by
    is_downvoted = user_email in downvoted_by
    
   
    if vote.voteType == "upvote":
        if is_upvoted:
            # Toggle: remove upvote 
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"upvotes": -1},
                    "$pull": {"upvotedBy": user_email}
                }
            )
        elif is_downvoted:
            # Switch from downvote to upvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"upvotes": 1, "downvotes": -1},
                    "$pull": {"downvotedBy": user_email},
                    "$addToSet": {"upvotedBy": user_email}
                }
            )
        else:
            # New upvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"upvotes": 1},
                    "$addToSet": {"upvotedBy": user_email}
                }
            )
    elif vote.voteType == "downvote":
        if is_downvoted:
            # Toggle: remove downvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"downvotes": -1},
                    "$pull": {"downvotedBy": user_email}
                }
            )
        elif is_upvoted:
            # Switch from upvote to downvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"upvotes": -1, "downvotes": 1},
                    "$pull": {"upvotedBy": user_email},
                    "$addToSet": {"downvotedBy": user_email}
                }
            )
        else:
            # New downvote
            questions_col.update_one(
                {"_id": ObjectId(question_id)},
                {
                    "$inc": {"downvotes": 1},
                    "$addToSet": {"downvotedBy": user_email}
                }
            )
    else:
        raise HTTPException(status_code=400, detail="Invalid vote type")
    
    updated = questions_col.find_one({"_id": ObjectId(question_id)})
    if updated:
        updated["_id"] = str(updated["_id"])
        upvoted_by_updated = updated.get("upvotedBy", [])
        downvoted_by_updated = updated.get("downvotedBy", [])
        if user_email in upvoted_by_updated:
            updated["userVote"] = "upvote"
        elif user_email in downvoted_by_updated:
            updated["userVote"] = "downvote"
        else:
            updated["userVote"] = None
        return QuestionResponse(**updated)
    raise HTTPException(status_code=404, detail="Question not found after update")


# ANSWER ROUTES

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
        "usefulBy": [],
        "notUsefulBy": [],
        "accepted": False,
        "createdAt": datetime.now()
    }
    result = answers_col.insert_one(new_answer)
    
    # Add 50 points for answering and increment answer count
    from services.contribution_service import ContributionService
    ContributionService.add_points(user_email, 50, "Answered question")
    ContributionService.increment_field(user_email, "answerQuestionCount", 1)
    
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
    useful_by_updated = updated.get("usefulBy", [])
    not_useful_by_updated = updated.get("notUsefulBy", [])
    if user_email in useful_by_updated:
        updated["userVote"] = "useful"
    elif user_email in not_useful_by_updated:
        updated["userVote"] = "notUseful"
    else:
        updated["userVote"] = None
    return AnswerResponse(**updated)

@router.post("/answers/{answer_id}/useful")
def mark_answer_useful(
    answer_id: str,
    vote: VoteRequest,
    user_email: str = Depends(get_current_user_email)
):
    """Mark answer as useful or not useful - one vote per user using arrays"""
    if not answer_id or answer_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid answer ID")
    
    if not ObjectId.is_valid(answer_id):
        raise HTTPException(status_code=400, detail="Invalid answer ID format")
    
    answer = answers_col.find_one({"_id": ObjectId(answer_id)})
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    
    # Initialize arrays if not exists
    useful_by = answer.get("usefulBy", [])
    not_useful_by = answer.get("notUsefulBy", [])
    
    is_useful = user_email in useful_by
    is_not_useful = user_email in not_useful_by
    
    # Like/Unlike logic using arrays
    if vote.voteType == "useful":
        if is_useful:
            # Toggle: remove useful vote
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"usefulCount": -1},
                    "$pull": {"usefulBy": user_email}
                }
            )
        elif is_not_useful:
            # Switch from notUseful to useful
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"usefulCount": 1, "notUsefulCount": -1},
                    "$pull": {"notUsefulBy": user_email},
                    "$addToSet": {"usefulBy": user_email}
                }
            )
        else:
            # New useful vote
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"usefulCount": 1},
                    "$addToSet": {"usefulBy": user_email}
                }
            )
    elif vote.voteType == "notUseful":
        if is_not_useful:
            # Toggle: remove notUseful vote
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"notUsefulCount": -1},
                    "$pull": {"notUsefulBy": user_email}
                }
            )
        elif is_useful:
            # Switch from useful to notUseful
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"usefulCount": -1, "notUsefulCount": 1},
                    "$pull": {"usefulBy": user_email},
                    "$addToSet": {"notUsefulBy": user_email}
                }
            )
        else:
            # New notUseful vote
            answers_col.update_one(
                {"_id": ObjectId(answer_id)},
                {
                    "$inc": {"notUsefulCount": 1},
                    "$addToSet": {"notUsefulBy": user_email}
                }
            )
    else:
        raise HTTPException(status_code=400, detail="Invalid vote type")
    
    updated = answers_col.find_one({"_id": ObjectId(answer_id)})
    if updated:
        answer_id_str = str(updated["_id"])
        updated["_id"] = answer_id_str
        updated["id"] = answer_id_str 
        useful_by_updated = updated.get("usefulBy", [])
        not_useful_by_updated = updated.get("notUsefulBy", [])
        if user_email in useful_by_updated:
            updated["userVote"] = "useful"
        elif user_email in not_useful_by_updated:
            updated["userVote"] = "notUseful"
        else:
            updated["userVote"] = None
        return AnswerResponse(**updated)
    raise HTTPException(status_code=404, detail="Answer not found after update")


# COMMENT ROUTES

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
        "likedBy": [],
        "dislikedBy": [],
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
    # Validate parentId
    if not parentId or parentId == "undefined" or parentId == "null":
        return []
    
    # Validate parentType
    if parentType not in ["question", "answer"]:
        raise HTTPException(status_code=400, detail="parentType must be 'question' or 'answer'")
    
    # Validate ObjectId format if it's a MongoDB ObjectId
    if parentType == "question" or parentType == "answer":
        if not ObjectId.is_valid(parentId):
            return []
    
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
            liked_by = c.get("likedBy", [])
            disliked_by = c.get("dislikedBy", [])
            if user_email in liked_by:
                comment_user_vote = "like"
            elif user_email in disliked_by:
                comment_user_vote = "dislike"
            else:
                comment_user_vote = None
        c["userVote"] = comment_user_vote
        result_comments.append(CommentResponse(**c))
    
    return result_comments

@router.post("/comments/{comment_id}/like")
def like_comment(
    comment_id: str,
    user_email: str = Depends(get_current_user_email)
):
    """Like a comment - one vote per user using arrays"""
    if not comment_id or comment_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid comment ID")
    
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(status_code=400, detail="Invalid comment ID format")
    
    comment = comments_col.find_one({"_id": ObjectId(comment_id)})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Initialize 
    liked_by = comment.get("likedBy", [])
    disliked_by = comment.get("dislikedBy", [])
    
    is_liked = user_email in liked_by
    is_disliked = user_email in disliked_by
    
    if is_liked:
        # Toggle: remove like
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"likes": -1},
                "$pull": {"likedBy": user_email}
            }
        )
    elif is_disliked:
        # Switch from dislike to like
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"likes": 1, "dislikes": -1},
                "$pull": {"dislikedBy": user_email},
                "$addToSet": {"likedBy": user_email}
            }
        )
    else:
        # New like
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"likes": 1},
                "$addToSet": {"likedBy": user_email}
            }
        )
    
    updated = comments_col.find_one({"_id": ObjectId(comment_id)})
    updated["_id"] = str(updated["_id"])
    liked_by_updated = updated.get("likedBy", [])
    disliked_by_updated = updated.get("dislikedBy", [])
    if user_email in liked_by_updated:
        updated["userVote"] = "like"
    elif user_email in disliked_by_updated:
        updated["userVote"] = "dislike"
    else:
        updated["userVote"] = None
    return CommentResponse(**updated)

@router.post("/comments/{comment_id}/dislike")
def dislike_comment(
    comment_id: str,
    user_email: str = Depends(get_current_user_email)
):
    """Dislike a comment - one vote per user using arrays"""
    if not comment_id or comment_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid comment ID")
    
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(status_code=400, detail="Invalid comment ID format")
    
    comment = comments_col.find_one({"_id": ObjectId(comment_id)})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Initialize 
    liked_by = comment.get("likedBy", [])
    disliked_by = comment.get("dislikedBy", [])
    
    is_liked = user_email in liked_by
    is_disliked = user_email in disliked_by
    
    if is_disliked:
        # Toggle: remove dislike
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"dislikes": -1},
                "$pull": {"dislikedBy": user_email}
            }
        )
    elif is_liked:
        # Switch from like to dislike
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"likes": -1, "dislikes": 1},
                "$pull": {"likedBy": user_email},
                "$addToSet": {"dislikedBy": user_email}
            }
        )
    else:
        # New dislike
        comments_col.update_one(
            {"_id": ObjectId(comment_id)},
            {
                "$inc": {"dislikes": 1},
                "$addToSet": {"dislikedBy": user_email}
            }
        )
    
    updated = comments_col.find_one({"_id": ObjectId(comment_id)})
    updated["_id"] = str(updated["_id"])
    liked_by_updated = updated.get("likedBy", [])
    disliked_by_updated = updated.get("dislikedBy", [])
    if user_email in liked_by_updated:
        updated["userVote"] = "like"
    elif user_email in disliked_by_updated:
        updated["userVote"] = "dislike"
    else:
        updated["userVote"] = None
    return CommentResponse(**updated)


# EDIT / DELETE ROUTES (OWNER ONLY)

class QuestionUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    tags: Optional[str] = Field(None, max_length=500)

@router.put("/questions/{question_id}")
def update_question(
    question_id: str,
    question_update: QuestionUpdate,
    user_email: str = Depends(get_current_user_email)
):
    """Update a question (owner only)"""
    if not question_id or question_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid question ID")
    
    if not ObjectId.is_valid(question_id):
        raise HTTPException(status_code=400, detail="Invalid question ID format")
    
    question = questions_col.find_one({"_id": ObjectId(question_id)})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Owner-only check
    if question["authorId"] != user_email:
        raise HTTPException(status_code=403, detail="Only question owner can edit")
    
    update_data = {}
    if question_update.title is not None:
        update_data["title"] = question_update.title
    if question_update.description is not None:
        update_data["description"] = question_update.description
    if question_update.tags is not None:
        update_data["tags"] = question_update.tags
    
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    questions_col.update_one(
        {"_id": ObjectId(question_id)},
        {"$set": update_data}
    )
    
    updated = questions_col.find_one({"_id": ObjectId(question_id)})
    updated["_id"] = str(updated["_id"])
    upvoted_by = updated.get("upvotedBy", [])
    downvoted_by = updated.get("downvotedBy", [])
    if user_email in upvoted_by:
        updated["userVote"] = "upvote"
    elif user_email in downvoted_by:
        updated["userVote"] = "downvote"
    else:
        updated["userVote"] = None
    return QuestionResponse(**updated)

@router.delete("/questions/{question_id}")
def delete_question(
    question_id: str,
    user_email: str = Depends(get_current_user_email)
):
    """Delete a question (owner only)"""
    if not question_id or question_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid question ID")
    
    if not ObjectId.is_valid(question_id):
        raise HTTPException(status_code=400, detail="Invalid question ID format")
    
    question = questions_col.find_one({"_id": ObjectId(question_id)})
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Owner-only check
    if question["authorId"] != user_email:
        raise HTTPException(status_code=403, detail="Only question owner can delete")
    
    # Delete associated answers and comments
    answers_col.delete_many({"questionId": question_id})
    comments_col.delete_many({"parentType": "question", "parentId": question_id})
    
    questions_col.delete_one({"_id": ObjectId(question_id)})
    return {"message": "Question deleted successfully"}

class AnswerUpdate(BaseModel):
    content: Optional[str] = None

@router.put("/answers/{answer_id}")
def update_answer(
    answer_id: str,
    answer_update: AnswerUpdate,
    user_email: str = Depends(get_current_user_email)
):
    """Update an answer (owner only)"""
    if not answer_id or answer_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid answer ID")
    
    if not ObjectId.is_valid(answer_id):
        raise HTTPException(status_code=400, detail="Invalid answer ID format")
    
    answer = answers_col.find_one({"_id": ObjectId(answer_id)})
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    
    # Owner-only check
    if answer["authorId"] != user_email:
        raise HTTPException(status_code=403, detail="Only answer owner can edit")
    
    if answer_update.content is None:
        raise HTTPException(status_code=400, detail="Content is required")
    
    answers_col.update_one(
        {"_id": ObjectId(answer_id)},
        {"$set": {"content": answer_update.content}}
    )
    
    updated = answers_col.find_one({"_id": ObjectId(answer_id)})
    answer_id_str = str(updated["_id"])
    updated["_id"] = answer_id_str
    updated["id"] = answer_id_str
    useful_by = updated.get("usefulBy", [])
    not_useful_by = updated.get("notUsefulBy", [])
    if user_email in useful_by:
        updated["userVote"] = "useful"
    elif user_email in not_useful_by:
        updated["userVote"] = "notUseful"
    else:
        updated["userVote"] = None
    return AnswerResponse(**updated)

@router.delete("/answers/{answer_id}")
def delete_answer(
    answer_id: str,
    user_email: str = Depends(get_current_user_email)
):
    """Delete an answer (owner only)"""
    if not answer_id or answer_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid answer ID")
    
    if not ObjectId.is_valid(answer_id):
        raise HTTPException(status_code=400, detail="Invalid answer ID format")
    
    answer = answers_col.find_one({"_id": ObjectId(answer_id)})
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")
    
    # Owner-only check
    if answer["authorId"] != user_email:
        raise HTTPException(status_code=403, detail="Only answer owner can delete")
    
    # Delete associated comments
    comments_col.delete_many({"parentType": "answer", "parentId": answer_id})
    
    answers_col.delete_one({"_id": ObjectId(answer_id)})
    return {"message": "Answer deleted successfully"}

class CommentUpdate(BaseModel):
    content: Optional[str] = None

@router.put("/comments/{comment_id}")
def update_comment(
    comment_id: str,
    comment_update: CommentUpdate,
    user_email: str = Depends(get_current_user_email)
):
    """Update a comment (owner only)"""
    if not comment_id or comment_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid comment ID")
    
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(status_code=400, detail="Invalid comment ID format")
    
    comment = comments_col.find_one({"_id": ObjectId(comment_id)})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Owner-only check
    if comment["authorId"] != user_email:
        raise HTTPException(status_code=403, detail="Only comment owner can edit")
    
    if comment_update.content is None:
        raise HTTPException(status_code=400, detail="Content is required")
    
    comments_col.update_one(
        {"_id": ObjectId(comment_id)},
        {"$set": {"content": comment_update.content}}
    )
    
    updated = comments_col.find_one({"_id": ObjectId(comment_id)})
    updated["_id"] = str(updated["_id"])
    liked_by = updated.get("likedBy", [])
    disliked_by = updated.get("dislikedBy", [])
    if user_email in liked_by:
        updated["userVote"] = "like"
    elif user_email in disliked_by:
        updated["userVote"] = "dislike"
    else:
        updated["userVote"] = None
    return CommentResponse(**updated)

@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: str,
    user_email: str = Depends(get_current_user_email)
):
    """Delete a comment (owner only)"""
    if not comment_id or comment_id == "undefined":
        raise HTTPException(status_code=400, detail="Invalid comment ID")
    
    if not ObjectId.is_valid(comment_id):
        raise HTTPException(status_code=400, detail="Invalid comment ID format")
    
    comment = comments_col.find_one({"_id": ObjectId(comment_id)})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    if comment["authorId"] != user_email:
        raise HTTPException(status_code=403, detail="Only comment owner can delete")
    
    comments_col.delete_one({"_id": ObjectId(comment_id)})
    return {"message": "Comment deleted successfully"}

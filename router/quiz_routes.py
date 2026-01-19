from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from jose import jwt, JWTError
import os
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/api", tags=["Quiz"])

SECRET = os.getenv("NEXTAUTH_SECRET")
ALGORITHM = "HS256"

if not SECRET:
    raise ValueError("NEXTAUTH_SECRET environment variable is required")

def get_current_user_email(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    
    token = parts[1]
    
    if not SECRET:
        raise HTTPException(status_code=500, detail="Server configuration error: NEXTAUTH_SECRET not set")
    
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM], options={"verify_aud": False})
        email = payload.get("email") or payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")
        return email
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {str(e)}")

class QuizCompletionRequest(BaseModel):
    quizId: str
    score: int = 0
    totalQuestions: int = 0

@router.post("/quiz/complete")
def complete_quiz(
    completion: QuizCompletionRequest,
    user_email: str = Depends(get_current_user_email)
):
    """Mark a quiz as completed and award points"""
    # Add 50 points for completing quiz
    from services.contribution_service import ContributionService
    ContributionService.add_points(user_email, 50, "Completed quiz")
    ContributionService.increment_field(user_email, "completedQuizCount", 1)
    
    # Check badges
    from services.badge_service import BadgeService
    BadgeService.check_and_assign_badges(user_email)
    
    return {
        "message": "Quiz completed",
        "pointsAwarded": 50
    }

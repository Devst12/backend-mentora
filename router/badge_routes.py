from fastapi import APIRouter, HTTPException, Depends, Header
from pymongo import MongoClient
from pydantic import BaseModel
from typing import List
import os
from dotenv import load_dotenv
from jose import jwt, JWTError

load_dotenv()

router = APIRouter(prefix="/api", tags=["Badges"])

mongo_uri = os.getenv("MONGODB_URI")
SECRET = os.getenv("NEXTAUTH_SECRET")
ALGORITHM = "HS256"

if not SECRET:
    raise ValueError("NEXTAUTH_SECRET environment variable is required")

client = MongoClient(mongo_uri)
db = client["mentora"]
users_col = db["appUsers"]

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

@router.get("/badges")
def get_all_badges():
    """Get all available badges"""
    from services.badge_service import BadgeService
    return BadgeService.get_all_badges()

@router.get("/users/{user_id}/badges")
def get_user_badges(user_id: str):
    """Get badges for a specific user (by email)"""
    from services.badge_service import BadgeService
    return BadgeService.get_user_badges(user_id)

@router.get("/users/{user_id}/contributions")
def get_user_contributions(user_id: str):
    """Get contribution statistics for a user"""
    from services.contribution_service import ContributionService
    stats = ContributionService.get_user_stats(user_id)
    from services.badge_service import BadgeService
    badges = BadgeService.get_user_badges(user_id)
    
    return {
        **stats,
        "badges": badges
    }

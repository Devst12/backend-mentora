# backend/user_routes.py
import os
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel
from pymongo import MongoClient
from jose import jwt, JWTError
from dotenv import load_dotenv

# ── Load environment variables ──
load_dotenv()
mongo_uri = os.getenv("MONGODB_URI")
SECRET = os.getenv("NEXTAUTH_SECRET")
ALGORITHM = "HS256"

client = MongoClient(mongo_uri)
db = client.get_default_database()
users_collection = db["appUsers"]

# ── Router ──
router = APIRouter(tags=["Users"])

# ── Pydantic model ──
class SyncUserModel(BaseModel):
    name: str
    email: str
    image: str | None = None

# ── Sync user endpoint ──
@router.post("/sync-user")
async def sync_user(data: SyncUserModel):
    email = data.email
    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    try:
        result = users_collection.update_one(
            {"email": email},
            {
                "$set": {
                    "email": email,
                    "name": data.name,
                    "image": data.image,
                    "lastLogin": datetime.now(timezone.utc),
                },
                "$setOnInsert": {
                    "contributionPoints": 0,
                    "notesCount": 0,
                    "badgesCount": 0,
                    "createdAt": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )
        return {
            "matched": result.matched_count,
            "upserted_id": str(result.upserted_id) if result.upserted_id else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Helper function: verify JWT token ──
def get_current_user_email(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    token = parts[1]
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")
        return email
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {str(e)}")


# ── Get user stats endpoint ──
@router.get("/user-stats")
async def user_stats(email: str = None, current_user_email: str = Depends(get_current_user_email)):
    # Use query param if provided, else fallback to JWT email
    target_email = email or current_user_email
    user = users_collection.find_one({"email": target_email}, {"_id": 0})
    if not user:
        return {
            "contributionPoints": 0,
            "notesCount": 0,
            "badgesCount": 0,
        }
    return user

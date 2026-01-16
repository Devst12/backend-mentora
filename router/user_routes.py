# backend/user_routes.py
import os
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel
from pymongo import MongoClient
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()
mongo_uri = os.getenv("MONGODB_URI")
SECRET = os.getenv("NEXTAUTH_SECRET")
ALGORITHM = "HS256"

if not SECRET or not SECRET.strip():
    import sys
    print("ERROR: NEXTAUTH_SECRET environment variable is not set or is empty!", file=sys.stderr)
    print("Please set NEXTAUTH_SECRET in your .env file or environment variables.", file=sys.stderr)
    raise ValueError("NEXTAUTH_SECRET environment variable is required. Please set it in your .env file.")

client = MongoClient(mongo_uri)
db = client.get_default_database()
users_collection = db["appUsers"]
router = APIRouter(tags=["Users"])

class SyncUserModel(BaseModel):
    name: str
    email: str
    image: str | None = None

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
        # Decode the NextAuth JWT token
        payload = jwt.decode(token, SECRET.strip(), algorithms=[ALGORITHM], options={"verify_aud": False})
        
        # NextAuth tokens have email in different possible fields
        email = payload.get("email") or payload.get("sub")
        
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")
        
        return email
    except JWTError as e:
        print(f"JWT Error: {str(e)}")  # Debug log
        print(f"Token received: {token[:50]}...")  # Debug log (first 50 chars)
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {str(e)}")

@router.post("/sync-user")
async def sync_user(data: SyncUserModel):
    try:
        users_collection.update_one(
            {"email": data.email},
            {
                "$set": {
                    "name": data.name,
                    "email": data.email,
                    "image": data.image,
                    "lastLogin": datetime.now(timezone.utc),
                },
                "$setOnInsert": {
                    "contributionPoints": 0,
                    "notesCount": 0,
                    "badgesCount": 0,
                    "badges": [],
                    "acceptedAnswersCount": 0,
                    "uploadedPdfCount": 0,
                    "completedQuizCount": 0,
                    "createdAt": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/user-stats")
async def user_stats(current_user_email: str = Depends(get_current_user_email)):
    user = users_collection.find_one({"email": current_user_email}, {"_id": 0})
    if not user:
        return {"contributionPoints": 0, "notesCount": 0, "badgesCount": 0, "image": None}
    return {
        "contributionPoints": user.get("contributionPoints", 0),
        "notesCount": user.get("notesCount", 0),
        "badgesCount": user.get("badgesCount", 0),
        "image": user.get("image"),
    }
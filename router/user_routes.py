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

client = MongoClient(mongo_uri)
db = client.get_default_database()
users_collection = db["appUsers"]
router = APIRouter(tags=["Users"])

class SyncUserModel(BaseModel):
    name: str
    email: str
    image: str | None = None

# Helper to verify NextAuth JWT
def get_current_user_email(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(status_code=401, detail="Invalid format")
    token = parts[1]
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM], options={"verify_aud": False})
        email = payload.get("email") or payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Token missing email")
        return email
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

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

# --- FULL ADMIN ROUTE ---
@router.get("/admin/all-users")
async def get_all_users():
    """Returns a list of all users from the appUsers collection"""
    try:
        # Fetching all documents, removing _id for JSON serialization
        users = list(users_collection.find({}, {"_id": 0}))
        # Sort by points descending
        users.sort(key=lambda x: x.get("contributionPoints", 0), reverse=True)
        return users # This MUST return a list [] for frontend filter to work
    except Exception as e:
        print(f"Database error: {e}")
        return [] # Return empty list on error to prevent frontend crash
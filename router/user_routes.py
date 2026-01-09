# router/user_routes.py
import os
from fastapi import APIRouter, Request
from pymongo import MongoClient
from dotenv import load_dotenv

# Load env variables
current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, '.env'))

router = APIRouter()

# --- DATABASE SETUP ---
mongo_uri = os.getenv("MONGO_URI")

if not mongo_uri:
    print("❌ ERROR: MONGO_URI is missing!")
else:
    try:
        client = MongoClient(mongo_uri)
        db = client['mentora_db']
        users_collection = db['appUsers']
        print("✅ Connected to MongoDB! Saving data to database: 'mentora_db'")
    except Exception as e:
        print(f"❌ Connection Failed: {e}")


# ---------------- ROOT ----------------
@router.get("/")
def home():
    return {"message": "✅ Python Server is Running."}


# ---------------- SYNC USER ----------------
@router.post("/api/sync-user")
async def sync_user(request: Request):
    print("\n🔹 INCOMING SIGN-IN REQUEST 🔹")
    data = await request.json()
    print(f"📥 Received Payload: {data}")

    email = data.get("email")
    if not email:
        print("❌ Error: Email is missing from request")
        return {"error": "No email"}

    user_data = {
        "email": email,
        "name": data.get("name"),
        "image": data.get("image"),
    }

    try:
        result = users_collection.update_one(
            {"email": email},
            {"$set": user_data},
            upsert=True
        )

        if result.upserted_id:
            print(f"🎉 NEW USER CREATED in 'mentora_db': {email}")
        else:
            print(f"✅ USER UPDATED in 'mentora_db': {email}")

        return {"success": True}

    except Exception as e:
        print(f"❌ DB WRITE ERROR: {e}")
        return {"error": str(e)}


# ---------------- USER STATS ----------------
@router.get("/api/user-stats")
def get_user_stats(email: str):
    user = users_collection.find_one({"email": email}, {"_id": 0})
    return user if user else {}

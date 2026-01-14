from fastapi import APIRouter, HTTPException, Depends, Header, Query
from pymongo import MongoClient
from pydantic import BaseModel, Field, BeforeValidator
from typing import List, Optional, Annotated
from datetime import datetime
from bson import ObjectId
import os
from slugify import slugify
import math
from dotenv import load_dotenv

# 1. LOAD ENVIRONMENT VARIABLES
# This forces Python to look for your .env file
load_dotenv()

router = APIRouter()

# --- DATABASE SETUP (WITH DEBUGGING) ---
mongo_uri = os.getenv("MONGODB_URI")

# DEBUG PRINT 1: Check Connection String
if not mongo_uri:
    print("❌ CRITICAL ERROR: 'MONGODB_URI' not found in .env file! Using localhost.")
    mongo_uri = "mongodb://localhost:27017"
else:
    # Print first 20 chars only for security
    print(f"✅ DEBUG: Found Mongo URI: {mongo_uri[:20]}...") 

try:
    client = MongoClient(mongo_uri)
    # Force a connection check
    client.admin.command('ping')
    print("✅ DEBUG: Successfully connected to MongoDB Server.")
except Exception as e:
    print(f"❌ CRITICAL ERROR: Could not connect to MongoDB. Error: {e}")

# DEBUG PRINT 2: Check Database & Collection Names
db_name = "mentora"          # Must match your Compass DB name
col_name = "pdfuploads"      # Must match your Mongoose collection name

db = client[db_name]
uploads_col = db[col_name]
print(f"✅ DEBUG: Writing data to Database: '{db_name}' -> Collection: '{col_name}'")

# --- MODELS (VALIDATION) ---
PyObjectId = Annotated[str, BeforeValidator(str)]

class UploadSchema(BaseModel):
    title: str
    description: Optional[str] = ""
    pdfUrl: str
    tags: List[str] = []
    category: str = "Others"
    commentsEnabled: bool = True
    visibility: str = "Public"

class UploadResponse(UploadSchema):
    id: PyObjectId = Field(alias="_id")
    uploaderEmail: str
    slug: str
    createdAt: datetime

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat()}

# --- HELPER: AUTH ---
def get_current_user_email(x_user_email: str = Header(None)):
    if not x_user_email:
        raise HTTPException(status_code=401, detail="Unauthorized: No Email Header")
    return x_user_email

def generate_slug(title: str):
    unique_suffix = datetime.now().strftime("%f")
    return f"{slugify(title)}-{unique_suffix}"

# --- ROUTES ---

@router.get("/api/uploads")
def get_uploads(page: int = Query(1, ge=1), search: str = "", category: str = "All", user_email: str = Depends(get_current_user_email)):
    limit = 30
    skip = (page - 1) * limit
    query = {"uploaderEmail": user_email}
    
    if category != "All": query["category"] = category
    if search: 
        query["$or"] = [{"title": {"$regex": search, "$options": "i"}}, {"tags": {"$in": [search]}}]

    total = uploads_col.count_documents(query)
    cursor = uploads_col.find(query).sort("createdAt", -1).skip(skip).limit(limit)
    
    return {
        "uploads": [UploadResponse(**u) for u in cursor],
        "currentPage": page,
        "totalPages": max(1, math.ceil(total / limit))
    }

@router.post("/api/uploads", status_code=201)
def create_upload(upload: UploadSchema, user_email: str = Depends(get_current_user_email)):
    print(f"📥 DEBUG: Receiving Upload Request: {upload.title}")

    if len("".join(upload.tags)) > 500: raise HTTPException(400, "Tags too long")
    
    new_doc = upload.dict()
    new_doc.update({
        "uploaderEmail": user_email,
        "slug": generate_slug(upload.title),
        "createdAt": datetime.now(),
        "updatedAt": datetime.now()
    })
    
    try:
        res = uploads_col.insert_one(new_doc)
        print(f"💾 DEBUG: Saved to DB with ID: {res.inserted_id}")
        return UploadResponse(**uploads_col.find_one({"_id": res.inserted_id}))
    except Exception as e:
        print(f"❌ ERROR Saving to DB: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/api/uploads/{id}")
def update_upload(id: str, upload: UploadSchema, user_email: str = Depends(get_current_user_email)):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    
    existing = uploads_col.find_one({"_id": ObjectId(id)})
    if not existing or existing["uploaderEmail"] != user_email:
        raise HTTPException(403, "Forbidden")

    update_data = upload.dict()
    if upload.title != existing["title"]: update_data["slug"] = generate_slug(upload.title)
    update_data["updatedAt"] = datetime.now()

    uploads_col.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    return UploadResponse(**uploads_col.find_one({"_id": ObjectId(id)}))

@router.delete("/api/uploads/{id}")
def delete_upload(id: str, user_email: str = Depends(get_current_user_email)):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    res = uploads_col.delete_one({"_id": ObjectId(id), "uploaderEmail": user_email})
    if res.deleted_count == 0: raise HTTPException(404, "Not found or forbidden")
    return {"message": "Deleted"}
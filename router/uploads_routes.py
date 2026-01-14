from fastapi import APIRouter, HTTPException, Depends, Header, Query, UploadFile, File
from pymongo import MongoClient
from pydantic import BaseModel, Field, BeforeValidator
from typing import List, Optional, Annotated
from datetime import datetime
from bson import ObjectId
import os
import shutil
from pathlib import Path
from slugify import slugify
import math
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

# --- DATABASE SETUP ---
mongo_uri = os.getenv("MONGODB_URI") 
client = MongoClient(mongo_uri)
db = client["mentora"]
uploads_col = db["pdfuploads"]

# --- MODELS ---
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

# ✅ 1. THE PRIVATE ROUTE (Strictly for My Uploads)
# This is what your /uploads page uses. It requires an email header.
@router.get("/api/my-uploads")
def get_my_uploads(
    page: int = Query(1, ge=1), 
    category: str = "All",
    search: str = "",
    user_email: str = Header(..., alias="x-user-email") # <--- REQUIRED
):
    limit = 30
    skip = (page - 1) * limit
    
    # 🔒 FILTER: Only show files where uploaderEmail == logged-in email
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

# 2. GET ALL (Public Landing Page)
@router.get("/api/uploads")
def get_public_uploads(page: int = Query(1, ge=1), category: str = "All", search: str = ""):
    limit = 30
    skip = (page - 1) * limit
    query = {} 
    
    if category != "All": query["category"] = category
    if search: 
        query["$or"] = [{"title": {"$regex": search, "$options": "i"}}, {"tags": {"$in": [search]}}]

    total = uploads_col.count_documents(query)
    cursor = uploads_col.find(query).sort("createdAt", -1).skip(skip).limit(limit)
    
    return {
        "uploads": [UploadResponse(**u) for u in cursor],
        "totalPages": max(1, math.ceil(total / limit))
    }

# 3. GET SINGLE PDF
@router.get("/api/uploads/{id}")
def get_single_upload(id: str):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    doc = uploads_col.find_one({"_id": ObjectId(id)})
    if not doc: raise HTTPException(404, "Not found")
    return UploadResponse(**doc)

# 4. POST METADATA (Create Upload)
@router.post("/api/uploads", status_code=201)
def create_upload(upload: UploadSchema, user_email: str = Depends(get_current_user_email)):
    new_doc = upload.dict()
    new_doc.update({
        "uploaderEmail": user_email, # Saves the logged-in email here
        "slug": generate_slug(upload.title),
        "createdAt": datetime.now(),
        "updatedAt": datetime.now()
    })
    res = uploads_col.insert_one(new_doc)
    return UploadResponse(**uploads_col.find_one({"_id": res.inserted_id}))

# 5. DELETE (Secure Delete)
@router.delete("/api/uploads/{id}")
def delete_upload(id: str, user_email: str = Depends(get_current_user_email)):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    # Only delete if the ID exists AND the email matches
    res = uploads_col.delete_one({"_id": ObjectId(id), "uploaderEmail": user_email})
    if res.deleted_count == 0: raise HTTPException(404, "Not found or forbidden")
    return {"message": "Deleted"}
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

# ✅ Connect to Collections
uploads_col = db["pdfuploads"]
categories_col = db["categories"] 
users_col = db["appUsers"]

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
    uploaderImage: Optional[str] = None
    uploaderEmail: Optional[str] = None 

class UploadResponse(UploadSchema):
    id: PyObjectId = Field(alias="_id")
    uploaderEmail: str
    uploaderImage: Optional[str] = None
    slug: str
    createdAt: datetime

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat()}

class CategorySchema(BaseModel):
    name: str

def get_current_user_email(x_user_email: str = Header(None)):
    if not x_user_email:
        raise HTTPException(status_code=401, detail="Unauthorized: No Email Header")
    return x_user_email

def generate_slug(title: str):
    unique_suffix = datetime.now().strftime("%f")
    return f"{slugify(title)}-{unique_suffix}"

# ==========================================
# 📂 CATEGORY ROUTES
# ==========================================

@router.get("/api/categories")
def get_categories():
    cats = list(categories_col.find({}, {"_id": 1, "name": 1}).sort("createdAt", -1))
    cleaned_cats = []
    for c in cats:
        cleaned_cats.append({
            "_id": str(c["_id"]),
            "name": c.get("name", "Unnamed")
        })
    return cleaned_cats

# 🔥 NEW: TRENDING TOPICS ENDPOINT 🔥
@router.get("/api/trending")
def get_trending_topics():
    pipeline = [
        # 1. Filter out documents where category is missing or empty
        {"$match": {"category": {"$exists": True, "$ne": None, "$ne": ""}}},
        # 2. Group by 'category' and count them
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        # 3. Sort by 'count' in descending order (High to Low)
        {"$sort": {"count": -1}},
        # 4. Take the top 5
        {"$limit": 5}
    ]
    
    # Run aggregation
    results = list(uploads_col.aggregate(pipeline))
    
    # Extract names: [{"_id": "Python", "count": 10}] -> ["Python"]
    return {"categories": [item["_id"] for item in results]}

@router.post("/api/categories", status_code=201)
def create_category(category: CategorySchema):
    clean_name = category.name.strip()
    if not clean_name: raise HTTPException(400, "Name required")
    slug = slugify(clean_name)
    if categories_col.find_one({"slug": slug}):
        raise HTTPException(400, "Category already exists")
    new_cat = {
        "name": clean_name, "slug": slug,
        "createdAt": datetime.now(), "updatedAt": datetime.now(), "__v": 0
    }
    res = categories_col.insert_one(new_cat)
    return {"_id": str(res.inserted_id), "name": clean_name}

@router.put("/api/categories/{id}")
def update_category(id: str, category: CategorySchema):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    clean_name = category.name.strip()
    if not clean_name: raise HTTPException(400, "Name required")
    slug = slugify(clean_name)
    existing = categories_col.find_one({"slug": slug})
    if existing and str(existing["_id"]) != id:
        raise HTTPException(400, "Category name already taken")
    result = categories_col.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"name": clean_name, "slug": slug, "updatedAt": datetime.now()}}
    )
    if result.matched_count == 0: raise HTTPException(404, "Category not found")
    return {"_id": id, "name": clean_name}

# ==========================================
# 🔵 2. PDF UPLOAD ROUTES
# ==========================================

@router.post("/api/upload-file")
async def upload_file(file: UploadFile = File(...)):
    upload_dir = Path("static/pdfs")
    upload_dir.mkdir(parents=True, exist_ok=True)
    unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{file.filename}"
    file_path = upload_dir / unique_name
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"url": f"http://127.0.0.1:8000/static/pdfs/{unique_name}"}

# ✅ PRIVATE: Get MY Uploads (Requires Email)
@router.get("/api/my-uploads")
def get_my_uploads(page: int = Query(1, ge=1), category: str = "All", search: str = "", user_email: str = Header(..., alias="x-user-email")):
    limit = 30
    skip = (page - 1) * limit
    query = {"uploaderEmail": user_email}
    if category != "All": query["category"] = category
    if search: query["$or"] = [{"title": {"$regex": search, "$options": "i"}}, {"tags": {"$in": [search]}}]
    total = uploads_col.count_documents(query)
    cursor = uploads_col.find(query).sort("createdAt", -1).skip(skip).limit(limit)
    return {
        "uploads": [UploadResponse(**u) for u in cursor],
        "currentPage": page, "totalPages": max(1, math.ceil(total / limit))
    }

# ✅ PUBLIC: Get ALL Uploads (NO Email Required)
@router.get("/api/uploads")
def get_public_uploads(page: int = Query(1, ge=1), category: str = "All", search: str = ""):
    limit = 30
    skip = (page - 1) * limit
    query = {} 
    if category != "All": query["category"] = category
    if search: query["$or"] = [{"title": {"$regex": search, "$options": "i"}}, {"tags": {"$in": [search]}}]
    total = uploads_col.count_documents(query)
    cursor = uploads_col.find(query).sort("createdAt", -1).skip(skip).limit(limit)
    return {
        "uploads": [UploadResponse(**u) for u in cursor],
        "totalPages": max(1, math.ceil(total / limit))
    }

@router.get("/api/uploads/{id}")
def get_single_upload(id: str):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    doc = uploads_col.find_one({"_id": ObjectId(id)})
    if not doc: raise HTTPException(404, "Not found")
    return UploadResponse(**doc)

# 🎁 REWARD LOGIC: Create Upload & Add 50 Points
@router.post("/api/uploads", status_code=201)
def create_upload(upload: UploadSchema, user_email: str = Depends(get_current_user_email)):
    new_doc = upload.dict()
    new_doc.update({
        "uploaderEmail": user_email, 
        "slug": generate_slug(upload.title),
        "createdAt": datetime.now(),
        "updatedAt": datetime.now()
    })
    res = uploads_col.insert_one(new_doc)
    
    # Add Points using contribution service
    from services.contribution_service import ContributionService
    ContributionService.add_points(user_email, 50, "Uploaded PDF")
    ContributionService.increment_field(user_email, "uploadedPdfCount", 1)
    
    # Check badges
    from services.badge_service import BadgeService
    BadgeService.check_and_assign_badges(user_email)
    
    return UploadResponse(**uploads_col.find_one({"_id": res.inserted_id}))

# 🔻 PENALTY LOGIC: Delete Upload & Remove 50 Points
@router.delete("/api/uploads/{id}")
def delete_upload(id: str, user_email: str = Depends(get_current_user_email)):
    if not ObjectId.is_valid(id): raise HTTPException(400, "Invalid ID")
    res = uploads_col.delete_one({"_id": ObjectId(id), "uploaderEmail": user_email})
    if res.deleted_count == 0: raise HTTPException(404, "Not found or forbidden")
    
    # Remove Points using contribution service
    from services.contribution_service import ContributionService
    ContributionService.add_points(user_email, -50, "Deleted PDF")
    ContributionService.increment_field(user_email, "uploadedPdfCount", -1)
    
    return {"message": "Deleted and points deducted"}

# ==========================================
# 📊 3. USER STATS (Profile Page)
# ==========================================
@router.get("/api/user-stats")
def get_user_stats(user_email: str = Header(..., alias="x-user-email")):
    user = users_col.find_one({"email": user_email})
    points = user.get("contributionPoints", 0) if user else 0
    notes_count = uploads_col.count_documents({"uploaderEmail": user_email})
    
    badges = 0
    if notes_count >= 1: badges += 1
    if points >= 100: badges += 1
    
    return {
        "contributionPoints": points,
        "notesCount": notes_count,
        "badgesCount": badges,
        "image": user.get("image") if user else None
    }
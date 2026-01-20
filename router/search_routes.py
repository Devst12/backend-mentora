from fastapi import APIRouter, Query, HTTPException
from pymongo import MongoClient
from pydantic import BaseModel, Field, BeforeValidator
from typing import List, Annotated
from datetime import datetime
from bson import ObjectId
import os
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

class UploadResponse(BaseModel):
    id: PyObjectId = Field(alias="_id")
    title: str
    description: str
    pdfUrl: str
    tags: List[str] = []
    category: str
    commentsEnabled: bool
    visibility: str
    uploaderEmail: str
    uploaderImage: str | None = None
    slug: str
    createdAt: datetime

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda v: v.isoformat()}

# ==========================================
# 🔍 GOOGLE-LEVEL SEARCH ENGINE
# ==========================================

@router.get("/api/search/suggestions")
def get_search_suggestions(q: str = Query(..., min_length=1)):
    """
    Returns partial matches (e.g. typing 'pdf' shows 'new pdf', 'old pdf', 'pdf upload').
    """
    pipeline = [
        {"$unwind": "$tags"},
        {
            "$match": {
                # CHANGED: Removed '^' to allow matching anywhere in the string
                "tags": {"$regex": q, "$options": "i"} 
            }
        },
        {"$group": {"_id": "$tags"}},
        {"$limit": 10},
        {"$sort": {"_id": 1}}
    ]
    results = list(uploads_col.aggregate(pipeline))
    return {"suggestions": [item["_id"] for item in results]}


@router.get("/api/search")
def search_uploads(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Finds ALL related files (Tags OR Title).
    """
    limit_val = limit
    skip = (page - 1) * limit_val
    
    # CHANGED: Strict $in removed. Using pure Regex for fuzzy matching.
    query = {
        "$or": [
            {"tags": {"$elemMatch": {"$regex": q, "$options": "i"}}}, # Fuzzy tag match
            {"title": {"$regex": q, "$options": "i"}}                 # Fuzzy title match
        ]
    }
    
    total = uploads_col.count_documents(query)
    cursor = uploads_col.find(query).sort("createdAt", -1).skip(skip).limit(limit_val)
    results = [UploadResponse(**u) for u in cursor]
    
    return {
        "query": q,
        "totalResults": total,
        "currentPage": page,
        "totalPages": (total + limit_val - 1) // limit_val,
        "results": results
    }

@router.get("/api/search/slug/{slug}")
def get_upload_by_slug(slug: str):
    doc = uploads_col.find_one({"slug": slug})
    if not doc:
        raise HTTPException(status_code=404, detail="Upload not found")
    return UploadResponse(**doc)
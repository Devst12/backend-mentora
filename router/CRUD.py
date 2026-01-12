# router/uploads_routes.py
import os
import re
from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.param_functions import Depends
from pydantic import BaseModel, Field
from typing import List, Optional
from bson import ObjectId
import motor.motor_asyncio
from datetime import datetime
from utils import generate_custom_slug, get_current_user  # We'll define these helpers

# ────────────── INIT ROUTER & DB ──────────────
router = APIRouter()
MONGO_URI = os.getenv("MONGO_URI")
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["mentora_db"]
pdf_collection = db["pdf_uploads"]

# ────────────── MODELS ──────────────
class PdfUploadModel(BaseModel):
    title: str
    description: Optional[str] = ""
    pdfUrl: str
    tags: Optional[List[str]] = []
    category: Optional[str] = "Others"
    commentsEnabled: Optional[bool] = True
    visibility: Optional[str] = "Public"
    slug: Optional[str] = None
    uploaderEmail: str
    createdAt: datetime = Field(default_factory=datetime.utcnow)

# Helper to convert Mongo ObjectId to string
def serialize_doc(doc):
    doc["_id"] = str(doc["_id"])
    return doc

# ────────────── POST /uploads ──────────────
@router.post("/uploads")
async def create_upload(data: PdfUploadModel, user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Tag Limit Check
    if data.tags and len("".join(data.tags)) > 500:
        raise HTTPException(status_code=400, detail="Tags exceed 500 characters")

    temp_id = ObjectId()
    slug = generate_custom_slug(data.title, str(temp_id))
    data.slug = slug
    data.uploaderEmail = user["email"]

    doc = await pdf_collection.insert_one(jsonable_encoder(data))
    created_doc = await pdf_collection.find_one({"_id": doc.inserted_id})
    return JSONResponse(content=serialize_doc(created_doc), status_code=201)


# ────────────── GET /uploads ──────────────
@router.get("/uploads")
async def get_uploads(request: Request, page: int = 1, category: str = None, search: str = None, user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    limit = 30
    skip = (page - 1) * limit
    query = {"uploaderEmail": user["email"]}

    # Filters
    if category and category != "All":
        query["category"] = category
    if search:
        regex = re.compile(search, re.IGNORECASE)
        query["$or"] = [{"title": regex}, {"tags": {"$in": [regex]}}]

    cursor = pdf_collection.find(query).sort("createdAt", -1).skip(skip).limit(limit)
    data = [serialize_doc(doc) async for doc in cursor]
    total_items = await pdf_collection.count_documents(query)
    total_pages = (total_items + limit - 1) // limit

    return {"uploads": data, "currentPage": page, "totalPages": total_pages}


# ────────────── PUT /uploads/{id} ──────────────
@router.put("/uploads/{id}")
async def update_upload(id: str, request: Request, user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    existing_doc = await pdf_collection.find_one({"_id": ObjectId(id)})
    if not existing_doc:
        raise HTTPException(status_code=404, detail="Not found")
    if existing_doc["uploaderEmail"] != user["email"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    title_to_use = data.get("title") or existing_doc.get("title")
    update_data = {}
    for field in ["title", "description", "tags", "category", "commentsEnabled", "visibility"]:
        if field in data:
            update_data[field] = data[field]
    if title_to_use:
        update_data["slug"] = generate_custom_slug(title_to_use, id)

    await pdf_collection.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    updated_doc = await pdf_collection.find_one({"_id": ObjectId(id)})
    return JSONResponse(content=serialize_doc(updated_doc))


# ────────────── DELETE /uploads/{id} ──────────────
@router.delete("/uploads/{id}")
async def delete_upload(id: str, user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    doc_to_delete = await pdf_collection.find_one({"_id": ObjectId(id)})
    if not doc_to_delete:
        raise HTTPException(status_code=404, detail="Not found")
    if doc_to_delete["uploaderEmail"] != user["email"]:
        raise HTTPException(status_code=403, detail="Forbidden")

    await pdf_collection.delete_one({"_id": ObjectId(id)})
    return {"message": "Deleted successfully"}

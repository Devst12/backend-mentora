from fastapi import APIRouter, HTTPException, Body
from database import category_collection
from models.category import CategorySchema
from bson import ObjectId
import re

router = APIRouter(prefix="/api/categories", tags=["Categories"])

def slugify(text: str):
    return re.sub(r'[^\w-]+', '', text.lower().replace(" ", "-"))

def category_helper(cat) -> dict:
    return {
        "id": str(cat["_id"]), # Converting ObjectId to string for JSON
        "name": cat["name"],
        "slug": cat["slug"]
    }

@router.get("/")
async def get_categories():
    categories = []
    async for cat in category_collection.find().sort("_id", -1):
        categories.append(category_helper(cat))
    return categories

@router.post("/")
async def create_category(category: CategorySchema = Body(...)):
    slug = slugify(category.name)
    if await category_collection.find_one({"slug": slug}):
        raise HTTPException(status_code=400, detail="Category already exists")
    
    new_doc = {"name": category.name, "slug": slug}
    result = await category_collection.insert_one(new_doc)
    created = await category_collection.find_one({"_id": result.inserted_id})
    return category_helper(created)

@router.put("/{id}")
async def update_category(id: str, category: CategorySchema = Body(...)):
    slug = slugify(category.name)
    updated = await category_collection.find_one_and_update(
        {"_id": ObjectId(id)},
        {"$set": {"name": category.name, "slug": slug}},
        return_document=True
    )
    if updated:
        return category_helper(updated)
    raise HTTPException(status_code=404, detail="Category not found")
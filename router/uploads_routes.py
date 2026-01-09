from fastapi import APIRouter
from pymongo import MongoClient
import os
from bson.objectid import ObjectId

router = APIRouter()

mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(mongo_uri)
db = client["MentoraDB"]  # <-- select DB here
uploads_col = db["uploads"]  # <-- your uploads collection

# Helper to serialize Mongo docs
def serialize_doc(doc):
    doc["_id"] = str(doc["_id"])
    return doc

# Fetch all uploads
@router.get("/uploads")
def get_all_uploads():
    uploads = list(uploads_col.find().sort("_id", -1))  # newest first
    return [serialize_doc(u) for u in uploads]

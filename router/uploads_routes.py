from fastapi import APIRouter
from pymongo import MongoClient
import os
from bson.objectid import ObjectId

router = APIRouter()

mongo_uri = os.getenv("MONGO_URI")
client = MongoClient(mongo_uri)
db = client["MentoraDB"]  
uploads_col = db["uploads"] 


def serialize_doc(doc):
    doc["_id"] = str(doc["_id"])
    return doc

@router.get("/uploads")
def get_all_uploads():
    uploads = list(uploads_col.find().sort("_id", -1)) 
    return [serialize_doc(u) for u in uploads]

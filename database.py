# database.py
from dotenv import load_dotenv
import os
from pymongo import MongoClient

# ── Load .env file first ──
load_dotenv()  # This makes os.getenv("MONGO_URI") work

# ── Get MongoDB URI from environment variable ──
MONGO_URI = os.getenv("MONGO_URI")

# ── Connect to MongoDB ──
client = MongoClient(MONGO_URI)

# ── Select Database and Collection ──
db = client["MentoraDB"]
questions_col = db["questions"]

import motor.motor_asyncio

# Replace with your MongoDB URI
MONGO_URL = "mongodb://localhost:27017"
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URL)
database = client.unified_db  # Your DB Name
category_collection = database.get_collection("categories")
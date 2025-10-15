# clear_all.py
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
MONGO_URL = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "room_mgmt")

if not MONGO_URL:
    raise RuntimeError("Missing MONGODB_URI")

client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
client.admin.command("ping")
db = client[DB_NAME]
db["rooms"].delete_many({})
db["bookings"].delete_many({})
print("🧹 Cleared rooms & bookings.")

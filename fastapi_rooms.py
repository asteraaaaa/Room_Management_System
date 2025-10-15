# fastapi_rooms.py  (expanded schema + interval conflict)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, Field
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ServerSelectionTimeoutError
from bson import ObjectId
from datetime import datetime
from typing import Optional, List, Any, Dict
import os
from dotenv import load_dotenv

# ---------------------------
# Env + Mongo connection
# ---------------------------
load_dotenv()

MONGO_URL = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "room_mgmt")

if not MONGO_URL:
    raise RuntimeError("Missing MONGODB_URI in .env file")

client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
try:
    client.admin.command("ping")
    print("✅ Connected to MongoDB Atlas")
except ServerSelectionTimeoutError as e:
    raise RuntimeError(f"❌ Could not connect to MongoDB: {e}")

db = client[DB_NAME]
rooms_col = db["rooms"]
bookings_col = db["bookings"]

# Helpful indexes (idempotent)
rooms_col.create_index([("name_lc", ASCENDING)], unique=True, name="room_name_lc_unique")
rooms_col.create_index([("active", ASCENDING)], name="room_active")
bookings_col.create_index([("room_id", ASCENDING), ("start", ASCENDING)], name="room_start_idx")
bookings_col.create_index([("room_id", ASCENDING), ("end", ASCENDING)], name="room_end_idx")

# ---------------------------
# FastAPI app
# ---------------------------
app = FastAPI(title="Rooms & Bookings API (Expanded)")

# ---------------------------
# Models
# ---------------------------
class RoomIn(BaseModel):
    name: str = Field(..., min_length=1)
    level: Optional[str] = Field(default=None, description="e.g., L1, Level 3, Block B")
    capacity: Optional[int] = Field(default=None, ge=1)
    location: Optional[str] = None
    equipment: Optional[List[str]] = Field(default_factory=list, description="e.g., ['Projector','Whiteboard']")
    notes: Optional[str] = None
    active: bool = True

class RoomOut(BaseModel):
    id: str
    name: str
    level: Optional[str]
    capacity: Optional[int]
    location: Optional[str]
    equipment: List[str]
    notes: Optional[str]
    active: bool
    created_at: datetime

class BookingIn(BaseModel):
    room_id: str  # rooms._id as string
    name: str     # booker name
    purpose: Optional[str] = None
    attendees: Optional[int] = Field(default=None, ge=1)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    # ISO 8601 'YYYY-MM-DDTHH:MM' (seconds optional). We'll parse to datetime.
    start: str
    end: str

class BookingOut(BaseModel):
    id: str
    room_id: str
    room_name: str
    booker_name: str
    purpose: Optional[str]
    attendees: Optional[int]
    contact_email: Optional[str]
    contact_phone: Optional[str]
    start: datetime
    end: datetime
    created_at: datetime

# ---------------------------
# Helpers
# ---------------------------
def oid_str(oid: Any) -> str:
    return str(oid) if isinstance(oid, ObjectId) else str(oid)

def serialize_with_id(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    d = dict(doc)
    d["id"] = oid_str(d.pop("_id"))
    return d

def parse_iso(dt_str: str) -> datetime:
    """
    Accepts 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DDTHH:MM:SS'
    Raises ValueError if invalid.
    """
    try:
        # Try seconds first
        return datetime.fromisoformat(dt_str)
    except Exception:
        # Try without seconds
        try:
            return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M")
        except Exception:
            raise ValueError("Invalid datetime format. Use ISO 8601, e.g., 2025-10-15T14:30")

def ensure_no_overlap(room_id: str, start: datetime, end: datetime):
    """
    Overlap rule (closed-open): an existing booking overlaps if:
      existing.start < new_end AND existing.end > new_start
    """
    conflict = bookings_col.find_one({
        "room_id": room_id,
        "start": {"$lt": end},
        "end": {"$gt": start},
    })
    if conflict:
        raise HTTPException(
            status_code=409,
            detail="Time conflict: overlaps another booking for this room."
        )

# ---------------------------
# Routes
# ---------------------------
@app.get("/")
def root():
    return {"message": "Rooms & Bookings API is running (expanded schema)"}

# ---- Rooms ----
@app.post("/rooms/", response_model=RoomOut, status_code=201)
def create_room(room: RoomIn):
    name = (room.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Room name required")

    name_lc = name.lower()
    if rooms_col.find_one({"name_lc": name_lc}):
        raise HTTPException(status_code=400, detail="Room already exists")

    doc = {
        "name": name,
        "name_lc": name_lc,
        "level": room.level,
        "capacity": room.capacity,
        "location": room.location,
        "equipment": room.equipment or [],
        "notes": room.notes,
        "active": bool(room.active),
        "created_at": datetime.utcnow(),
    }
    res = rooms_col.insert_one(doc)
    out = serialize_with_id(doc | {"_id": res.inserted_id})
    return RoomOut(**out)  # type: ignore

@app.get("/rooms/", response_model=List[RoomOut])
def list_rooms():
    items = list(rooms_col.find({}).sort("created_at", 1))
    out: List[RoomOut] = []
    for r in items:
        s = serialize_with_id(r)
        out.append(RoomOut(**s))  # type: ignore
    return out

@app.patch("/rooms/{room_id}", response_model=RoomOut)
def update_room(room_id: str, room: RoomIn):
    try:
        _id = ObjectId(room_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room ID")

    # uniqueness on name if changed
    name = (room.name or "").strip()
    name_lc = name.lower()
    exists = rooms_col.find_one({"name_lc": name_lc, "_id": {"$ne": _id}})
    if exists:
        raise HTTPException(status_code=400, detail="Room already exists")

    update_doc = {
        "name": name,
        "name_lc": name_lc,
        "level": room.level,
        "capacity": room.capacity,
        "location": room.location,
        "equipment": room.equipment or [],
        "notes": room.notes,
        "active": bool(room.active),
    }
    res = rooms_col.find_one_and_update(
        {"_id": _id},
        {"$set": update_doc},
        return_document=True
    )
    if not res:
        raise HTTPException(status_code=404, detail="Room not found")
    s = serialize_with_id(res)
    return RoomOut(**s)  # type: ignore

@app.delete("/rooms/{room_id}")
def delete_room(room_id: str):
    try:
        _id = ObjectId(room_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room ID")

    # Cascade delete bookings
    bookings_col.delete_many({"room_id": room_id})

    res = rooms_col.delete_one({"_id": _id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Room not found")
    return {"message": "Room and its bookings deleted"}

# ---- Bookings ----
@app.post("/bookings/", response_model=BookingOut, status_code=201)
def create_booking(b: BookingIn):
    # Validate room exists & active
    try:
        _rid = ObjectId(b.room_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid room ID format")

    room = rooms_col.find_one({"_id": _rid})
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if not room.get("active", True):
        raise HTTPException(status_code=400, detail="Room is inactive")

    # Parse datetimes
    try:
        start_dt = parse_iso(b.start)
        end_dt = parse_iso(b.end)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    # Overlap check
    ensure_no_overlap(b.room_id, start_dt, end_dt)

    doc = {
        "room_id": b.room_id,
        "room_name": room["name"],  # denormalized for easy UI
        "booker_name": (b.name or "").strip(),
        "purpose": (b.purpose or None),
        "attendees": b.attendees,
        "contact_email": b.contact_email,
        "contact_phone": b.contact_phone,
        "start": start_dt,
        "end": end_dt,
        "created_at": datetime.utcnow(),
    }
    res = bookings_col.insert_one(doc)
    full = serialize_with_id(doc | {"_id": res.inserted_id})
    return BookingOut(**full)  # type: ignore

@app.get("/bookings/", response_model=List[BookingOut])
def list_bookings():
    items = list(bookings_col.find({}).sort("start", 1))
    out: List[BookingOut] = []
    for bk in items:
        s = serialize_with_id(bk)
        out.append(BookingOut(**s))  # type: ignore
    return out

@app.get("/rooms/{room_id}/bookings", response_model=List[BookingOut])
def list_bookings_for_room(room_id: str):
    items = list(bookings_col.find({"room_id": room_id}).sort("start", 1))
    return [BookingOut(**serialize_with_id(b) ) for b in items]  # type: ignore

@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: str):
    try:
        _id = ObjectId(booking_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid booking ID")

    res = bookings_col.delete_one({"_id": _id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Booking not found")
    return {"message": "Booking deleted"}

# ---------------------------
# Entrypoint
# ---------------------------
if __name__ == "__main__":
    import uvicorn
    # Run: python fastapi_rooms.py
    uvicorn.run(app, host="127.0.0.1", port=8000)

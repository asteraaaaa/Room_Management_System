# fastapi_rooms.py  (expanded schema + interval conflict)
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError
from bson import ObjectId
from datetime import datetime, timezone
from typing import Annotated, Optional, List, Any, Dict
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
# Every text field has a maximum length so a request cannot store megabytes of text.
EquipmentItem = Annotated[str, Field(min_length=1, max_length=50)]


class RoomIn(BaseModel):
    """A whole room, used to create one."""
    name: str = Field(..., min_length=1, max_length=100)
    level: Optional[str] = Field(default=None, max_length=50, description="e.g., L1, Level 3, Block B")
    capacity: Optional[int] = Field(default=None, ge=1, le=100000)
    location: Optional[str] = Field(default=None, max_length=120)
    equipment: Optional[List[EquipmentItem]] = Field(
        default_factory=list, max_length=30, description="e.g., ['Projector','Whiteboard']")
    notes: Optional[str] = Field(default=None, max_length=1000)
    active: bool = True


class RoomPatch(BaseModel):
    """
    A partial update: only the fields that are sent change. A field sent as null is cleared
    (name and active cannot be cleared). Fields that are left out keep their current value.
    """
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    level: Optional[str] = Field(default=None, max_length=50)
    capacity: Optional[int] = Field(default=None, ge=1, le=100000)
    location: Optional[str] = Field(default=None, max_length=120)
    equipment: Optional[List[EquipmentItem]] = Field(default=None, max_length=30)
    notes: Optional[str] = Field(default=None, max_length=1000)
    active: Optional[bool] = None


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
    name: str = Field(..., min_length=1, max_length=100)  # booker name
    purpose: Optional[str] = Field(default=None, max_length=200)
    attendees: Optional[int] = Field(default=None, ge=1, le=100000)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = Field(default=None, max_length=30)
    # ISO 8601 'YYYY-MM-DDTHH:MM' (seconds optional). An offset or a trailing 'Z' is accepted;
    # a time without an offset is taken as UTC. We'll parse to datetime.
    start: str = Field(..., max_length=40)
    end: str = Field(..., max_length=40)

    @field_validator("name")
    @classmethod
    def booker_name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Booker name is required")
        return v


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


def utcnow() -> datetime:
    """Current UTC time without tzinfo (what is stored in the database)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime) -> datetime:
    """
    One rule for every time: a time with an offset is converted to UTC, and a time without one is
    taken as UTC. That way two bookings can always be compared, whatever format each was sent in.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_iso(dt_str: str) -> datetime:
    """
    Accepts 'YYYY-MM-DDTHH:MM', 'YYYY-MM-DDTHH:MM:SS', and either with a '+08:00' offset or a trailing 'Z'.
    Raises ValueError if invalid.
    """
    s = (dt_str or "").strip()
    if s[-1:] in ("Z", "z"):          # Python < 3.11 does not understand a trailing Z
        s = s[:-1] + "+00:00"
    try:
        return to_naive_utc(datetime.fromisoformat(s))
    except ValueError:
        try:
            return to_naive_utc(datetime.strptime(s, "%Y-%m-%dT%H:%M"))
        except ValueError:
            raise ValueError("Invalid datetime format. Use ISO 8601, e.g., 2025-10-15T14:30")


def parse_object_id(value: str, what: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid {what}")


def overlap_query(room_id: str, start: datetime, end: datetime) -> Dict[str, Any]:
    """
    Overlap rule (closed-open): an existing booking overlaps if:
      existing.start < new_end AND existing.end > new_start
    """
    return {"room_id": room_id, "start": {"$lt": end}, "end": {"$gt": start}}


def ensure_no_overlap(room_id: str, start: datetime, end: datetime):
    if bookings_col.find_one(overlap_query(room_id, start, end)):
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
        "created_at": utcnow(),
    }
    try:
        res = rooms_col.insert_one(doc)
    except DuplicateKeyError:
        # two requests created the same name at the same moment; the unique index caught it
        raise HTTPException(status_code=400, detail="Room already exists")
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
def update_room(room_id: str, room: RoomPatch):
    _id = parse_object_id(room_id, "room ID")

    changes = room.model_dump(exclude_unset=True)   # only what the caller actually sent
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update")

    if "name" in changes:
        name = (changes["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Room name required")
        if rooms_col.find_one({"name_lc": name.lower(), "_id": {"$ne": _id}}):
            raise HTTPException(status_code=400, detail="Room already exists")
        changes["name"] = name
        changes["name_lc"] = name.lower()
    if "active" in changes and changes["active"] is None:
        raise HTTPException(status_code=400, detail="'active' cannot be null")
    if "equipment" in changes:
        changes["equipment"] = changes["equipment"] or []

    try:
        res = rooms_col.find_one_and_update(
            {"_id": _id},
            {"$set": changes},
            return_document=True
        )
    except DuplicateKeyError:
        raise HTTPException(status_code=400, detail="Room already exists")
    if not res:
        raise HTTPException(status_code=404, detail="Room not found")

    # Bookings keep a copy of the room name for the UI; keep that copy current after a rename.
    if "name" in changes:
        bookings_col.update_many({"room_id": str(_id)}, {"$set": {"room_name": changes["name"]}})

    s = serialize_with_id(res)
    return RoomOut(**s)  # type: ignore


@app.delete("/rooms/{room_id}")
def delete_room(room_id: str):
    _id = parse_object_id(room_id, "room ID")

    if not rooms_col.find_one({"_id": _id}):
        raise HTTPException(status_code=404, detail="Room not found")

    # Cascade delete bookings
    bookings_col.delete_many({"room_id": str(_id)})
    rooms_col.delete_one({"_id": _id})
    return {"message": "Room and its bookings deleted"}


# ---- Bookings ----
@app.post("/bookings/", response_model=BookingOut, status_code=201)
def create_booking(b: BookingIn):
    # Validate room exists & active
    _rid = parse_object_id(b.room_id, "room ID format")
    room_id = str(_rid)   # one canonical spelling, so lookups and the cascade delete always match

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

    # Overlap check (fast path, catches almost every conflict)
    ensure_no_overlap(room_id, start_dt, end_dt)

    doc = {
        "room_id": room_id,
        "room_name": room["name"],  # denormalized for easy UI
        "booker_name": b.name,      # already trimmed and checked by the model
        "purpose": ((b.purpose or "").strip() or None),
        "attendees": b.attendees,
        "contact_email": b.contact_email,
        "contact_phone": b.contact_phone,
        "start": start_dt,
        "end": end_dt,
        "created_at": utcnow(),
    }
    res = bookings_col.insert_one(doc)

    # The check above and the insert are two separate steps, so two requests for the same slot at the same
    # moment could both pass it. Check again now that our booking is stored: whichever request looks last is
    # guaranteed to see the other one. If anything else overlaps, take ours back. At most one survives; in a
    # true tie both back off and the person simply tries again.
    clash = bookings_col.find_one({**overlap_query(room_id, start_dt, end_dt), "_id": {"$ne": res.inserted_id}})
    if clash:
        bookings_col.delete_one({"_id": res.inserted_id})
        raise HTTPException(
            status_code=409,
            detail="Time conflict: overlaps another booking for this room."
        )

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
    _id = parse_object_id(room_id, "room ID")
    items = list(bookings_col.find({"room_id": str(_id)}).sort("start", 1))
    return [BookingOut(**serialize_with_id(b)) for b in items]  # type: ignore


@app.delete("/bookings/{booking_id}")
def delete_booking(booking_id: str):
    _id = parse_object_id(booking_id, "booking ID")

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

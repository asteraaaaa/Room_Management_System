# seed_api.py
import os
import requests
from datetime import datetime, timedelta
import random

API = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

# -------------------------------
# Mock Rooms (12 total)
# -------------------------------
ROOMS = [
    {"name": "Strategy Room Alpha", "level": "L4", "capacity": 10,
     "location": "Block A – North Wing",
     "equipment": ["TV", "Whiteboard", "LAN Port"],
     "notes": "Restricted area. Access card required.", "active": True},
    {"name": "Strategy Room Bravo", "level": "L4", "capacity": 8,
     "location": "Block A – North Wing",
     "equipment": ["TV", "Conference Phone", "Webcam"],
     "notes": "Small group meeting room.", "active": True},
    {"name": "Innovation Lab", "level": "L3", "capacity": 20,
     "location": "Block B – South Wing",
     "equipment": ["Projector", "Speaker", "Microphone", "HDMI"],
     "notes": "Flexible workspace for design thinking sessions.", "active": True},
    {"name": "Training Room East", "level": "L2", "capacity": 25,
     "location": "Block C – East Wing",
     "equipment": ["Projector", "Whiteboard", "Air-Conditioning"],
     "notes": "Used for technical courses.", "active": True},
    {"name": "Training Room West", "level": "L2", "capacity": 25,
     "location": "Block C – West Wing",
     "equipment": ["Projector", "Speaker", "Microphone"],
     "notes": "Common training facility.", "active": True},
    {"name": "Conference Hall 1", "level": "G", "capacity": 60,
     "location": "Main Building – Lobby",
     "equipment": ["Projector", "Speaker", "Microphone"],
     "notes": "Large events and briefings.", "active": True},
    {"name": "Conference Hall 2", "level": "G", "capacity": 50,
     "location": "Main Building – Lobby",
     "equipment": ["Projector", "Speaker", "Microphone"],
     "notes": "Overflow hall with similar setup as Hall 1.", "active": True},
    {"name": "Ops Planning Room", "level": "L3", "capacity": 12,
     "location": "Block D – Command Wing",
     "equipment": ["TV", "Conference Phone", "LAN Port"],
     "notes": "Operational planning meetings.", "active": True},
    {"name": "Analytics Room", "level": "L1", "capacity": 8,
     "location": "Block E – Basement",
     "equipment": ["TV", "LAN Port", "Whiteboard"],
     "notes": "Used for data review.", "active": True},
    {"name": "Briefing Room North", "level": "L1", "capacity": 15,
     "location": "Block F – North Wing",
     "equipment": ["Projector", "Whiteboard"],
     "notes": "Pre-deployment briefings.", "active": True},
    {"name": "Briefing Room South", "level": "L1", "capacity": 15,
     "location": "Block F – South Wing",
     "equipment": ["Projector", "Whiteboard"],
     "notes": "Smaller group briefings.", "active": True},
    {"name": "War Simulation Center", "level": "B1", "capacity": 30,
     "location": "Underground Block – Restricted",
     "equipment": ["Projector", "Speaker", "Microphone", "LAN Port"],
     "notes": "Simulation systems connected via secure VLAN.", "active": True},
]

# -------------------------------
# Mock People + Purposes
# -------------------------------
BOOKER_NAMES = [
    "Hj. Ariff", "Ir. Daniel", "Pn. Sofia", "Dr. Farhan", "Mr. Lim", "Eng. Noraini",
    "Dr. Gopal", "En. Hafiz", "Pn. Melissa", "TS. Rakesh", "Ir. Yong", "Mr. Iskandar",
    "Mdm. Chloe", "En. Farid", "Mr. Adrian", "Dr. Kamal", "Ms. Natasha", "Prof. Hanif"
]

PURPOSES = [
    "Briefing", "Training", "Workshop", "Mission Planning", "Equipment Calibration",
    "Strategy Review", "Safety Drill", "Client Presentation", "Coordination Meeting",
    "System Testing", "Simulation Run", "Performance Evaluation"
]

# -------------------------------
# Helper functions
# -------------------------------
def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")

def post(path, payload):
    r = requests.post(f"{API}{path}", json=payload, timeout=15)
    ok = r.status_code in (200, 201)
    try:
        return ok, r.json()
    except Exception:
        return ok, {"detail": r.text}

def get(path):
    r = requests.get(f"{API}{path}", timeout=15)
    ok = r.status_code == 200
    try:
        return ok, r.json()
    except Exception:
        return ok, {"detail": r.text}

# -------------------------------
# Main Seeder
# -------------------------------
def main(force=False):
    print("🔹 Checking rooms...")
    created = []
    for room in ROOMS:
        ok, data = post("/rooms/", room)
        if ok:
            created.append(room["name"])
        elif "already exists" in str(data):
            continue
        else:
            print("❌ Room error:", room["name"], data)
    print(f"✅ Rooms ensured: {len(created)} added / duplicates skipped.")

    ok, rooms = get("/rooms/")
    if not ok:
        print("❌ Cannot fetch rooms:", rooms)
        return
    name_to_id = {r["name"]: r["id"] for r in rooms}
    print(f"Total rooms available: {len(name_to_id)}")

    # Rooms are skipped when they already exist, but every run used to add another ~60 bookings.
    # Only seed bookings into an empty database unless --force is given.
    ok, existing = get("/bookings/")
    if ok and existing and not force:
        print(f"ℹ️  {len(existing)} booking(s) already exist; not adding more. Use --force to add another batch.")
        print("Done seeding!")
        return

    # Create 60 random bookings across next 14 days
    print("🔹 Creating bookings...")
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    total = 0
    for _ in range(60):
        room = random.choice(rooms)
        rid = room["id"]

        # pick random day/time window
        day_offset = random.randint(0, 13)
        start_hour = random.choice([8, 9, 10, 11, 13, 14, 15, 16])
        dur_hours = random.choice([1, 1.5, 2])
        s = (now + timedelta(days=day_offset)).replace(hour=start_hour)
        e = s + timedelta(hours=dur_hours)

        payload = {
            "room_id": rid,
            "name": random.choice(BOOKER_NAMES),
            "purpose": random.choice(PURPOSES),
            "attendees": random.choice([4, 6, 8, 10, 12, 15, 20]),
            "contact_email": f"{random.choice(['ops','admin','staff','planning'])}@example.com",
            "contact_phone": f"+60-{random.randint(10,19)}-{random.randint(1000000,9999999)}",
            "start": iso(s),
            "end": iso(e),
        }

        ok, data = post("/bookings/", payload)
        if ok:
            total += 1
        elif "conflict" in str(data).lower():
            continue
        else:
            print("Booking error:", data)

    print(f"✅ Created {total} bookings successfully.")
    print("Done seeding!")

if __name__ == "__main__":
    import sys
    main(force="--force" in sys.argv[1:])

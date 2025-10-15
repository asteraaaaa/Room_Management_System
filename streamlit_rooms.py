# streamlit_rooms.py
import os
import requests
import pandas as pd
import streamlit as st
from datetime import date, time, datetime, timedelta

# -----------------------------
# Config
# -----------------------------
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

st.set_page_config(
    page_title="Room & Booking Manager",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------
# API helpers
# -----------------------------
def api_ok() -> bool:
    try:
        r = requests.get(f"{API_BASE_URL}/", timeout=6)
        return r.ok
    except Exception:
        return False

def get(path: str):
    r = requests.get(f"{API_BASE_URL}{path}")
    return r.json(), r.status_code

def post(path: str, payload: dict):
    r = requests.post(f"{API_BASE_URL}{path}", json=payload)
    # Try to surface concise details
    if not r.ok:
        try:
            return r.json(), r.status_code
        except Exception:
            return {"detail": r.text}, r.status_code
    return r.json(), r.status_code

def patch(path: str, payload: dict):
    r = requests.patch(f"{API_BASE_URL}{path}", json=payload)
    if not r.ok:
        try:
            return r.json(), r.status_code
        except Exception:
            return {"detail": r.text}, r.status_code
    return r.json(), r.status_code

def delete(path: str):
    r = requests.delete(f"{API_BASE_URL}{path}")
    if not r.ok:
        try:
            return r.json(), r.status_code
        except Exception:
            return {"detail": r.text}, r.status_code
    return r.json(), r.status_code

# -----------------------------
# Utilities
# -----------------------------
def combine_iso(d: date, t: time) -> str:
    # API accepts ISO 8601 "YYYY-MM-DDTHH:MM"
    return f"{d.isoformat()}T{t.strftime('%H:%M')}"

def to_human_dt(dt_str: str | None) -> str:
    if not dt_str:
        return ""
    try:
        # API returns RFC 3339/ISO (FastAPI + pydantic) -> parse
        return pd.to_datetime(dt_str).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt_str)

def toast_result(ok: bool, msg_ok: str, msg_err: str):
    if ok:
        st.success(msg_ok)
    else:
        st.error(msg_err)

# Keep equipment presets to aid UX; can still type custom below
EQUIPMENT_PRESETS = [
    "Projector", "Whiteboard", "HDMI", "TV", "Conference Phone", "Speaker",
    "Webcam", "Microphone", "LAN Port", "Air-Conditioning"
]

# -----------------------------
# Pages
# -----------------------------
def rooms_page():
    st.header("Rooms")

    t1, t2, t3 = st.tabs(["Create Room", "View/Manage Rooms", "Quick Edit"])

    # --- Create Room ---
    with t1:
        st.subheader("Create New Room")
        with st.form("create_room_form"):
            name = st.text_input("Name", placeholder="Briefing Room A", help="Required")
            col1, col2 = st.columns(2)
            with col1:
                level = st.text_input("Level / Floor", placeholder="L2 / Level 2 / Block B")
                capacity = st.number_input("Capacity", min_value=1, step=1, value=10)
                active = st.checkbox("Active", value=True)
            with col2:
                location = st.text_input("Location", placeholder="Block B, East Wing")
                equip_multi = st.multiselect("Equipment (preset)", EQUIPMENT_PRESETS)
                custom_equip = st.text_input("Extra equipment (comma-separated)", placeholder="Apple TV, Laser pointer")
            notes = st.text_area("Notes", placeholder="Near pantry; has blackout curtains", height=80)

            submitted = st.form_submit_button("Create Room", type="primary")
            if submitted:
                if not name.strip():
                    st.error("Room name is required")
                else:
                    equipment = list(dict.fromkeys([*equip_multi, *[e.strip() for e in custom_equip.split(",") if e.strip()]]))
                    payload = {
                        "name": name.strip(),
                        "level": level.strip() or None,
                        "capacity": int(capacity) if capacity else None,
                        "location": location.strip() or None,
                        "equipment": equipment,
                        "notes": notes.strip() or None,
                        "active": bool(active),
                    }
                    data, code = post("/rooms/", payload)
                    if code == 201:
                        st.success(f"✅ Room created: {data['name']} (ID saved by server)")
                        st.rerun()
                    else:
                        st.error(f"❌ {data.get('detail', 'Failed to create room')}")

    # --- View/Manage Rooms ---
    with t2:
        st.subheader("All Rooms")
        rooms, rc = get("/rooms/")
        if rc == 200 and rooms:
            df = pd.DataFrame(rooms)
            # nice columns
            cols = ["id","name","level","capacity","location","equipment","active","created_at","notes"]
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            df["created_at"] = pd.to_datetime(df["created_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")
            st.dataframe(df[cols], use_container_width=True, hide_index=True)

            st.markdown("---")
            colA, colB = st.columns([1,1])
            with colA:
                # Delete selected
                label_id_map = {f"{r['name']} ({r['id'][:8]}…)": r["id"] for r in rooms}
                pick = st.selectbox("Select room to delete", list(label_id_map.keys()) if label_id_map else [], key="del_room_pick")
                if pick and st.button("Delete Room", type="secondary"):
                    rid = label_id_map[pick]
                    d, c = delete(f"/rooms/{rid}")
                    if c == 200:
                        st.success("🗑️ Room deleted (and its bookings).")
                        st.rerun()
                    else:
                        st.error(f"❌ {d.get('detail','Delete failed')}")
            with colB:
                st.info("Tip: Delete cascades all bookings for that room.")

        else:
            st.info("No rooms yet")

    # --- Quick Edit (PATCH) ---
    with t3:
        st.subheader("Quick Edit a Room")
        rooms, rc = get("/rooms/")
        if rc == 200 and rooms:
            m = {f"{r['name']} ({r['id'][:8]}…)": r for r in rooms}
            pick = st.selectbox("Pick a room", list(m.keys()))
            if pick:
                r = m[pick]
                with st.form(f"edit_{r['id']}"):
                    name = st.text_input("Name", value=r["name"])
                    c1, c2 = st.columns(2)
                    with c1:
                        level = st.text_input("Level / Floor", value=r.get("level") or "")
                        capacity = st.number_input("Capacity", min_value=1, step=1, value=int(r.get("capacity") or 1))
                        active = st.checkbox("Active", value=bool(r.get("active", True)))
                    with c2:
                        location = st.text_input("Location", value=r.get("location") or "")
                        equip_multi = st.multiselect("Equipment (preset)", EQUIPMENT_PRESETS,
                                                     default=[e for e in (r.get("equipment") or []) if e in EQUIPMENT_PRESETS])
                        others = [e for e in (r.get("equipment") or []) if e not in EQUIPMENT_PRESETS]
                        custom_equip = st.text_input("Extra equipment (comma-separated)", value=", ".join(others))
                    notes = st.text_area("Notes", value=r.get("notes") or "", height=80)

                    submitted = st.form_submit_button("Save changes", type="primary")
                    if submitted:
                        equipment = list(dict.fromkeys([*equip_multi, *[e.strip() for e in custom_equip.split(",") if e.strip()]]))
                        payload = {
                            "name": name.strip(),
                            "level": level.strip() or None,
                            "capacity": int(capacity) if capacity else None,
                            "location": location.strip() or None,
                            "equipment": equipment,
                            "notes": notes.strip() or None,
                            "active": bool(active),
                        }
                        data, code = patch(f"/rooms/{r['id']}", payload)
                        if code == 200:
                            st.success("✅ Room updated")
                            st.rerun()
                        else:
                            st.error(f"❌ {data.get('detail', 'Update failed')}")
        else:
            st.info("No rooms to edit")


def bookings_page():
    st.header("Bookings")

    t1, t2, t3 = st.tabs(["Create Booking", "All Bookings", "By Room"])

    # Fetch rooms (active)
    rooms, rc = get("/rooms/")
    active_rooms = [r for r in rooms] if rc == 200 else []

    # --- Create Booking ---
    with t1:
        st.subheader("Create New Booking")
        if not active_rooms:
            st.warning("⚠️ No rooms available. Create a room first.")
        else:
            with st.form("create_booking_form"):
                rmap = {f"{r['name']} ({r.get('level','-')})": r for r in active_rooms if r.get("active", True)}
                room_label = st.selectbox("Room", list(rmap.keys()))
                col1, col2, col3 = st.columns([1.2, 1, 1])
                with col1:
                    name = st.text_input("Your name")
                    purpose = st.text_input("Purpose")
                    attendees = st.number_input("Attendees", min_value=1, step=1, value=5)
                with col2:
                    contact_email = st.text_input("Contact email")
                    start_date = st.date_input("Start date", value=date.today())
                    start_time = st.time_input("Start time", value=(datetime.now() + timedelta(minutes=15)).time().replace(second=0, microsecond=0))
                with col3:
                    contact_phone = st.text_input("Contact phone")
                    end_date = st.date_input("End date", value=date.today())
                    end_time = st.time_input("End time", value=(datetime.now() + timedelta(hours=1)).time().replace(second=0, microsecond=0))

                submitted = st.form_submit_button("Create Booking", type="primary")

                if submitted:
                    if not name.strip():
                        st.error("Booker name is required")
                    else:
                        # Compose ISO strings
                        start_iso = combine_iso(start_date, start_time)
                        end_iso = combine_iso(end_date, end_time)

                        payload = {
                            "room_id": rmap[room_label]["id"],
                            "name": name.strip(),
                            "purpose": purpose.strip() or None,
                            "attendees": int(attendees),
                            "contact_email": contact_email.strip() or None,
                            "contact_phone": contact_phone.strip() or None,
                            "start": start_iso,
                            "end": end_iso,
                        }
                        data, code = post("/bookings/", payload)
                        if code == 201:
                            st.success("✅ Booking created")
                            st.rerun()
                        else:
                            st.error(f"❌ {data.get('detail','Failed to create booking')}")

    # --- All Bookings ---
    with t2:
        st.subheader("Upcoming / All Bookings")
        items, code = get("/bookings/")
        if code == 200 and items:
            df = pd.DataFrame(items)
            for c in ["start", "end", "created_at"]:
                if c in df:
                    df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d %H:%M")
            show_cols = ["id","room_name","booker_name","purpose","attendees","start","end","created_at","contact_email","contact_phone"]
            for c in show_cols:
                if c not in df.columns:
                    df[c] = None
            st.dataframe(df[show_cols], use_container_width=True, hide_index=True)

            st.markdown("---")
            # Inline cancel
            label_id_map = {f"{b['room_name']} — {b['booker_name']} ({to_human_dt(b.get('start'))} → {to_human_dt(b.get('end'))})": b["id"] for b in items}
            pick = st.selectbox("Cancel booking", list(label_id_map.keys()) if label_id_map else [], key="cancel_bkg_pick")
            if pick and st.button("Cancel selected booking", type="secondary"):
                bid = label_id_map[pick]
                d, c = delete(f"/bookings/{bid}")
                if c == 200:
                    st.success("🗑️ Booking cancelled")
                    st.rerun()
                else:
                    st.error(f"❌ {d.get('detail','Delete failed')}")
        else:
            st.info("No bookings yet")

    # --- Bookings by Room ---
    with t3:
        st.subheader("Bookings by Room")
        rooms, rc = get("/rooms/")
        if rc == 200 and rooms:
            m = {f"{r['name']} ({r.get('level','-')})": r for r in rooms}
            pick = st.selectbox("Room", list(m.keys()))
            if pick:
                rid = m[pick]["id"]
                items, code = get(f"/rooms/{rid}/bookings")
                if code == 200 and items:
                    for b in items:
                        st.write(
                            f"• **{b['room_name']}** — {b['booker_name']} | "
                            f"{to_human_dt(b.get('start'))} → {to_human_dt(b.get('end'))} | "
                            f"({b.get('purpose','-')})"
                        )
                else:
                    st.info("No bookings for this room")
        else:
            st.info("No rooms found")

def dashboard_page():
    st.header("Dashboard")
    rooms, rc = get("/rooms/")
    bookings, bc = get("/bookings/")
    if rc == 200 and bc == 200:
        c1, c2, c3, c4 = st.columns(4)
        with c1: st.metric("Rooms", len(rooms))
        with c2: st.metric("Active Rooms", sum(1 for r in rooms if r.get("active", True)))
        with c3: st.metric("Bookings", len(bookings))
        with c4:
            unique_days = len(set(pd.to_datetime([b["start"] for b in bookings]).date)) if bookings else 0
            st.metric("Days with bookings", unique_days)

        st.markdown("---")
        if bookings:
            df = pd.DataFrame(bookings)
            df["start_day"] = pd.to_datetime(df["start"]).dt.date
            st.subheader("Bookings per Day")
            st.line_chart(df.groupby("start_day").size(), use_container_width=True)
        else:
            st.info("No booking data yet")
    else:
        st.error("❌ Failed to load data")

# -----------------------------
# App entry
# -----------------------------
st.title("🏢 Room & Booking Manager")
st.caption("Interval-based conflicts, richer room attributes, and improved forms.")
st.markdown("---")

if not api_ok():
    st.error(f"❌ Cannot connect to FastAPI server at {API_BASE_URL}")
    st.info("Run API first:\n\n```bash\npython fastapi_rooms.py\n```")
else:
    st.success("Connected to FastAPI server")
    st.sidebar.title("Navigation")
    page = st.sidebar.selectbox("Choose a page", ["Rooms", "Bookings", "Dashboard"])
    if page == "Rooms":
        rooms_page()
    elif page == "Bookings":
        bookings_page()
    else:
        dashboard_page()

"""API tests for fastapi_rooms.py (run with: pytest). The database is an in-memory stand-in; see conftest.py."""
import threading
from concurrent.futures import ThreadPoolExecutor

import fastapi_rooms as app_mod
import pytest
from fastapi.testclient import TestClient

client = TestClient(app_mod.app, raise_server_exceptions=False)   # unhandled errors come back as HTTP 500

D = "2030-06-10"   # a fixed future day


@pytest.fixture(autouse=True)
def clean_db():
    app_mod.rooms_col.delete_many({})
    app_mod.bookings_col.delete_many({})
    yield


# ---------- helpers ----------
def make_room(**kw):
    body = {"name": "Room A"}
    body.update(kw)
    r = client.post("/rooms/", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def make_booking(room_id, start, end, **kw):
    body = {"room_id": room_id, "name": "Alice", "start": start, "end": end}
    body.update(kw)
    return client.post("/bookings/", json=body)


# ---------- basics ----------
def test_root_is_up():
    assert client.get("/").status_code == 200


# ---------- rooms: create ----------
def test_create_room_minimal_gets_defaults():
    room = make_room(name="Minimal")
    assert room["id"] and room["active"] is True and room["equipment"] == []
    assert room["created_at"]


def test_create_room_full():
    room = make_room(name="Hall", level="L2", capacity=40, location="Block B",
                     equipment=["Projector", "Mic"], notes="n", active=False)
    assert (room["level"], room["capacity"], room["location"], room["active"]) == ("L2", 40, "Block B", False)
    assert room["equipment"] == ["Projector", "Mic"]


def test_duplicate_name_is_rejected_case_insensitively():
    make_room(name="Boardroom")
    assert client.post("/rooms/", json={"name": "  BOARDROOM  "}).status_code == 400


def test_two_creates_racing_on_one_name_get_a_clean_400(monkeypatch):
    """If both requests pass the pre-check, the unique index must still turn the loser into a 400, not a 500."""
    make_room(name="Taken")
    monkeypatch.setattr(app_mod.rooms_col, "find_one", lambda *a, **k: None)   # pre-check sees nothing
    assert client.post("/rooms/", json={"name": "Taken"}).status_code == 400


def test_blank_room_name_is_rejected():
    assert client.post("/rooms/", json={"name": "   "}).status_code == 400
    assert client.post("/rooms/", json={"name": ""}).status_code == 422


def test_zero_or_negative_capacity_is_rejected():
    assert client.post("/rooms/", json={"name": "X", "capacity": 0}).status_code == 422
    assert client.post("/rooms/", json={"name": "Y", "capacity": -3}).status_code == 422


@pytest.mark.parametrize("body", [
    {"name": "x" * 101},
    {"name": "N", "level": "x" * 51},
    {"name": "N", "location": "x" * 121},
    {"name": "N", "notes": "x" * 1001},
    {"name": "N", "equipment": ["e"] * 31},
    {"name": "N", "equipment": ["x" * 51]},
    {"name": "N", "capacity": 100001},
])
def test_overlong_room_fields_are_rejected(body):
    assert client.post("/rooms/", json=body).status_code == 422


def test_list_rooms_oldest_first():
    for n in ("One", "Two", "Three"):
        make_room(name=n)
    assert [r["name"] for r in client.get("/rooms/").json()] == ["One", "Two", "Three"]


# ---------- rooms: update (PATCH) ----------
def test_patch_full_update_works():
    room = make_room(name="Old", level="L1", capacity=5)
    r = client.patch(f"/rooms/{room['id']}", json={"name": "New", "level": "L9", "capacity": 7})
    assert r.status_code == 200 and r.json()["name"] == "New" and r.json()["level"] == "L9"


def test_patch_with_only_a_name_keeps_the_other_fields():
    room = make_room(name="Lab", level="L3", capacity=20, location="Block C",
                     equipment=["Projector"], notes="keep me", active=False)
    r = client.patch(f"/rooms/{room['id']}", json={"name": "Lab 2"})
    assert r.status_code == 200
    out = r.json()
    assert out["name"] == "Lab 2"
    assert out["level"] == "L3" and out["capacity"] == 20 and out["location"] == "Block C"
    assert out["equipment"] == ["Projector"] and out["notes"] == "keep me"
    assert out["active"] is False, "an inactive room must not be silently re-activated"


def test_patch_can_change_a_single_flag_without_touching_the_name():
    room = make_room(name="Flag", level="L1")
    out = client.patch(f"/rooms/{room['id']}", json={"active": False}).json()
    assert out["active"] is False and out["name"] == "Flag" and out["level"] == "L1"


def test_patch_with_null_clears_an_optional_field():
    room = make_room(name="Clear", level="L1", notes="old note", equipment=["TV"])
    out = client.patch(f"/rooms/{room['id']}", json={"level": None, "notes": None, "equipment": None}).json()
    assert out["level"] is None and out["notes"] is None and out["equipment"] == []
    assert out["name"] == "Clear"


def test_patch_cannot_clear_the_name_or_the_active_flag():
    room = make_room(name="Keep")
    assert client.patch(f"/rooms/{room['id']}", json={"name": None}).status_code == 400
    assert client.patch(f"/rooms/{room['id']}", json={"name": "   "}).status_code == 400
    assert client.patch(f"/rooms/{room['id']}", json={"active": None}).status_code == 400


def test_patch_with_nothing_to_change_is_rejected():
    room = make_room(name="Same")
    assert client.patch(f"/rooms/{room['id']}", json={}).status_code == 400


def test_patch_to_an_existing_name_is_rejected():
    make_room(name="A")
    b = make_room(name="B")
    assert client.patch(f"/rooms/{b['id']}", json={"name": "a"}).status_code == 400


def test_patch_keeping_its_own_name_is_fine():
    room = make_room(name="Mine")
    assert client.patch(f"/rooms/{room['id']}", json={"name": "Mine", "level": "L2"}).status_code == 200


def test_patch_bad_and_unknown_ids():
    assert client.patch("/rooms/not-an-id", json={"name": "X"}).status_code == 400
    assert client.patch("/rooms/" + "0" * 24, json={"name": "X"}).status_code == 404


def test_renaming_a_room_updates_its_existing_bookings():
    room = make_room(name="Before")
    other = make_room(name="Other")
    assert make_booking(room["id"], f"{D}T09:00", f"{D}T10:00").status_code == 201
    assert make_booking(other["id"], f"{D}T09:00", f"{D}T10:00").status_code == 201
    client.patch(f"/rooms/{room['id']}", json={"name": "After"})
    names = sorted(b["room_name"] for b in client.get("/bookings/").json())
    assert names == ["After", "Other"]


# ---------- rooms: delete ----------
def test_deleting_a_room_removes_its_bookings():
    room = make_room(name="Gone")
    other = make_room(name="Stay")
    make_booking(room["id"], f"{D}T09:00", f"{D}T10:00")
    make_booking(other["id"], f"{D}T09:00", f"{D}T10:00")
    assert client.delete(f"/rooms/{room['id']}").status_code == 200
    assert [b["room_name"] for b in client.get("/bookings/").json()] == ["Stay"]


def test_delete_bad_and_unknown_room():
    assert client.delete("/rooms/zzz").status_code == 400
    assert client.delete("/rooms/" + "0" * 24).status_code == 404


def test_deleting_an_unknown_room_deletes_nothing_else():
    room = make_room(name="Safe")
    make_booking(room["id"], f"{D}T09:00", f"{D}T10:00")
    assert client.delete("/rooms/" + "0" * 24).status_code == 404
    assert len(client.get("/bookings/").json()) == 1


def test_an_uppercase_room_id_still_matches_the_cascade_delete():
    """The same id can be written in upper or lower case; bookings must be stored under one spelling."""
    room = make_room(name="Case")
    assert make_booking(room["id"].upper(), f"{D}T09:00", f"{D}T10:00").status_code == 201
    assert len(client.get(f"/rooms/{room['id']}/bookings").json()) == 1
    assert client.delete(f"/rooms/{room['id']}").status_code == 200
    assert client.get("/bookings/").json() == []


# ---------- bookings: create and validation ----------
def test_valid_booking():
    room = make_room()
    r = make_booking(room["id"], f"{D}T09:00", f"{D}T10:30", purpose="Standup", attendees=4,
                     contact_email="a@example.com", contact_phone="012")
    assert r.status_code == 201
    b = r.json()
    assert b["room_name"] == "Room A" and b["booker_name"] == "Alice" and b["attendees"] == 4


def test_booking_validation_errors():
    rid = make_room()["id"]
    assert make_booking(rid, f"{D}T10:00", f"{D}T09:00").status_code == 400        # end before start
    assert make_booking(rid, f"{D}T10:00", f"{D}T10:00").status_code == 400        # zero length
    assert make_booking(rid, "tomorrow", f"{D}T10:00").status_code == 400          # bad datetime
    assert make_booking("nope", f"{D}T09:00", f"{D}T10:00").status_code == 400     # bad room id
    assert make_booking("0" * 24, f"{D}T09:00", f"{D}T10:00").status_code == 404   # unknown room
    assert make_booking(rid, f"{D}T09:00", f"{D}T10:00", attendees=0).status_code == 422
    assert make_booking(rid, f"{D}T09:00", f"{D}T10:00", contact_email="bad").status_code == 422
    assert client.post("/bookings/", json={"room_id": rid}).status_code == 422     # missing fields


def test_inactive_room_cannot_be_booked():
    room = make_room(name="Closed", active=False)
    assert make_booking(room["id"], f"{D}T09:00", f"{D}T10:00").status_code == 400


def test_blank_booker_name_is_rejected_by_the_api():
    room = make_room()
    assert make_booking(room["id"], f"{D}T09:00", f"{D}T10:00", name="   ").status_code == 422
    assert make_booking(room["id"], f"{D}T09:00", f"{D}T10:00", name="").status_code == 422


def test_booker_name_is_trimmed():
    room = make_room()
    b = make_booking(room["id"], f"{D}T09:00", f"{D}T10:00", name="  Bob  ").json()
    assert b["booker_name"] == "Bob"


@pytest.mark.parametrize("field,value", [
    ("name", "x" * 101), ("purpose", "x" * 201), ("contact_phone", "1" * 31), ("attendees", 100001),
])
def test_overlong_booking_fields_are_rejected(field, value):
    room = make_room()
    assert make_booking(room["id"], f"{D}T09:00", f"{D}T10:00", **{field: value}).status_code == 422


# ---------- time formats and time zones ----------
def test_iso_time_with_a_Z_suffix_is_accepted():
    room = make_room()
    assert make_booking(room["id"], f"{D}T09:00:00Z", f"{D}T10:00:00Z").status_code == 201


def test_mixing_a_timezone_offset_with_a_plain_time_does_not_crash():
    room = make_room()
    r = make_booking(room["id"], f"{D}T09:00+08:00", f"{D}T10:00")
    assert r.status_code in (201, 400, 409)   # anything but a server error


def test_timezone_aware_times_conflict_correctly():
    room = make_room()
    assert make_booking(room["id"], f"{D}T09:00+08:00", f"{D}T10:00+08:00").status_code == 201
    assert make_booking(room["id"], f"{D}T09:30+08:00", f"{D}T10:30+08:00").status_code == 409


def test_an_offset_time_and_the_same_moment_in_utc_are_the_same_slot():
    """09:00+08:00 is 01:00 UTC, and a time with no offset is taken as UTC."""
    room = make_room()
    assert make_booking(room["id"], f"{D}T09:00+08:00", f"{D}T10:00+08:00").status_code == 201
    assert make_booking(room["id"], f"{D}T01:30", f"{D}T02:30").status_code == 409
    assert make_booking(room["id"], f"{D}T01:30:00Z", f"{D}T02:30:00Z").status_code == 409
    assert make_booking(room["id"], f"{D}T02:00", f"{D}T03:00").status_code == 201   # starts as the other ends


def test_stored_times_come_back_as_utc():
    room = make_room()
    b = make_booking(room["id"], f"{D}T09:00+08:00", f"{D}T10:00+08:00").json()
    assert b["start"].startswith(f"{D}T01:00") and b["end"].startswith(f"{D}T02:00")


# ---------- the overlap rule ----------
@pytest.mark.parametrize("start,end,expected", [
    ("09:00", "10:00", 409),   # identical
    ("09:30", "10:30", 409),   # overlaps the end
    ("08:30", "09:30", 409),   # overlaps the start
    ("09:15", "09:45", 409),   # inside
    ("08:00", "11:00", 409),   # contains
    ("10:00", "11:00", 201),   # starts exactly when the other ends: allowed
    ("08:00", "09:00", 201),   # ends exactly when the other starts: allowed
])
def test_overlap_rule(start, end, expected):
    room = make_room()
    assert make_booking(room["id"], f"{D}T09:00", f"{D}T10:00").status_code == 201
    assert make_booking(room["id"], f"{D}T{start}", f"{D}T{end}").status_code == expected


def test_same_time_in_a_different_room_is_fine():
    a, b = make_room(name="A"), make_room(name="B")
    assert make_booking(a["id"], f"{D}T09:00", f"{D}T10:00").status_code == 201
    assert make_booking(b["id"], f"{D}T09:00", f"{D}T10:00").status_code == 201


def test_a_cancelled_slot_can_be_booked_again():
    room = make_room()
    first = make_booking(room["id"], f"{D}T09:00", f"{D}T10:00").json()
    assert client.delete(f"/bookings/{first['id']}").status_code == 200
    assert make_booking(room["id"], f"{D}T09:00", f"{D}T10:00").status_code == 201


def test_a_request_that_loses_a_race_takes_its_booking_back(monkeypatch):
    """
    Simulates the unlucky interleaving: another request for the same slot is stored between our overlap
    check and our insert. After inserting, we look again, see it, and withdraw our own booking.
    """
    room = make_room()
    real_insert = app_mod.bookings_col.insert_one
    competitor = {}

    def insert_then_competitor_arrives(doc, *a, **k):
        res = real_insert(doc, *a, **k)
        # the other request lands right after ours was stored
        rival = {**{k2: v for k2, v in doc.items() if k2 != "_id"}, "booker_name": "Rival"}
        competitor["id"] = real_insert(rival).inserted_id
        return res

    monkeypatch.setattr(app_mod.bookings_col, "insert_one", insert_then_competitor_arrives)
    r = make_booking(room["id"], f"{D}T09:00", f"{D}T10:00")
    assert r.status_code == 409
    monkeypatch.undo()
    remaining = client.get("/bookings/").json()
    assert [b["booker_name"] for b in remaining] == ["Rival"], "exactly one booking must remain"


def test_simultaneous_requests_for_one_slot_never_double_book():
    room = make_room()
    barrier = threading.Barrier(12)

    def book(_):
        barrier.wait()
        return make_booking(room["id"], f"{D}T09:00", f"{D}T10:00").status_code

    with ThreadPoolExecutor(12) as pool:
        codes = list(pool.map(book, range(12)))
    assert codes.count(201) <= 1, f"{codes.count(201)} of 12 simultaneous requests got the same slot"
    assert len(client.get("/bookings/").json()) == codes.count(201)


# ---------- bookings: list, per room, delete ----------
def test_bookings_are_listed_by_start_time():
    room = make_room()
    make_booking(room["id"], f"{D}T14:00", f"{D}T15:00")
    make_booking(room["id"], f"{D}T09:00", f"{D}T10:00")
    starts = [b["start"] for b in client.get("/bookings/").json()]
    assert starts == sorted(starts)


def test_bookings_for_one_room_only():
    a, b = make_room(name="A"), make_room(name="B")
    make_booking(a["id"], f"{D}T09:00", f"{D}T10:00")
    make_booking(b["id"], f"{D}T09:00", f"{D}T10:00")
    out = client.get(f"/rooms/{a['id']}/bookings").json()
    assert len(out) == 1 and out[0]["room_name"] == "A"


def test_bookings_for_a_malformed_room_id_is_a_clear_400():
    assert client.get("/rooms/not-an-id/bookings").status_code == 400


def test_delete_booking_bad_and_unknown():
    assert client.delete("/bookings/zzz").status_code == 400
    assert client.delete("/bookings/" + "0" * 24).status_code == 404

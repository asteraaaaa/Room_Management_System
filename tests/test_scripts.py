"""Tests for the helper scripts (wipe.py and seed_api.py). Database is an in-memory stand-in; see conftest.py."""
import pytest

import fastapi_rooms as app_mod
import seed_api
import wipe


@pytest.fixture
def filled_db(monkeypatch):
    # the in-memory stand-in gives every MongoClient() its own empty store, so point wipe.py at the shared one
    monkeypatch.setattr(wipe, "MongoClient", lambda *a, **k: app_mod.client)
    app_mod.rooms_col.delete_many({})
    app_mod.bookings_col.delete_many({})
    app_mod.rooms_col.insert_one({"name": "R", "name_lc": "r"})
    app_mod.bookings_col.insert_one({"room_id": "x", "room_name": "R"})
    yield
    app_mod.rooms_col.delete_many({})
    app_mod.bookings_col.delete_many({})


# ---------- wipe.py ----------
def test_wipe_does_nothing_if_the_confirmation_is_wrong(filled_db, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: "yes")
    assert wipe.main([]) == 1
    assert app_mod.rooms_col.count_documents({}) == 1 and app_mod.bookings_col.count_documents({}) == 1
    assert "Nothing was deleted" in capsys.readouterr().out


def test_wipe_deletes_after_the_database_name_is_typed(filled_db, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "rms_test")
    assert wipe.main([]) == 0
    assert app_mod.rooms_col.count_documents({}) == 0 and app_mod.bookings_col.count_documents({}) == 0


def test_wipe_with_yes_flag_skips_the_question(filled_db, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not ask when --yes is given"))
    assert wipe.main(["--yes"]) == 0
    assert app_mod.rooms_col.count_documents({}) == 0


def test_wipe_shows_the_database_name_but_never_the_connection_string(filled_db, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_: "no")
    wipe.main([])
    out = capsys.readouterr().out
    assert "rms_test" in out and "fake-host" not in out and "mongodb" not in out.lower()


# ---------- seed_api.py ----------
def test_seeder_does_not_add_bookings_when_some_exist(monkeypatch, capsys):
    posted = []
    rooms = [{"name": r["name"], "id": f"id{i}"} for i, r in enumerate(seed_api.ROOMS)]

    def fake_get(path):
        return (True, rooms) if path == "/rooms/" else (True, [{"id": "b1"}])

    monkeypatch.setattr(seed_api, "get", fake_get)
    monkeypatch.setattr(seed_api, "post", lambda path, payload: posted.append(path) or (False, {"detail": "already exists"}))
    seed_api.main()
    assert "/bookings/" not in posted
    assert "not adding more" in capsys.readouterr().out


def test_seeder_adds_bookings_to_an_empty_database_and_with_force(monkeypatch):
    rooms = [{"name": r["name"], "id": f"id{i}"} for i, r in enumerate(seed_api.ROOMS)]
    state = {"bookings": []}

    def fake_get(path):
        return (True, rooms) if path == "/rooms/" else (True, state["bookings"])

    def fake_post(path, payload):
        if path == "/bookings/":
            state["bookings"].append(payload)
        return True, {}

    monkeypatch.setattr(seed_api, "get", fake_get)
    monkeypatch.setattr(seed_api, "post", fake_post)
    seed_api.main()
    first = len(state["bookings"])
    assert first == 60
    seed_api.main()                      # already seeded: nothing added
    assert len(state["bookings"]) == first
    seed_api.main(force=True)            # explicitly asked: another batch
    assert len(state["bookings"]) == first + 60

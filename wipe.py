# wipe.py
# Deletes EVERY room and booking from the database named in .env. There is no undo.
#
#   python wipe.py          asks you to type the database name before deleting anything
#   python wipe.py --yes    skips the question (for scripts; be sure the .env is the database you mean)
import os
import sys

from dotenv import load_dotenv
from pymongo import MongoClient


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)

    load_dotenv()
    mongo_url = os.getenv("MONGODB_URI")
    db_name = os.getenv("DB_NAME", "room_mgmt")

    if not mongo_url:
        raise RuntimeError("Missing MONGODB_URI")

    client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[db_name]

    rooms = db["rooms"].count_documents({})
    bookings = db["bookings"].count_documents({})
    # Only the database NAME is shown, never the connection string (it contains the password).
    print(f"Database: {db_name}")
    print(f"This will delete {rooms} room(s) and {bookings} booking(s). It cannot be undone.")

    if "--yes" not in args:
        answer = input(f'Type the database name ("{db_name}") to confirm: ').strip()
        if answer != db_name:
            print("Cancelled. Nothing was deleted.")
            return 1

    db["rooms"].delete_many({})
    db["bookings"].delete_many({})
    print("🧹 Cleared rooms & bookings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

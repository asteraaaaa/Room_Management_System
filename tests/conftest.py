"""
Test setup. The real app connects to MongoDB Atlas using the credential in .env. Tests must never do that,
so before anything imports the app:

  * MONGODB_URI is set to a dummy value. load_dotenv() does not override a variable that is already set,
    so the real value in .env is never loaded.
  * pymongo.MongoClient is replaced by mongomock, an in-memory MongoDB stand-in.
"""
import os
import sys
from pathlib import Path

os.environ["MONGODB_URI"] = "mongodb://fake-host/"
os.environ["DB_NAME"] = "rms_test"

import mongomock  # noqa: E402
import pymongo  # noqa: E402

pymongo.MongoClient = mongomock.MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

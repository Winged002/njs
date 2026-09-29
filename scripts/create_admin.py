import getpass
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from werkzeug.security import generate_password_hash
from db import users, ensure_indexes

ensure_indexes()
username = input("Username: ").strip().lower()
email = input("Email (optional): ").strip() or None
password = getpass.getpass("Password: ")
if len(password) < 12:
    raise SystemExit("Use a password of at least 12 characters.")

existing = users.find_one({"username": username})
doc = {
    "username": username,
    "email": email,
    "password_hash": generate_password_hash(password),
    "credits": 10000,
    "created_at": datetime.now(timezone.utc),
}
if existing:
    users.update_one({"_id": existing["_id"]}, {"$set": {
        "email": email or existing.get("email"),
        "password_hash": doc["password_hash"],
    }})
    print("Existing user password updated.")
else:
    users.insert_one(doc)
    print("Admin user created.")

"""User accounts for the web app: sign-up, log-in, sessions and saved ratings.

Stored in a small SQLite database (Python standard library only). Passwords are
hashed with PBKDF2 and never stored in plain text.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

# Vercel only allows writing to /tmp; locally the database sits next to the app.
DB_PATH = Path("/tmp/plotline.db") if os.environ.get("VERCEL") else Path(__file__).parent / "plotline.db"

# Demo accounts: each one logs in as a real reader from the Goodbooks-10k data.
DEMO_ACCOUNTS = [
    {"key": "fantasy", "name": "Aarav", "taste": "Fantasy & young adult fan", "dataset_user_id": 314},
    {"key": "mystery", "name": "Priya", "taste": "Mystery & thriller reader", "dataset_user_id": 11920},
    {"key": "classics", "name": "Sam", "taste": "Classics & children's books", "dataset_user_id": 16735},
    {"key": "nonfiction", "name": "Meera", "taste": "Non-fiction & memoir lover", "dataset_user_id": 28694},
]

_lock = threading.Lock()


def _connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def _db():
    """One locked transaction; the connection is always closed afterwards."""
    with _lock:
        con = _connect()
        try:
            with con:
                yield con
        finally:
            con.close()


def init_db():
    with _db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                salt TEXT,
                dataset_user_id INTEGER,
                genres TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ratings (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                book_id INTEGER NOT NULL,
                rating REAL NOT NULL,
                PRIMARY KEY (user_id, book_id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        for d in DEMO_ACCOUNTS:
            con.execute(
                "INSERT OR IGNORE INTO users (name, email, dataset_user_id) VALUES (?, ?, ?)",
                (d["name"], f"{d['key']}@demo.plotline", d["dataset_user_id"]),
            )


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def _public(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "is_demo": row["dataset_user_id"] is not None,
        "dataset_user_id": row["dataset_user_id"],
        "genres": json.loads(row["genres"]),
    }


def _new_session(con, user_id):
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    return token


def sign_up(name, email, password):
    """Returns (token, user), or raises ValueError if the email is taken."""
    salt = secrets.token_hex(16)
    with _db() as con:
        try:
            cur = con.execute(
                "INSERT INTO users (name, email, password_hash, salt) VALUES (?, ?, ?, ?)",
                (name.strip(), email.strip().lower(), _hash(password, salt), salt),
            )
        except sqlite3.IntegrityError:
            raise ValueError("An account with this email already exists")
        token = _new_session(con, cur.lastrowid)
        row = con.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return token, _public(row)


def log_in(email, password):
    """Returns (token, user), or None if the email or password is wrong."""
    with _db() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        if not row or not row["password_hash"]:
            return None
        if not secrets.compare_digest(_hash(password, row["salt"]), row["password_hash"]):
            return None
        return _new_session(con, row["id"]), _public(row)


def log_in_demo(key):
    with _db() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (f"{key}@demo.plotline",)).fetchone()
        if not row:
            return None
        return _new_session(con, row["id"]), _public(row)


def log_out(token):
    with _db() as con:
        con.execute("DELETE FROM sessions WHERE token = ?", (token,))


def user_for_token(token):
    with _db() as con:
        row = con.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE token = ?", (token,)
        ).fetchone()
        return _public(row) if row else None


def set_genres(user_id, genres):
    with _db() as con:
        con.execute("UPDATE users SET genres = ? WHERE id = ?", (json.dumps(genres), user_id))


def get_ratings(user_id):
    """{book_id: stars} for a user's saved ratings."""
    with _db() as con:
        rows = con.execute("SELECT book_id, rating FROM ratings WHERE user_id = ?", (user_id,)).fetchall()
        return {r["book_id"]: r["rating"] for r in rows}


def set_rating(user_id, book_id, rating):
    """Saves a rating; a rating of 0 removes it."""
    with _db() as con:
        if rating:
            con.execute(
                "INSERT INTO ratings (user_id, book_id, rating) VALUES (?, ?, ?) "
                "ON CONFLICT (user_id, book_id) DO UPDATE SET rating = excluded.rating",
                (user_id, book_id, rating),
            )
        else:
            con.execute("DELETE FROM ratings WHERE user_id = ? AND book_id = ?", (user_id, book_id))

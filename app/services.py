"""
FieldData AI - Stage 2: business logic (accounts, sessions, projects).
Kept separate from the web layer so it can be tested without a web server.
"""
import re
import sqlite3
import time

from . import security

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FAILED, WINDOW_SECONDS = 5, 600
_failed = {}          # (ip, email) -> list of failure timestamps (in memory; resets on restart)


class ServiceError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _clean(text, field, max_len, required=False):
    text = (text or "").strip()
    if required and not text:
        raise ServiceError(422, f"{field} is required.")
    if len(text) > max_len:
        raise ServiceError(422, f"{field} must be at most {max_len} characters.")
    return text


def _public(user):
    return {"id": user["id"], "name": user["name"], "email": user["email"]}


# ---- accounts and sessions ----------------------------------------------------------------
def register(conn, name, email, password):
    name = _clean(name, "Name", 100, required=True)
    email = _clean(email, "Email", 254, required=True).lower()
    if not EMAIL_RE.match(email):
        raise ServiceError(422, "Please enter a valid email address.")
    if not isinstance(password, str) or not (8 <= len(password) <= 128):
        raise ServiceError(422, "Password must be 8 to 128 characters.")
    try:
        cur = conn.execute("INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                           (name, email, security.hash_password(password)))
        conn.commit()
    except sqlite3.IntegrityError:
        raise ServiceError(409, "An account with this email already exists.")
    return {"id": cur.lastrowid, "name": name, "email": email}


def login(conn, email, password, client_ip="unknown"):
    email = (email or "").strip().lower()
    key, now = (client_ip, email), time.time()
    recent = [t for t in _failed.get(key, []) if now - t < WINDOW_SECONDS]
    _failed[key] = recent
    if len(recent) >= MAX_FAILED:
        raise ServiceError(429, "Too many failed attempts. Please wait a few minutes and try again.")
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    # verify even when the user does not exist, so timing does not reveal which emails are registered
    stored = row["password_hash"] if row else security.hash_password("dummy-password")
    ok = security.verify_password(password if isinstance(password, str) else "", stored)
    if not row or not ok:
        _failed[key].append(now)
        raise ServiceError(401, "Incorrect email or password.")
    _failed.pop(key, None)
    return _public(row)


def create_session(conn, user_id):
    token = security.new_token()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                 (security.token_hash(token), user_id, time.time() + security.SESSION_DAYS * 86400))
    conn.commit()
    return token


def user_from_token(conn, token):
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
        (security.token_hash(token), time.time())).fetchone()
    return _public(row) if row else None


def end_session(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (security.token_hash(token),))
        conn.commit()


# ---- projects (every query is limited to the signed-in user's own projects) --------------------
def create_project(conn, user_id, name, description="", location=""):
    name = _clean(name, "Project name", 100, required=True)
    description = _clean(description, "Description", 1000)
    location = _clean(location, "Location", 200)
    cur = conn.execute("INSERT INTO projects (user_id, name, description, location) VALUES (?, ?, ?, ?)",
                       (user_id, name, description, location))
    conn.commit()
    return get_project(conn, user_id, cur.lastrowid)


def list_projects(conn, user_id):
    rows = conn.execute("SELECT * FROM projects WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def get_project(conn, user_id, project_id):
    row = conn.execute("SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)).fetchone()
    if not row:
        raise ServiceError(404, "Project not found.")      # same answer for 'missing' and 'not yours'
    return dict(row)


def delete_project(conn, user_id, project_id):
    get_project(conn, user_id, project_id)
    conn.execute("DELETE FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id))
    conn.commit()

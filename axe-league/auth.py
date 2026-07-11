"""Authentication: first-run setup, login roles, and access decorators.

Roles:
  admin       - full access
  scorekeeper - can score/edit matches only
  viewer      - read-only (no password)
"""
import os
import secrets
import sqlite3
from functools import wraps

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db as dbmod
from db import get_db

ROLES = ("admin", "scorekeeper")


def ensure_schema():
    """Idempotent migration: make sure the users table exists (also on
    databases created before auth was added)."""
    conn = sqlite3.connect(dbmod.DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
               role TEXT PRIMARY KEY,
               password_hash TEXT NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS round_dates (
               season_id INTEGER NOT NULL
                   REFERENCES seasons(id) ON DELETE CASCADE,
               round INTEGER NOT NULL,
               date TEXT NOT NULL,
               PRIMARY KEY (season_id, round)
           )"""
    )
    conn.commit()
    conn.close()


def load_secret_key():
    """Persistent Flask session key stored next to the database."""
    path = os.path.join(os.path.dirname(dbmod.DB_PATH), "secret_key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as f:
        return f.read().strip()


def setup_done(db=None):
    db = db or get_db()
    n = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    return n >= len(ROLES)


def set_password(db, role, password):
    db.execute(
        "INSERT INTO users (role, password_hash) VALUES (?, ?)"
        " ON CONFLICT(role) DO UPDATE SET password_hash=excluded.password_hash",
        (role, generate_password_hash(password)))


def check_password(db, role, password):
    row = db.execute("SELECT password_hash FROM users WHERE role=?",
                     (role,)).fetchone()
    return bool(row) and check_password_hash(row["password_hash"], password)


def current_role():
    return session.get("role")


def _deny():
    if request.path.startswith("/api/"):
        return jsonify({"ok": False,
                        "error": "You don't have permission to do that."}), 403
    return redirect(url_for("login", next=request.path))


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if current_role() != "admin":
            return _deny()
        return f(*args, **kwargs)
    return wrapper


def scorekeeper_required(f):
    """Admin or scorekeeper."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if current_role() not in ("admin", "scorekeeper"):
            return _deny()
        return f(*args, **kwargs)
    return wrapper

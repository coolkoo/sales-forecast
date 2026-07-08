"""Authentication + RBAC.

Users with roles, PBKDF2-hashed passwords, opaque session tokens (HttpOnly
cookie). Roles map to permissions; the API middleware enforces them. Seeds a
demo user per role on first run.

Roles → permissions:
  admin   : view · operate · admin   (settings, LLM/alert config, user mgmt)
  manager : view · operate           (run pipeline, sync, act tools)
  analyst : view                     (read analytics/reports)
  viewer  : view

NOTE: served over plain HTTP on the LAN — credentials travel in cleartext.
Put a TLS reverse proxy in front for production (same posture as traderific).
"""
from __future__ import annotations

import datetime
import hashlib
import secrets

from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, text

from app import db

_MD = MetaData()
_users = Table("app_user", _MD, Column("username", String, primary_key=True),
               Column("salt", String), Column("pw_hash", String), Column("role", String),
               Column("store", String), Column("active", Boolean), Column("created", DateTime))
_sess = Table("app_session", _MD, Column("token", String, primary_key=True),
              Column("username", String), Column("expires", DateTime))

ROLES = {"admin": ["view", "operate", "admin"], "manager": ["view", "operate"],
         "analyst": ["view"], "viewer": ["view"]}
_DEMO = [("admin", "admin"), ("manager", "manager"), ("analyst", "analyst"), ("viewer", "viewer")]


def _hash(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()


def _now():
    return datetime.datetime.utcnow()


def ensure():
    _MD.create_all(db.engine(), tables=[_users, _sess], checkfirst=True)
    if int(db.read_sql("SELECT COUNT(*) c FROM app_user")["c"].iloc[0]) == 0:
        with db.engine().begin() as cx:
            for u, role in _DEMO:
                salt = secrets.token_hex(8)
                cx.execute(_users.insert(), {"username": u, "salt": salt, "pw_hash": _hash(u, salt),
                                             "role": role, "store": None, "active": True, "created": _now()})


def authenticate(username: str, password: str) -> tuple[str, str | None]:
    """Returns (status, token). status: ok | bad | nouser | pending."""
    ensure()
    r = db.read_sql("SELECT * FROM app_user WHERE username=:u", {"u": username})
    if not len(r):
        return ("nouser", None)
    row = r.iloc[0].to_dict()
    if _hash(password, str(row["salt"])) != str(row["pw_hash"]):
        return ("bad", None)
    if not bool(row.get("active")):
        return ("pending", None)
    token = secrets.token_urlsafe(24)
    with db.engine().begin() as cx:
        cx.execute(_sess.insert(), {"token": token, "username": username,
                                    "expires": _now() + datetime.timedelta(hours=12)})
    return ("ok", token)


def signup(username: str, password: str) -> dict:
    """Self-registration → a PENDING viewer account (an admin activates it)."""
    ensure()
    username = (username or "").strip()
    if not username or not password:
        return {"error": "Username and password are required"}
    if len(password) < 4:
        return {"error": "Password must be at least 4 characters"}
    if not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        return {"error": "Username may only contain letters, numbers, _ - ."}
    if len(db.read_sql("SELECT 1 FROM app_user WHERE username=:u", {"u": username})):
        return {"error": "That username is already taken"}
    salt = secrets.token_hex(8)
    with db.engine().begin() as cx:
        cx.execute(_users.insert(), {"username": username, "salt": salt, "pw_hash": _hash(password, salt),
                                     "role": "viewer", "store": None, "active": False, "created": _now()})
    return {"ok": True, "pending": True}


def user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    ensure()
    r = db.read_sql("SELECT s.username u, us.role r, us.store st FROM app_session s "
                    "JOIN app_user us ON s.username=us.username "
                    "WHERE s.token=:t AND s.expires > :now AND us.active",
                    {"t": token, "now": _now()})
    if not len(r):
        return None
    row = r.iloc[0]
    role = str(row["r"])
    store = row["st"]
    return {"username": str(row["u"]), "role": role, "permissions": ROLES.get(role, ["view"]),
            "store": None if store is None or str(store) == "None" else str(store)}


def logout(token: str | None):
    if token:
        with db.engine().begin() as cx:
            cx.execute(text("DELETE FROM app_session WHERE token=:t"), {"t": token})


def list_users() -> list[dict]:
    ensure()
    df = db.read_sql("SELECT username, role, store, active FROM app_user ORDER BY username")
    df["active"] = df["active"].astype(bool)
    return df.to_dict("records")


def upsert_user(username: str, role: str, password: str | None = None,
                store: str | None = None, active: bool = True) -> dict:
    ensure()
    if role not in ROLES:
        return {"error": "invalid role"}
    if not username:
        return {"error": "username required"}
    exists = len(db.read_sql("SELECT 1 FROM app_user WHERE username=:u", {"u": username}))
    with db.engine().begin() as cx:
        if exists:
            cx.execute(text("UPDATE app_user SET role=:r, store=:s, active=:a WHERE username=:u"),
                       {"r": role, "s": store or None, "a": bool(active), "u": username})
            if password:
                salt = secrets.token_hex(8)
                cx.execute(text("UPDATE app_user SET salt=:sa, pw_hash=:h WHERE username=:u"),
                           {"sa": salt, "h": _hash(password, salt), "u": username})
        else:
            if not password:
                return {"error": "password required for a new user"}
            salt = secrets.token_hex(8)
            cx.execute(_users.insert(), {"username": username, "salt": salt, "pw_hash": _hash(password, salt),
                                         "role": role, "store": store or None, "active": bool(active), "created": _now()})
    return {"ok": True, "username": username}


def delete_user(username: str) -> dict:
    if username == "admin":
        return {"error": "cannot delete the admin user"}
    with db.engine().begin() as cx:
        cx.execute(text("DELETE FROM app_user WHERE username=:u"), {"u": username})
        cx.execute(text("DELETE FROM app_session WHERE username=:u"), {"u": username})
    return {"ok": True}

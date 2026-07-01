"""Email/password auth: bcrypt hashing + JWT session cookies.

Accounts require a database (see db.py). Auth is optional at the app level:
anonymous users can still create listings; logged-in users get durable,
per-account listing history.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from typing import Optional

import bcrypt
import jwt
from fastapi import Request, Response

from . import config, db

COOKIE_NAME = "thryft_session"
TOKEN_TTL_DAYS = 30


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:  # noqa: BLE001
        return False


def _make_token(user_id: str) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + _dt.timedelta(days=TOKEN_TTL_DAYS)}
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def set_session_cookie(response: Response, user_id: str, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        _make_token(user_id),
        max_age=TOKEN_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def current_user(request: Request) -> Optional[dict]:
    """Return the logged-in user dict, or None. Never raises."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
        return db.get_user_by_id(payload.get("sub", ""))
    except Exception:  # noqa: BLE001 - expired/invalid token -> treat as anonymous
        return None


def signup(email: str, password: str) -> Optional[dict]:
    """Create a user; returns the user dict or None if the email is taken."""
    return db.create_user(uuid.uuid4().hex[:16], email.strip().lower(), hash_password(password))


def login(email: str, password: str) -> Optional[dict]:
    """Verify credentials; returns the user dict (no hash) or None."""
    rec = db.get_user_by_email(email.strip().lower())
    if not rec or not verify_password(password, rec.get("password_hash", "")):
        return None
    return {"id": rec["id"], "email": rec["email"], "created_at": rec.get("created_at")}

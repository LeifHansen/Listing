"""Signing up, in and out: the session doors (/api/auth).

The session itself — the token, the cookie, the password hash — is
backend/auth.py; these are the routes onto it. /api/auth/connect-ticket is
not here: it starts a marketplace connect flow, and stays in main.py with
the rest of that flow.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from .. import auth, db
from ..config import log
from . import deps

router = APIRouter()


@router.post("/api/auth/signup")
def auth_signup(request: Request, response: Response, payload: dict) -> dict:
    deps.rate_limit_auth(request, "signup")
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not db.enabled():
        raise HTTPException(400, "Accounts require a database (set DATABASE_URL).")
    user = auth.signup(email, password)
    if user is db.EMAIL_TAKEN:
        raise HTTPException(409, "An account with that email already exists")
    if not user:
        raise HTTPException(
            503, "Account service is temporarily unavailable (database error). "
                 "Please try again shortly.")
    auth.set_session_cookie(response, user["id"], secure=request.url.scheme == "https")
    return {"user": user, "token": auth.make_token(user["id"])}


@router.post("/api/auth/login")
def auth_login(request: Request, response: Response, payload: dict) -> dict:
    deps.rate_limit_auth(request, "login")
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not db.enabled():
        raise HTTPException(400, "Accounts require a database (set DATABASE_URL).")
    # A database that cannot be reached raises StorageUnavailable out of
    # auth.login, and the central handler answers 503. That used to be read
    # off the status CACHE here instead, which is refreshed every ten
    # seconds: for the gap after an outage began a right password was
    # "wrong", and for the gap after it ended a wrong one was "the database
    # is down". None from auth.login now means exactly refused.
    user = auth.login(email, password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    auth.set_session_cookie(response, user["id"], secure=request.url.scheme == "https")
    return {"user": user, "token": auth.make_token(user["id"])}


@router.post("/api/auth/logout")
def auth_logout(response: Response) -> dict:
    """Sign out THIS browser. The token itself stays valid until it expires —
    see /api/auth/logout-everywhere for the one that cancels it."""
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.post("/api/auth/logout-everywhere")
def auth_logout_everywhere(request: Request, response: Response) -> dict:
    """Cancel every session token this account has, including this one.

    Clearing the cookie ends nothing for anyone else holding a copy of the
    token: it is self-contained and good for 30 days. A shared or borrowed
    device, a browser profile left signed in, a token out of a backup or a
    log — all of them kept working, and the seller had no way to end it. This
    is that way.

    It raises rather than reporting a failure as success (db.revoke_sessions
    is strict). Telling someone their other sessions are gone when the write
    never landed is the worst outcome available: they stop looking, and
    whoever holds the token keeps it.
    """
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    db.revoke_sessions(user["id"])
    # This browser too. Anything else would leave the seller looking at a
    # screen that says everything is signed out while it demonstrably is not.
    auth.clear_session_cookie(response)
    log.info("sessions revoked: user=%s", user["id"])
    return {"ok": True,
            "message": "Signed out everywhere. Sign in again to keep using "
                       "Thryft Shop on this device."}


@router.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    return {"user": auth.current_user(request)}

"""Connect tickets — the short-lived credential the native shell rides when a
full-page OAuth navigation can't carry the Bearer header or a cookie. The
whole point is that a leaked one is nearly useless: wrong purpose, expired, or
tampered must all fail.
"""
from __future__ import annotations

from backend import auth


def test_ticket_roundtrip():
    t = auth.make_ticket("user-1", "connect")
    assert auth.verify_ticket(t, "connect") == "user-1"


def test_ticket_purpose_is_load_bearing():
    """A connect ticket must not authenticate anything else."""
    t = auth.make_ticket("user-1", "connect")
    assert auth.verify_ticket(t, "delete-account") is None


def test_expired_ticket_rejected():
    t = auth.make_ticket("user-1", "connect", ttl_seconds=-5)
    assert auth.verify_ticket(t, "connect") is None


def test_garbage_and_tampering_rejected():
    assert auth.verify_ticket("", "connect") is None
    assert auth.verify_ticket("not.a.jwt", "connect") is None
    t = auth.make_ticket("user-1", "connect")
    assert auth.verify_ticket(t[:-4] + "AAAA", "connect") is None


def test_ticket_is_not_a_session_token():
    """The 30-day session JWT and the 60-second ticket must not be
    interchangeable in either direction."""
    session = auth.make_token("user-1")
    assert auth.verify_ticket(session, "connect") is None


def _request_with(authz: str):
    """A bare Starlette request carrying one Authorization header -- what a
    native client sends, and what someone holding a leaked ticket would."""
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"authorization", authz.encode())]})


def test_a_ticket_is_not_accepted_as_a_session(monkeypatch):
    """The other direction. The ticket rides a redirect's query string, so it
    lands in browser history and the access log; current_user used to decode
    it like any session token, which made it a 60-second login that could
    mint its own successor every minute."""
    from backend import db
    monkeypatch.setattr(db, "get_user_by_id",
                        lambda uid: {"id": uid, "email": "a@b.c"})
    ticket = auth.make_ticket("user-1", "connect")
    assert auth.current_user(_request_with(f"Bearer {ticket}")) is None
    # and the real session token still works through the same door
    session = auth.make_token("user-1")
    assert auth.current_user(_request_with(f"Bearer {session}"))["id"] == "user-1"

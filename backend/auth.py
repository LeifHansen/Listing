"""Email/password auth: bcrypt hashing + JWT session cookies.

Accounts require a database (see db.py). Auth is optional at the app level:
anonymous users can still create listings; logged-in users get durable,
per-account listing history.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
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


# The hash a login checks against when there is no account for that address.
#
# `login` used to return before bcrypt ran when the email was unknown, so a
# failed attempt cost ~270ms for an address that HAS an account here and
# essentially nothing for one that does not. The message was correctly
# identical either way; the clock was the answer. That is a legible oracle
# over any network, on an endpoint built to take an email and a guess -- and
# what it confirms is which sellers have a connected eBay identity, their
# photos and their listings behind one password, which is exactly the list a
# credential-stuffing run wants first.
#
# So the work happens either way. Built at import from the same gensalt() the
# real hashes use, so the cost factor tracks whatever this app hashes with
# rather than being pinned to a number that would drift; of a token no request
# can carry, because anything a caller could supply and match here would log
# them in as nobody. It costs one bcrypt per unknown-email attempt, which the
# auth rate limiter already bounds -- and that limiter counts every attempt,
# successful or not, precisely so this cost is capped.
_ABSENT_PASSWORD_HASH = bcrypt.hashpw(
    b"\x00 no account -- see verify_password", bcrypt.gensalt()).decode()


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


def make_token(user_id: str) -> str:
    """Public: mint a session token (for native clients using Bearer auth)."""
    return _make_token(user_id)


def current_user(request: Request) -> Optional[dict]:
    """Return the logged-in user dict, or None.

    Accepts either the session cookie (web) or an `Authorization: Bearer`
    header (native/mobile clients).

    None means "no valid session": no token, an expired or garbled one, or a
    revoked one. It does NOT mean "we could not check" — that raises
    StorageUnavailable, which the app answers as a 503.

    The distinction is the whole point. Every ownership check, every uid, and
    every logged-in screen ends here, so a swallowed read failure did not
    surface as an error; it logged the seller out. The store came back as the
    logged-out pitch, the notifications bell reported nothing sold, eBay
    reported itself disconnected without the account ever being read, and
    writes failed as 404s because the record "belonged to someone else".

    A caller with no token, or one whose token does not decode, never reaches
    storage — so the logged-out flows keep working through an outage, and only
    the requests that were being silently downgraded get the 503.
    """
    # Memoized per request: handlers call this several times (uid checks,
    # creds building, ownership asserts) and each call was a full DB
    # round-trip to Neon — several serial cross-region queries per publish.
    if hasattr(request.state, "auth_user"):
        return request.state.auth_user
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        authz = request.headers.get("Authorization", "")
        if authz.startswith("Bearer "):
            token = authz[7:].strip()
    if not token:
        request.state.auth_user = None
        return None
    try:
        payload = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
    except Exception:  # noqa: BLE001 - expired/invalid token -> anonymous
        request.state.auth_user = None
        return None
    # Deliberately outside a catch-all, and deliberately before the memo is
    # written: a handler calls this several times, and recording a failure as
    # "anonymous" would hand the later calls the answer the first one refused.
    user = db.get_user_by_id(payload.get("sub", ""))
    try:
        revoked = user is not None and _revoked(payload, user)
    except Exception:  # noqa: BLE001 - unplaceable token -> not authenticated
        revoked = True
    # A disabled account's live sessions end at the same chokepoint a revoked
    # one's do. Checked after the revocation logic on purpose: this only ever
    # converts a VALID session to None, never a failure to None — the
    # StorageUnavailable path above is untouched.
    if user is not None and user.get("disabled_at"):
        revoked = True
    request.state.auth_user = None if revoked else user
    return request.state.auth_user


def _revoked(payload: dict, user: dict) -> bool:
    """Was this token issued before the account cancelled its sessions?

    The session JWT is self-contained and lives 30 days, so clearing the
    cookie ends nothing for anyone else holding a copy. `sessions_valid_from`
    is what "log out everywhere" writes down, and it is already on the user
    dict this request just fetched — no second round trip.

    Two deliberate refusals:

      - `iat` and the stamp are whole seconds, so a token minted in the same
        second as the revocation cannot be told from one minted just before
        it. `<=` refuses it. The alternative leaves a one-second hole in the
        control on the very request that follows the button;
      - a token with no `iat` cannot be placed relative to the stamp at all.
        Nothing this app mints lacks one, and "cannot tell" is not "allow".
    """
    cutoff = user.get("sessions_valid_from")
    if not cutoff:
        return False
    issued = payload.get("iat")
    if issued is None:
        return True
    if cutoff.tzinfo is None:
        # SQLite hands back naive datetimes; the stamp is always written UTC.
        cutoff = cutoff.replace(tzinfo=_dt.timezone.utc)
    return int(issued) <= int(cutoff.timestamp())


def make_ticket(user_id: str, purpose: str, ttl_seconds: int = 60) -> str:
    """A short-lived, single-purpose credential for a top-level NAVIGATION.

    The native shell authenticates with a Bearer header, but starting an OAuth
    connect flow is a full-page navigation — no header rides along, and the
    session cookie never crosses origins. Putting the real 30-day session JWT
    in a URL would park a long-lived credential in every access log, so the
    navigation instead carries this: 60 seconds, one declared purpose, useless
    for anything else.
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    payload = {"sub": user_id, "purpose": purpose, "iat": now,
               "exp": now + _dt.timedelta(seconds=ttl_seconds)}
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def verify_ticket(ticket: str, purpose: str) -> Optional[str]:
    """The user id from a valid, unexpired ticket minted for `purpose`."""
    try:
        payload = jwt.decode(ticket, config.SECRET_KEY, algorithms=["HS256"])
        if payload.get("purpose") != purpose:
            return None
        return payload.get("sub") or None
    except Exception:  # noqa: BLE001 - expired/garbled -> not authenticated
        return None


def _state_sig(user_id: str, nonce: str) -> str:
    msg = f"{user_id}.{nonce}".encode()
    return hmac.new(config.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:32]


def make_state(user_id: str, nonce: str) -> str:
    """Sign the OAuth state binding the user id to a per-request nonce.

    The nonce is also stored in a cookie on the browser that starts the flow;
    the callback requires the two to match, which prevents a CSRF login-binding
    attack (an attacker feeding a victim their own eBay authorization code).
    """
    return f"{user_id}.{nonce}.{_state_sig(user_id, nonce)}"


def verify_state(state: str) -> Optional[tuple[str, str]]:
    """Return (user_id, nonce) from a signed state, or None if missing/tampered."""
    parts = (state or "").split(".")
    if len(parts) != 3:
        return None
    user_id, nonce, sig = parts
    if not user_id or not nonce or not sig:
        return None
    if not hmac.compare_digest(sig, _state_sig(user_id, nonce)):
        return None
    return user_id, nonce


def signup(email: str, password: str):
    """Create a user; returns the user dict, db.EMAIL_TAKEN, or None on DB error."""
    return db.create_user(uuid.uuid4().hex[:16], email.strip().lower(), hash_password(password))


def login(email: str, password: str) -> Optional[dict]:
    """Verify credentials; returns the user dict (no hash) or None.

    The comparison runs whether or not the account exists -- see
    _ABSENT_PASSWORD_HASH. Deliberately not short-circuited on `rec`: doing so
    answers "is there an account for this address?" in the time it takes to
    fail, which is the one thing this endpoint's own error message is careful
    not to say.
    """
    rec = db.get_user_by_email(email.strip().lower())
    stored = (rec or {}).get("password_hash") or _ABSENT_PASSWORD_HASH
    if not verify_password(password, stored) or not rec:
        return None
    # A disabled account gets the same generic refusal as a wrong password.
    # Deliberately after bcrypt ran and deliberately not its own message:
    # naming the real reason (or answering in a different amount of time)
    # confirms the account exists, which is the one thing this endpoint's
    # error is careful not to say.
    if rec.get("disabled_at"):
        return None
    return {"id": rec["id"], "email": rec["email"],
            "display_name": rec.get("display_name", ""),
            "role": rec.get("role") or "user",
            "created_at": rec.get("created_at")}

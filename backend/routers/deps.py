"""Request helpers shared by main.py and the route modules beside this one.

What a handler needs from the request that belongs to no one area: who and
where the caller is, whether they own the listing they name, their eBay
credentials, the sign-in rate limit, the opaque page cursor, and the
superadmin gate with its audit trail. They live here, not in main.py,
because a route module cannot import main.py — main includes the routers,
so the reverse is an import cycle.

main.py's own handlers call these too. A test that swaps main's `db` for a
stand-in therefore swaps this module's as well, or the ownership check below
reads the real one (tests/test_a_patch_on_main_never_silently_misses.py).
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request

from .. import auth, db, ratelimit
from ..config import log
from ..marketplaces import ebay_provider
from ..services import errorlog


def uid(request: Request) -> Optional[str]:
    """The signed-in seller's id, or None for an anonymous caller."""
    user = auth.current_user(request)
    # The one choke point where the seller's id is already resolved. Doing
    # this in the request middleware instead would add a database read to
    # every asset fetch — and auth.current_user RAISES StorageUnavailable on
    # a database blip, which would turn one Neon hiccup into a failing
    # liveness probe on the only machine.
    errorlog.note_user(user["id"] if user else "")
    return user["id"] if user else None


def assert_session_owner(session_id: str, request: Request) -> None:
    """404 when this session's saved listing belongs to a DIFFERENT user.
    Session ids appear in media URLs and can leak, so possession of an id
    must not grant write access. Unsaved or unowned (anonymous) sessions
    pass — the app supports logged-out flows.

    Fails CLOSED on a database outage. This check is the only thing standing
    between a leaked session id and write access to someone else's photos,
    and it answers from the database — so if a read failure were treated like
    "no such listing", one Neon blip would quietly disable the guard on every
    session-scoped endpoint at once, while the rest of the app (on-disk
    sessions, /media) kept serving. A brief 503 is the right trade.
    """
    rec = db.get_listing_strict(session_id)
    if rec is db.UNAVAILABLE:
        raise HTTPException(
            503, "Can't verify who this listing belongs to right now — "
                 "please try again in a moment.")
    if rec and rec.get("user_id") and rec["user_id"] != uid(request):
        raise HTTPException(404, "Listing not found")


def ebay_creds_for(request: Request):
    """Build live eBay creds for the logged-in user, or None if not connected."""
    return ebay_provider.creds_for(uid(request))


def client_ip(request: Request) -> str:
    """The caller's IP. Fly puts the real client in Fly-Client-IP; uvicorn
    runs with --proxy-headers so request.client is already the forwarded
    address, but the explicit header is the one Fly guarantees."""
    return (request.headers.get("Fly-Client-IP")
            or (request.client.host if request.client else "?"))


def rate_limit_auth(request: Request, bucket: str) -> None:
    """429 when one client floods an auth endpoint (see backend/ratelimit)."""
    ip = client_ip(request)
    if not ratelimit.check(f"{bucket}:{ip}"):
        log.warning("auth: rate limited %s from %s", bucket, ip)
        raise HTTPException(
            429, "Too many attempts. Wait a few minutes and try again.")


def page_cursor(stamp: Optional[str], row_id: Optional[str]) -> Optional[str]:
    """The opaque token naming one row, for the page that follows it.

    Base64url of "<stamp>|<id>" — encoded so the timestamp's colons and
    offset sign survive a query string untouched, and opaque so nobody starts
    hand-assembling one. It is the server's own words handed back; every read
    it feeds is scoped exactly as it would be without it, so a cursor says
    WHERE to start and never whose rows to start in.

    None when the row cannot name a place in the order. A blank half would
    mint a token the next request rejects as malformed — a 400 in the middle
    of a walk somebody started. No cursor degrades honestly instead: the page
    still says it was cut, and the button that could not have worked is
    simply not offered.
    """
    if not stamp or not row_id:
        return None
    raw = f"{stamp}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def cursor_from(token: str) -> tuple[datetime, str]:
    """Parse one, or raise 400.

    Refused rather than ignored. An ignored cursor answers with page one,
    which the client reads as the listings that FOLLOW the ones it has — so
    the store looks like it ends where it began, which is the bug paging
    exists to fix, arriving through the fix.
    """
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad).decode()
        stamp, sep, last_id = raw.partition("|")
        if not sep or not last_id:
            raise ValueError("no separator")
        when = datetime.fromisoformat(stamp)
        # Same rule as everywhere else a stored timestamp is read: a naive one
        # is UTC, not local, or the comparison silently moves the page edge.
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when, last_id
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            400, "That listing page link is no longer valid — reload the page "
                 "to start from the top.") from exc


def require_superadmin(request: Request) -> dict:
    """The signed-in superadmin, or 404. Fail CLOSED.

    404 rather than 401/403, on purpose: (a) it does not confirm an admin
    surface exists to whoever is probing for one; (b) lib/api.js treats any
    401 as "session expired" and signs the caller out client-side — the
    wrong outcome for a curious logged-in seller who typed /api/admin into
    devtools. A database outage propagates as StorageUnavailable → 503, like
    every other authenticated route: "cannot check" is never "not an admin".
    The role is re-read from the user row on every request (current_user's
    per-request read), so revoking it takes effect immediately — there is no
    role claim inside the 30-day JWT to wait out.
    """
    user = auth.current_user(request)
    if not user or (user.get("role") or "") != "superadmin":
        raise HTTPException(404, "Not found")
    return user


def audit_admin(admin: dict, request: Request, action: str,
                target_type: str = "", target_id: str = "",
                data: Optional[dict] = None) -> str:
    """Write the audit row for an admin action, BEFORE the action runs.

    Raises (→ 503) when it cannot: an admin action that cannot be written
    down does not run. Returns the row id — token grants carry it in their
    ledger `ref`, so the two trails reconcile mechanically.
    """
    return db.admin_audit(admin, action, target_type=target_type,
                          target_id=target_id, ip=client_ip(request),
                          data=data)

"""Request helpers shared by main.py and the route modules beside this one.

What a handler needs from the request that belongs to no one area: where the
caller is, the sign-in rate limit, the opaque page cursor, and the superadmin
gate with its audit trail. They live here, not in main.py, because a route
module cannot import main.py — main includes the routers, so the reverse is
an import cycle.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request

from .. import auth, db, ratelimit
from ..config import log


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

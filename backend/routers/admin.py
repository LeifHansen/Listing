"""The operator console (/api/admin) and the machine doors (/api/ops).

Three doors onto operator data, each with its own credential:

- /api/admin/diagnostics: the shared ADMIN_TOKEN header, for curl and CI.
  It keeps working with the database down, which is when it is needed.
- The rest of /api/admin: the console, gated by users.role. It
  authenticates a PERSON, so every action is written down with a name on it
  (deps.audit_admin) before it runs.
- /api/ops: ERROR_FEED_TOKEN, for the daily error-triage job. A token that
  reads which bugs are open, and nothing else.

POST /api/admin/compliance/run is the one console route still in main.py:
the recovery passes it runs are main's own.
"""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from .. import config, db, errors, objstore, storage
from ..services import deletion_queue, errorlog, owed_refunds
from . import deps

router = APIRouter()


def _diagnostics() -> dict:
    """Everything an operator needs to tell "not configured" apart from
    "misconfigured". Served only from the two admin routes below."""
    return {
        "ok": True,
        # The commit actually running. A deploy can report success while the
        # image serving traffic is older (a poisoned builder cache has done
        # this here before), and without this the only way to tell was to
        # diff response shapes against git history and guess.
        "build": config.BUILD_SHA or "unknown",
        "anthropic_configured": config.anthropic_ready(),
        "google_ai_configured": config.google_ai_ready(),
        "identify_provider": config.identify_provider(),
        "ebay_configured": config.ebay_ready(),
        "ebay_missing": config.ebay_status()["missing"],
        "taxonomy_configured": config.taxonomy_ready(),
        "ebay_env": config.EBAY_ENV,
        "ebay_oauth_ready": config.ebay_oauth_ready(),
        "ebay_deletion_endpoint_ready": bool(config.EBAY_VERIFICATION_TOKEN),
        # Etsy, where "configured" is only half the answer: which of Etsy's
        # three access tiers the app is on decides how many shops may connect
        # at all, and the roster is how the operator seats them. Counts, never
        # the addresses — this is a diagnostics endpoint, not a place to hand
        # out the beta's email list to anyone holding the admin token.
        # `etsy_seats: 0` means no ceiling (Commercial Access); a roster
        # larger than the ceiling also gets its own config_warnings() line,
        # because the overflow is refused on Etsy's page rather than here.
        "etsy_configured": config.etsy_oauth_ready(),
        "etsy_access_tier": config.etsy_access_tier(),
        "etsy_seats": config.etsy_seat_ceiling(),
        "etsy_roster": len(config.ETSY_OWNER_EMAILS),
        "etsy_gate_active": config.etsy_gate_active(),
        # True when the two above disagree about who is protected: the tier
        # still restricts who may authorize, and an empty roster leaves the
        # gate inert — so every seller reaches Etsy's refusal page. Carries
        # its own config_warnings() line; reported here too because this is
        # the endpoint an operator opens when a seller says Connect is broken.
        "etsy_access_unverified": config.etsy_access_unverified(),
        # Photo storage: is the R2 bucket wired up — and if not, exactly which
        # pieces are missing (four credentials sat deployed for a week while a
        # bare `false` here hid that two more vars were expected) — plus how
        # much room is left on the volume (a full one breaks every upload).
        "objstore_configured": objstore.enabled(),
        "objstore_missing": config.r2_missing(),
        "objstore_bucket": config.R2_BUCKET if objstore.enabled() else None,
        "objstore_url_mode": (("public" if config.r2_public_urls() else "presigned")
                              if objstore.enabled() else None),
        "objstore_error": objstore.last_error(),
        "disk_free_mb": round(storage.disk_free_bytes() / 1e6),
        "storage": "r2" if objstore.enabled() else "local",
        # Monetization, reported like every other integration: whether metering
        # is actually on, what's still missing before money can move, and which
        # Stripe mode the keys are in — a test key on a production deploy
        # accepts nothing and otherwise looks identical to a working one.
        "tokens_enabled": config.tokens_enabled(),
        "tokens_missing": config.tokens_missing(),
        "stripe_live_mode": config.stripe_live_mode(),
        # Misconfigurations that look exactly like "not configured yet": a
        # secret set under a name one word off from the one the code reads, or
        # an on/off flag set to something that isn't on. Every `*_missing` list
        # above reports those two cases identically to never having set them,
        # which is how production ran with the paid tier off and a Stripe key
        # visibly deployed. [] means nothing adjacent was found.
        "config_warnings": config.config_warnings(),
        "db": db.db_status(),
        # Erasures this deployment still owes: photos whose account is already
        # deleted, and eBay account-deletion notices acknowledged but not yet
        # carried out. Both are promises already made to somebody, so a number
        # here that does not come back down is the alert. Counts only — the
        # ids belong to people who asked to be forgotten.
        "deletion_backlog": deletion_queue.backlog(),
        # Refunds that did not commit and are still owed. Like the deletion
        # backlog, a number here that does not come back down is a promise
        # already made to somebody — in this case, their money.
        "owed_refunds": owed_refunds.backlog(),
    }


def _token_matches(supplied: str, expected: str) -> bool:
    """Constant-time equality for a header-borne token.

    On bytes, not str: secrets.compare_digest raises TypeError for a str with
    a character outside ASCII, and Starlette hands headers over as latin-1
    text -- so one probe with a byte >= 0x80 in it was a 500 and an
    error_events row, where a wrong token is a 401.
    """
    return secrets.compare_digest(supplied.encode("utf-8", "replace"),
                                  expected.encode("utf-8", "replace"))


def _require_admin(request: Request) -> None:
    """Fail CLOSED: an unset ADMIN_TOKEN denies rather than admits.

    An absent secret reading as "no check required" is exactly how this
    endpoint would end up public again on a deploy that forgot to set it --
    which is the state it is being moved out of.
    """
    expected = (config.ADMIN_TOKEN or "").strip()
    supplied = (request.headers.get("x-admin-token") or "").strip()
    if not expected or not supplied or not _token_matches(supplied, expected):
        raise HTTPException(401, "Not authorised.")


@router.get("/api/admin/diagnostics")
def admin_diagnostics(request: Request) -> dict:
    """The deployment detail /api/health used to hand out anonymously."""
    _require_admin(request)
    return _diagnostics()


# --- superadmin console ------------------------------------------------------
#
# The operator console: cross-user reads and a handful of account actions,
# gated by users.role rather than the shared header token above. The two
# doors deliberately coexist: /api/admin/diagnostics keeps working with the
# database down (curl/CI), while everything below authenticates a PERSON,
# so every action can be written down with a name on it.
#
# Cross-user reads are the point here, so the ownership scan
# (tests/test_every_scoped_route_checks_the_owner.py) reads this module like
# main.py: a handler scoped to one listing must be guarded or exempted there
# with a reason, as admin_get_listing is.

@router.get("/api/admin/system")
def admin_system(request: Request) -> dict:
    """_diagnostics(), for the console's System tab. Same payload as
    /api/admin/diagnostics behind the session gate instead of the header."""
    deps.require_superadmin(request)
    return _diagnostics()


@router.get("/api/admin/overview")
def admin_overview(request: Request, days: int = 30) -> dict:
    """The platform KPIs plus the two obligation backlogs. Reads raise
    rather than answering zeros — the console renders "couldn't check"."""
    deps.require_superadmin(request)
    if days not in (7, 30, 90):
        days = 30
    kpis = db.admin_platform_kpis(days)
    kpis["deletion_backlog"] = deletion_queue.backlog()
    kpis["owed_refunds"] = owed_refunds.backlog()
    return kpis


@router.get("/api/admin/users")
def admin_users(request: Request, q: str = "", before: str = "",
                limit: int = 50) -> dict:
    deps.require_superadmin(request)
    limit = max(1, min(limit, 100))
    cursor = deps.cursor_from(before) if before else None
    # One row more than will be returned, so the answer can say whether it
    # is the whole list — same probe-row trade as /api/listings.
    rows = db.admin_list_users(limit=limit + 1, before=cursor, q=q)
    truncated = len(rows) > limit
    rows = rows[:limit]
    out = {"users": rows,
           "rollups": db.admin_user_rollups([u["id"] for u in rows]),
           "next_cursor": (deps.page_cursor(rows[-1].get("created_at"),
                                            rows[-1].get("id"))
                           if truncated and rows else None)}
    try:
        out["total"] = db.admin_count_users()
    except errors.StorageUnavailable:
        # The page is honest without it; a total must never be invented.
        pass
    return out


@router.get("/api/admin/users/{user_id}")
def admin_user_detail(user_id: str, request: Request) -> dict:
    deps.require_superadmin(request)
    detail = db.admin_get_user(user_id)
    if detail is None:
        raise HTTPException(404, "No such account.")
    return {"user": detail}


# The most an admin can hand out in one grant. Not a product limit — a
# typo guard: 1000000 where 1000 was meant is a real balance someone
# spends, and there is no undo that claws back what was already used.
_ADMIN_GRANT_CAP = 100_000


@router.post("/api/admin/users/{user_id}/grant-tokens")
def admin_grant_tokens(user_id: str, request: Request,
                       payload: Optional[dict] = None) -> dict:
    """Credit an account (a support goodwill, a refund made right). The
    ledger row's ref carries the audit row's id, and token_credit's unique
    ref makes a retried grant a no-op rather than a double credit."""
    admin = deps.require_superadmin(request)
    body = payload or {}
    try:
        amount = int(body.get("tokens"))
    except (TypeError, ValueError):
        raise HTTPException(400, "How many tokens? Send a whole number.")
    if not 1 <= amount <= _ADMIN_GRANT_CAP:
        raise HTTPException(
            400, f"Grants are 1 to {_ADMIN_GRANT_CAP} tokens.")
    note = str(payload.get("note") or "").strip()[:200]
    target = db.get_user_by_id(user_id)   # raises → 503 when unreadable
    if not target:
        raise HTTPException(404, "No such account.")
    audit_id = deps.audit_admin(admin, request, "grant_tokens", "user",
                                user_id, data={"tokens": amount, "note": note})
    res = db.token_credit(user_id, amount, ref=f"admin:{audit_id}",
                          kind="grant",
                          note=note or f"granted by {admin['email']}")
    if res is None:
        raise HTTPException(
            503, "The grant was recorded but could not be applied — it was "
                 "NOT credited. Try again in a moment.")
    return {"ok": True, "granted": amount,
            "already": bool(res.get("already"))}


@router.post("/api/admin/users/{user_id}/revoke-sessions")
def admin_revoke_sessions(user_id: str, request: Request) -> dict:
    """Force-sign-out one account everywhere (a stolen token, a support
    request). db.revoke_sessions is strict, so success here means the write
    landed."""
    admin = deps.require_superadmin(request)
    target = db.get_user_by_id(user_id)   # raises → 503 when unreadable
    if not target:
        raise HTTPException(404, "No such account.")
    deps.audit_admin(admin, request, "revoke_sessions", "user", user_id)
    db.revoke_sessions(user_id)
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/disable")
def admin_set_disabled(user_id: str, request: Request,
                       payload: Optional[dict] = None) -> dict:
    """Lock an account out ({"disabled": true}) or back in (false).

    Two refusals: your own account (locking yourself out of the console
    that unlocks accounts), and another superadmin (demote them with
    scripts/grant_superadmin.py --revoke first, so removing an operator is
    a deliberate, audited, out-of-band step rather than a console click).
    Disabling also revokes sessions: the lockout must reach tokens that are
    already minted, not just the next login.
    """
    admin = deps.require_superadmin(request)
    body = payload or {}
    disabled = body.get("disabled")
    if not isinstance(disabled, bool):
        raise HTTPException(400, 'Send {"disabled": true} or false.')
    if user_id == admin["id"]:
        raise HTTPException(400, "You can't disable your own account.")
    target = db.get_user_by_id(user_id)   # raises → 503 when unreadable
    if not target:
        raise HTTPException(404, "No such account.")
    if (target.get("role") or "") == "superadmin":
        raise HTTPException(
            400, "That account is a superadmin — revoke its role first "
                 "(scripts/grant_superadmin.py --revoke).")
    deps.audit_admin(admin, request,
                     "disable_account" if disabled else "enable_account",
                     "user", user_id)
    updated = db.set_user_disabled(user_id, disabled)
    if updated is None:
        raise HTTPException(404, "No such account.")
    if disabled:
        db.revoke_sessions(user_id)
    return {"ok": True, "disabled_at": updated.get("disabled_at")}


@router.get("/api/admin/listings")
def admin_listings(request: Request, q: str = "", status: str = "",
                   user_id: str = "", before: str = "",
                   limit: int = 50) -> dict:
    deps.require_superadmin(request)
    limit = max(1, min(limit, 100))
    cursor = deps.cursor_from(before) if before else None
    rows = db.admin_list_listings(limit=limit + 1, before=cursor, q=q,
                                  status=status, user_id=user_id)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"listings": rows,
            "next_cursor": (deps.page_cursor(rows[-1].get("updated_at"),
                                             rows[-1].get("id"))
                            if truncated and rows else None)}


@router.get("/api/admin/listings/{listing_id}")
def admin_get_listing(listing_id: str, request: Request) -> dict:
    """One listing in full, whoever owns it — the read-only detail behind a
    row in the console's cross-user browse. See the ownership test's EXEMPT
    entry: cross-user is the point here, and the gate above is the check."""
    deps.require_superadmin(request)
    rec = db.get_listing_strict(listing_id)
    if rec is db.UNAVAILABLE:
        raise HTTPException(
            503, "Couldn't read that listing just now. Try again in a "
                 "moment.")
    if rec is None:
        raise HTTPException(404, "Listing not found")
    return rec


@router.get("/api/admin/ledger")
def admin_ledger_view(request: Request, kind: str = "", user_id: str = "",
                      before: str = "", limit: int = 50) -> dict:
    deps.require_superadmin(request)
    limit = max(1, min(limit, 200))
    cursor = deps.cursor_from(before) if before else None
    rows = db.admin_ledger(limit=limit + 1, before=cursor, kind=kind,
                           user_id=user_id)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"entries": rows,
            "next_cursor": (deps.page_cursor(rows[-1].get("created_at"),
                                             rows[-1].get("id"))
                            if truncated and rows else None)}


@router.get("/api/admin/compliance")
def admin_compliance(request: Request) -> dict:
    """The two obligation queues. The counts raise on a read failure (a zero
    here is a claim that nothing is owed), so an outage 503s the tab rather
    than rendering 'Nothing owed' over queue rows nobody could read."""
    deps.require_superadmin(request)
    return {
        "deletion_backlog": db.count_pending_deletion_notices(),
        "media_purge_backlog": db.count_pending_media_purges(),
        "deletion_notices": db.pending_deletion_notices(100),
        "media_purges": db.pending_media_purges(100),
    }


def _require_error_feed(request: Request) -> None:
    """The triage job's door. Fails CLOSED, like _require_admin.

    A twin of _require_admin rather than a reuse of it, reading its own
    ERROR_FEED_TOKEN. The distinction is the point: ADMIN_TOKEN also opens
    /api/admin/diagnostics, which reports raw database and object-store
    exception text — the Neon host, the role, the R2 account. A scheduled job
    that reads which bugs are open has no business holding that, and a
    credential in CI is the one most likely to leak.
    """
    expected = (config.ERROR_FEED_TOKEN or "").strip()
    supplied = (request.headers.get("x-error-feed-token") or "").strip()
    if not expected or not supplied or not _token_matches(supplied, expected):
        raise HTTPException(401, "Not authorised.")


def _error_report(before: str = "", limit: int = 50, since_hours: int = 0,
                  min_severity: str = "", include_resolved: bool = True
                  ) -> dict:
    """The distinct failures, newest-seen first. Shared by both doors below.

    `sink` rides along because a queue that is dropping rows would otherwise
    look exactly like a quiet day — the most dangerous thing a monitor can
    do. It is the lesson check_health.py's docstring records, one layer down:
    an alarm that cannot tell "nothing happened" from "I could not see" is
    worse than no alarm.
    """
    limit = max(1, min(limit, 200))
    cursor = deps.cursor_from(before) if before else None
    rows = db.error_events_list(limit=limit + 1, before=cursor,
                                since_hours=since_hours,
                                min_severity=min_severity,
                                include_resolved=include_resolved)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"errors": rows,
            "sink": errorlog.stats(),
            "next_cursor": (deps.page_cursor(rows[-1].get("last_seen"),
                                             rows[-1].get("id"))
                            if truncated and rows else None)}


@router.get("/api/admin/errors")
def admin_errors(request: Request, before: str = "", limit: int = 50,
                 since_hours: int = 0, severity: str = "",
                 include_resolved: bool = True) -> dict:
    """The console's Errors tab. Session-gated, like the rest of the console."""
    deps.require_superadmin(request)
    return _error_report(before=before, limit=limit, since_hours=since_hours,
                         min_severity=severity,
                         include_resolved=include_resolved)


@router.get("/api/ops/error-feed")
def ops_error_feed(request: Request, limit: int = 50, since_hours: int = 36,
                   severity: str = "") -> dict:
    """The same report, for the daily triage job. Token-gated.

    Two doors onto one payload, exactly as /api/admin/system and
    /api/admin/diagnostics already coexist: the session door authenticates a
    PERSON, which is right for the console and wrong for a robot that would
    have to hold a human's long-lived session to use it.

    Under /api/ops rather than /api/admin, and that is not cosmetic.
    test_every_console_route_is_gated walks app.routes and requires EVERY
    /api/admin/ path to answer 404 to a non-superadmin — "the next admin route
    is born tested". It carries exactly one exception, /api/admin/diagnostics,
    described in its own docstring as the older door. Adding two more would
    turn a guardrail that cannot be forgotten into a list somebody maintains,
    which is how the next unreviewed cross-user read ships. Machine doors get
    their own prefix instead, and the console's guarantee stays absolute.

    Defaults to a 36-hour window rather than 24: the job runs on a daily cron,
    and a calendar-day read drops anything that happened in the seam between
    one run and the next. Overlap costs nothing, because the fingerprint
    dedupes.
    """
    _require_error_feed(request)
    return _error_report(limit=limit, since_hours=since_hours,
                         min_severity=severity, include_resolved=False)


@router.post("/api/ops/errors/{fingerprint}/fixed")
def ops_error_fixed(fingerprint: str, request: Request,
                    payload: Optional[dict] = None) -> dict:
    """Mark a failure as having a fix proposed, so the job stops proposing one.

    Token-gated, and under /api/ops for the reason the feed above gives. It
    is never cleared automatically — if the bug returns, `last_seen` moves and
    the row surfaces again on its own, which is a fact rather than a guess
    about whether the fix worked.
    """
    _require_error_feed(request)
    pr = str((payload or {}).get("pr") or "")[:200]
    return {"ok": db.mark_error_fixed(fingerprint, pr)}


@router.get("/api/admin/audit")
def admin_audit_view(request: Request, before: str = "",
                     limit: int = 50) -> dict:
    deps.require_superadmin(request)
    limit = max(1, min(limit, 200))
    cursor = deps.cursor_from(before) if before else None
    rows = db.admin_audit_list(limit=limit + 1, before=cursor)
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"entries": rows,
            "next_cursor": (deps.page_cursor(rows[-1].get("created_at"),
                                             rows[-1].get("id"))
                            if truncated and rows else None)}

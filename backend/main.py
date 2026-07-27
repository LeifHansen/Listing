"""FastAPI application wiring the eBay listing pipeline together.

Pipeline:
  upload images -> optimize (Pillow) -> identify (Claude vision) ->
  edit/refine in preview -> publish (eBay, or dry-run).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, ebay_auth, ebay_errors, objstore, storage
from .config import log
from .models import (ItemSpecific, Listing, PublishRequest, RefineRequest,
                     SessionOnlyRequest)
from .services import (claude_ai, ebay, ebay_trading, images, listing_sync,
                       metrics, preflight, pricing, promotions, recommender,
                       taxonomy)

app = FastAPI(title="eBay Listing Generator")

# The frontend is a Vite/React app; serve its build output. (The Dockerfile
# builds it in a node stage; run.sh builds it for local dev.)
FRONTEND_DIR = config.ROOT_DIR / "frontend" / "dist"


@app.middleware("http")
async def _cache_headers(request: Request, call_next):
    """Cache policy: index.html must always revalidate so a deploy is visible
    on the next load, while Vite's content-hashed /assets/ bundles are immutable
    and can cache forever; /media images are content-stable, so let browsers
    keep them a bit (the UI cache-busts edited photos with ?v=)."""
    response = await call_next(request)
    path = request.url.path
    ctype = response.headers.get("content-type", "")
    if path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/media/"):
        response.headers.setdefault("Cache-Control", "public, max-age=3600")
    elif ctype.startswith(("text/html", "text/css")) or "javascript" in ctype:
        response.headers["Cache-Control"] = "no-cache"
    return response


def _sweep_orphans() -> None:
    """Reclaim volume space: delete session dirs on disk that aren't real
    listings (leftover bulk staging + abandoned uploads). Skipped entirely when
    the DB is unavailable, so live listings' images can never be mistaken for
    orphans."""
    ids = db.all_listing_ids()
    if ids is None:  # no DB / read failed — don't risk deleting real images
        return
    removed = storage.sweep_orphan_sessions(ids, max_age_seconds=3 * 3600)
    if removed:
        log.info("startup: swept %d orphaned session dir(s) to reclaim space", removed)


@app.on_event("startup")
def _warm_models() -> None:
    """Startup daemons (don't block uvicorn binding the port): warm the in-house
    background-removal model, and sweep orphaned session dirs off the volume."""
    import threading

    threading.Thread(target=images.warm, daemon=True).start()
    threading.Thread(target=_sweep_orphans, daemon=True).start()


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "anthropic_configured": config.anthropic_ready(),
        "ebay_configured": config.ebay_ready(),
        "ebay_missing": config.ebay_status()["missing"],
        "taxonomy_configured": config.taxonomy_ready(),
        "ebay_env": config.EBAY_ENV,
        "ebay_oauth_ready": config.ebay_oauth_ready(),
        "ebay_deletion_endpoint_ready": bool(config.EBAY_VERIFICATION_TOKEN),
        # adobe_configured = credentials present; adobe_ready = pipeline can
        # actually run (Adobe's APIs need R2 as presigned-URL hand-off storage).
        "adobe_configured": config.adobe_configured(),
        "adobe_ready": config.adobe_ready(),
        "photoroom_configured": config.photoroom_ready(),
        "storage": "r2" if objstore.enabled() else "local",
        "db": db.db_status(),
    }


def _category_query(listing) -> str:
    parts = [listing.brand, listing.title, listing.category_suggestion]
    return " ".join(p for p in parts if p).strip()


def _fill_category_specifics(listing: Listing, image_paths: list) -> int:
    """Best-effort: fill eBay's category item specifics (required + recommended)
    from the photos and merge them in without overwriting anything already set.
    Returns how many were added. NEVER raises — a listing must still save and
    publish if this enrichment fails.

    This runs server-side during identify (single + bulk) so listings come
    SEO-ready even on the bulk 'list live now' path, which publishes straight
    after identify and would otherwise reach eBay with only the generic
    specifics from the first vision pass (the 'specifics not populating' bug)."""
    if not (config.taxonomy_ready() and config.anthropic_ready()):
        return 0
    if not listing.category_id:
        return 0
    try:
        aspects = taxonomy.item_aspects(listing.category_id).get("aspects", [])
        paths = [p for p in image_paths if p.is_file()]
        if not aspects or not paths:
            return 0
        filled = claude_ai.fill_aspects(paths, listing, aspects)
    except Exception as exc:  # noqa: BLE001 - enrichment is optional
        log.info("specifics enrich skipped (cat=%s): %s", listing.category_id, exc)
        return 0
    have = {s.name.strip().lower() for s in listing.item_specifics if s.value.strip()}
    added = 0
    for f in filled:
        if f.name.strip().lower() not in have:
            listing.item_specifics.append(f)
            have.add(f.name.strip().lower())
            added += 1
    if added:
        log.info("specifics enrich: cat=%s added=%d", listing.category_id, added)
    return added


# Aspect names that mean "who made this". A wrong maker is worse than a blank
# one, so these only get filled by the double-layer check below.
_MAKER_ASPECT_NAMES = {"brand", "maker", "manufacturer"}
# Placeholder values that mean the maker is effectively unknown.
_GENERIC_MAKERS = {"", "unbranded", "unknown", "generic", "n/a", "none",
                   "no brand", "handmade", "does not apply"}


def _fill_maker(listing: Listing, image_paths: list) -> bool:
    """Best-effort maker/manufacturer identification (double-layer check).

    The generic identify pass is told never to guess, so Brand / Maker /
    Manufacturer are rarely filled. When they're missing, run the dedicated
    two-layer ID in claude_ai.identify_maker (hunt, then adversarial verify —
    like a reverse-image lookup with a second opinion) and only write a maker
    both layers agree on. NEVER raises. Returns True if anything was set."""
    if not config.anthropic_ready():
        return False
    brand_missing = (listing.brand or "").strip().lower() in _GENERIC_MAKERS
    have = {s.name.strip().lower() for s in listing.item_specifics if s.value.strip()}
    # Maker-ish aspects this category defines that are still empty.
    unfilled: list[str] = []
    try:
        if listing.category_id and config.taxonomy_ready():
            for a in taxonomy.item_aspects(listing.category_id).get("aspects", []):
                name = (a.get("name") or "").strip()
                if name.lower() in _MAKER_ASPECT_NAMES and name.lower() not in have:
                    unfilled.append(name)
    except Exception:  # noqa: BLE001 - aspects are optional context here
        pass
    if not (brand_missing or unfilled):
        return False  # maker already known — don't burn two vision calls
    try:
        paths = [p for p in image_paths if p.is_file()]
        found = claude_ai.identify_maker(paths, listing)
    except Exception as exc:  # noqa: BLE001 - enrichment is optional
        log.info("maker id skipped: %s", exc)
        return False
    if not found:
        return False
    maker = found["maker"]
    if brand_missing:
        listing.brand = maker
    for name in unfilled:
        listing.item_specifics.append(ItemSpecific(name=name, value=maker))
    # The maker is settled now — drop stale "verify the brand" style nags.
    listing.missing_info = [m for m in listing.missing_info
                            if not any(w in m.lower()
                                       for w in ("brand", "maker", "manufacturer"))]
    log.info("maker id: '%s' confirmed (%s) — evidence: %s",
             maker, found.get("confidence"), (found.get("evidence") or "")[:120])
    return True


def _uid(request: Request):
    user = auth.current_user(request)
    return user["id"] if user else None


def _assert_session_owner(session_id: str, request: Request) -> None:
    """404 when this session's saved listing belongs to a DIFFERENT user.
    Session ids appear in media URLs and can leak, so possession of an id
    must not grant write access. Unsaved or unowned (anonymous) sessions
    pass — the app supports logged-out flows."""
    rec = db.get_listing(session_id)
    if rec and rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")


def _in_background(fn, *args, what: str = "") -> None:
    """Run fn(*args) on a daemon thread — for mirror/bookkeeping work (R2
    pushes/deletes, updated_at bumps, directory cleanup) that shouldn't hold
    up the response. The user-visible change is already done locally by the
    time this runs; failures are logged, never surfaced."""
    def _run() -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - background work is best-effort
            log.warning("background %s failed: %s",
                        what or getattr(fn, "__name__", "task"), exc)
    threading.Thread(target=_run, daemon=True).start()


def _purge_session_images(session_id: str) -> None:
    """Delete a session's photos (local disk + R2) to reclaim storage once the
    listing is archived (sold). Keeps the DB record. Best-effort, never raises;
    eBay still hosts the images on the sold listing itself."""
    try:
        if objstore.enabled():
            for n in storage.list_optimized(session_id):
                try:
                    objstore.delete(objstore.key_for(session_id, n))
                except Exception:  # noqa: BLE001 - keep purging the rest
                    pass
        d = storage.session_dir(session_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - archiving must never fail on cleanup
        log.warning("archive: image purge failed for %s: %s", session_id, exc)


# --- auth ------------------------------------------------------------------

@app.post("/api/auth/signup")
def auth_signup(request: Request, response: Response, payload: dict) -> dict:
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


@app.post("/api/auth/login")
def auth_login(request: Request, response: Response, payload: dict) -> dict:
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not db.enabled():
        raise HTTPException(400, "Accounts require a database (set DATABASE_URL).")
    user = auth.login(email, password)
    if not user:
        if not db.db_status().get("connected"):
            raise HTTPException(
                503, "Account service is temporarily unavailable (database "
                     "error). Please try again shortly.")
        raise HTTPException(401, "Invalid email or password")
    auth.set_session_cookie(response, user["id"], secure=request.url.scheme == "https")
    return {"user": user, "token": auth.make_token(user["id"])}


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict:
    auth.clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    return {"user": auth.current_user(request)}


# --- eBay connect (Sign in with eBay) --------------------------------------

def _ebay_creds_for(request: Request):
    """Build live eBay creds for the logged-in user, or None if not connected."""
    uid = _uid(request)
    if not uid:
        return None
    acct = db.get_ebay_account(uid)
    if not acct or not acct.get("refresh_token"):
        return None
    try:
        fresh = ebay_auth.refresh_access_token(acct["refresh_token"])
    except Exception as exc:  # noqa: BLE001 - fall back to dry-run, but log it
        # A token-refresh outage otherwise looks identical to "not connected"
        # and silently dry-runs a live publish; log so it's debuggable.
        log.warning(f"ebay: token refresh failed for user {uid}: {exc}")
        return None
    return {
        "access_token": fresh["access_token"],
        "fulfillment_policy_id": acct.get("fulfillment_policy_id", ""),
        "payment_policy_id": acct.get("payment_policy_id", ""),
        "return_policy_id": acct.get("return_policy_id", ""),
        "merchant_location_key": acct.get("merchant_location_key", ""),
        "ship_from_postal": acct.get("ship_from_postal", ""),
        "_uid": uid,
    }


EBAY_NONCE_COOKIE = "ebay_oauth_nonce"


@app.get("/api/ebay/connect")
def ebay_connect(request: Request):
    if not config.ebay_oauth_ready():
        raise HTTPException(400, "eBay OAuth not configured (EBAY_CLIENT_ID/SECRET/RUNAME).")
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in before connecting eBay.")
    import secrets as _secrets
    nonce = _secrets.token_urlsafe(24)
    resp = RedirectResponse(ebay_auth.authorize_url(state=auth.make_state(uid, nonce)))
    # Bind the flow to this browser: the callback requires this cookie to match
    # the nonce embedded in the signed state (CSRF protection). Lax so it rides
    # the top-level redirect back from eBay.
    resp.set_cookie(EBAY_NONCE_COOKIE, nonce, max_age=600, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https")
    return resp


@app.get("/api/ebay/callback")
def ebay_callback(request: Request, code: str = "", state: str = ""):
    verified = auth.verify_state(state)
    if not code or not verified:
        return RedirectResponse("/?ebay=error")
    uid, nonce = verified
    # The nonce in the signed state must match the cookie set at connect time,
    # so a callback can only bind an eBay account to the browser that started
    # the flow (blocks CSRF authorization-code injection).
    cookie_nonce = request.cookies.get(EBAY_NONCE_COOKIE, "")
    if not cookie_nonce or cookie_nonce != nonce:
        log.warning("ebay callback: nonce mismatch (uid=%s)", uid)
        return RedirectResponse("/?ebay=error")
    try:
        tokens = ebay_auth.exchange_code(code)
        access = tokens["access_token"]
        policies = ebay_auth.fetch_policies_and_location(access)
        # Record WHICH eBay account this is, so the user can confirm they
        # connected the right one (best-effort — never block connect on it).
        ident = {"username": "", "email": ""}
        try:
            ident = ebay_auth.identity_display(ebay_auth.fetch_user_identity(access))
        except Exception as exc:  # noqa: BLE001
            log.warning(f"ebay: identity fetch failed on connect: {exc}")
        # Preserve the user's saved policy/location choices when reconnecting the
        # SAME account (or when identity is unreadable — don't risk clobbering
        # good settings). Only a switch to a DIFFERENT account takes the
        # auto-discovered defaults fresh, since the old account's policy ids
        # can't be reused. This is why a reconnect used to silently revert
        # shipping to eBay Standard Envelope.
        existing = db.get_ebay_account(uid) or {}
        new_user = ident["username"]
        keep_saved = (not new_user) or existing.get("ebay_username") == new_user
        save_kwargs = {
            "refresh_token": tokens["refresh_token"],
            "ebay_username": ident["username"],
            "ebay_email": ident["email"],
        }
        if keep_saved:
            for k, v in policies.items():
                if v and not existing.get(k):
                    save_kwargs[k] = v  # fill only the gaps
        else:
            save_kwargs.update(policies)
        db.save_ebay_account(uid, **save_kwargs)
        resp = RedirectResponse("/?ebay=connected")
        resp.delete_cookie(EBAY_NONCE_COOKIE)
        return resp
    except Exception:  # noqa: BLE001
        return RedirectResponse("/?ebay=error")


@app.get("/api/ebay/status")
def ebay_status(request: Request) -> dict:
    uid = _uid(request)
    acct = db.get_ebay_account(uid) if uid else None
    connected = bool(acct and acct.get("refresh_token"))
    # Which server-side OAuth vars are absent (names only, never values) — so
    # "the button does nothing" is diagnosable from the UI instead of guessed.
    oauth_missing = [name for name, val in (
        ("EBAY_CLIENT_ID", config.EBAY_CLIENT_ID),
        ("EBAY_CLIENT_SECRET", config.EBAY_CLIENT_SECRET),
        ("EBAY_RUNAME", config.EBAY_RUNAME),
    ) if not val]
    return {
        "oauth_ready": config.ebay_oauth_ready(),
        "oauth_missing": oauth_missing,
        "connected": connected,
        "env": config.EBAY_ENV,
        # Which eBay account is linked (empty for connections made before the
        # identity scope was added — reconnecting fills it in).
        "username": (acct.get("ebay_username") or "") if connected else "",
        "email": (acct.get("ebay_email") or "") if connected else "",
        "policies": {
            "fulfillment": bool(acct and acct.get("fulfillment_policy_id")),
            "payment": bool(acct and acct.get("payment_policy_id")),
            "return": bool(acct and acct.get("return_policy_id")),
            "location": bool(acct and acct.get("merchant_location_key")),
        } if connected else {},
    }


@app.get("/api/ebay/policies")
def get_ebay_policies(request: Request) -> dict:
    """The connected seller's eBay business policies + which ones are set as
    this account's defaults. These are eBay's 'templates' for shipping,
    payment, and returns; a listing's offer references them."""
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first to load your policies.")
    lists = ebay_auth.list_business_policies(creds["access_token"])
    acct = db.get_ebay_account(_uid(request)) or {}
    return {
        "policies": lists,
        "selected": {
            "fulfillment_policy_id": acct.get("fulfillment_policy_id", ""),
            "payment_policy_id": acct.get("payment_policy_id", ""),
            "return_policy_id": acct.get("return_policy_id", ""),
        },
        "location_set": bool(acct.get("merchant_location_key")),
        "ship_from_postal": acct.get("ship_from_postal", ""),
        "manage_url": "https://www.bizpolicy.ebay.com/businesspolicy/manage",
    }


@app.get("/api/ebay/shipping-services")
def shipping_services() -> dict:
    """The catalog of eBay shipping services a seller can one-tap into a
    fulfillment policy (static; no auth needed)."""
    return {"services": ebay_auth.SHIPPING_SERVICES}


@app.post("/api/ebay/ensure-policy")
def ensure_policy(request: Request, payload: dict) -> dict:
    """Find — or create — a fulfillment policy for any catalog shipping
    service, and make it the account default if none is set yet."""
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    svc = ebay_auth.service_by_code(str(payload.get("service_code", "")))
    if not svc:
        raise HTTPException(400, "Unknown shipping service.")
    try:
        pol = ebay_auth.ensure_service_policy(creds["access_token"], svc)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502, f"eBay couldn't create the policy: {exc.response.text[:300]}") from exc
    if pol.get("id") and not creds.get("fulfillment_policy_id"):
        db.save_ebay_account(creds["_uid"], fulfillment_policy_id=pol["id"])
    return pol


def _preflight_issues(request: Request, listing: Listing, mode: str) -> list[dict]:
    """Run the full pre-publish checklist for this user's account state."""
    creds = _ebay_creds_for(request)
    connected = bool(creds) or config.ebay_ready()
    if creds:
        fulfillment = listing.fulfillment_policy_id or creds.get("fulfillment_policy_id") or ""
        has_payment = bool(creds.get("payment_policy_id"))
        has_return = bool(creds.get("return_policy_id"))
        has_location = bool(creds.get("merchant_location_key"))
    else:
        fulfillment = listing.fulfillment_policy_id or config.EBAY_FULFILLMENT_POLICY_ID or ""
        has_payment = bool(config.EBAY_PAYMENT_POLICY_ID)
        has_return = bool(config.EBAY_RETURN_POLICY_ID)
        has_location = bool(config.EBAY_MERCHANT_LOCATION_KEY)

    # What the chosen shipping policy actually ships with (per-service weight
    # caps are the classic silent publish killer, e.g. Standard Envelope's 3 oz).
    services = (ebay_auth.fulfillment_policy_services(creds["access_token"], fulfillment)
                if creds and fulfillment else [])

    required = None
    if config.taxonomy_ready() and (listing.category_id or "").strip().isdigit():
        try:
            asp = taxonomy.item_aspects(listing.category_id)
            required = [a["name"] for a in asp.get("aspects", []) if a.get("required")]
        except Exception:  # noqa: BLE001 - aspects are a best-effort check
            required = None

    return preflight.validate(
        listing, mode,
        has_fulfillment=bool(fulfillment), has_payment=has_payment,
        has_return=has_return, has_location=has_location, connected=connected,
        policy_services=services, required_aspects=required)


@app.post("/api/publish-preflight")
async def publish_preflight(req: PublishRequest, request: Request) -> dict:
    """The full 'ready to publish?' checklist, without touching the listing."""
    issues = await run_in_threadpool(
        _preflight_issues, request, req.listing, req.mode or "live")
    return {"ok": not preflight.errors_only(issues), "issues": issues}


@app.get("/api/ebay/account-overview")
def ebay_account_overview(request: Request) -> dict:
    """A live mirror of the seller's most-updated eBay account settings —
    business policies (with the current defaults), ship-from locations, opted-in
    programs, and managed-payments status. Best-effort; {connected: false} when
    eBay isn't linked."""
    creds = _ebay_creds_for(request)
    if not creds or not creds.get("access_token"):
        return {"connected": False}
    try:
        ov = ebay_auth.account_overview(creds["access_token"])
    except Exception as exc:  # noqa: BLE001 - never fail the page
        log.warning("account-overview failed: %s", exc)
        ov = {}
    acct = db.get_ebay_account(_uid(request)) or {}
    ov["connected"] = True
    ov["account"] = {
        "username": acct.get("ebay_username", ""),
        "email": acct.get("ebay_email", ""),
        "marketplace": config.EBAY_MARKETPLACE_ID,
    }
    ov["selected"] = {
        "fulfillment_policy_id": creds.get("fulfillment_policy_id", ""),
        "payment_policy_id": creds.get("payment_policy_id", ""),
        "return_policy_id": creds.get("return_policy_id", ""),
        "merchant_location_key": creds.get("merchant_location_key", ""),
    }
    return ov


@app.post("/api/ebay/policies")
def set_ebay_policies(request: Request, payload: dict) -> dict:
    """Save the account's default shipping/payment/return policy selections and
    (optionally) a ship-from ZIP, which we use to create the eBay inventory
    location that publishing requires."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    fields = {
        k: str(payload.get(k) or "")
        for k in ("fulfillment_policy_id", "payment_policy_id", "return_policy_id")
        if k in payload
    }
    postal = str(payload.get("ship_from_postal") or "").strip()
    if postal:
        creds = _ebay_creds_for(request)
        if not creds:
            raise HTTPException(400, "Connect eBay first to set a ship-from location.")
        try:
            key = ebay_auth.ensure_inventory_location(creds["access_token"], postal)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                400, f"eBay rejected that ship-from location: {exc}") from exc
        fields["merchant_location_key"] = key
        fields["ship_from_postal"] = postal
    if not fields:
        raise HTTPException(400, "No settings provided.")
    db.save_ebay_account(uid, **fields)
    return {"ok": True, "selected": fields}


@app.get("/api/profile")
def get_profile(request: Request) -> dict:
    """The logged-in user's profile + eBay connection summary for Settings."""
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    acct = db.get_ebay_account(user["id"]) or {}
    connected = bool(acct.get("refresh_token"))
    return {
        "user": {"email": user["email"],
                 "display_name": user.get("display_name", "")},
        "ebay": {
            "connected": connected,
            "username": acct.get("ebay_username", "") if connected else "",
            "email": acct.get("ebay_email", "") if connected else "",
            "ship_from_postal": acct.get("ship_from_postal", ""),
            "policies_set": bool(acct.get("fulfillment_policy_id")
                                 and acct.get("payment_policy_id")
                                 and acct.get("return_policy_id")),
            "location_set": bool(acct.get("merchant_location_key")),
        },
    }


@app.post("/api/profile")
def save_profile(request: Request, payload: dict) -> dict:
    """Save profile customizations (currently: display name)."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    display_name = str(payload.get("display_name", "")).strip()[:80]
    updated = db.update_user(uid, display_name=display_name)
    if not updated:
        raise HTTPException(503, "Couldn't save your profile — try again shortly.")
    return {"ok": True, "user": {"email": updated["email"],
                                 "display_name": updated["display_name"]}}


# Prefs the client may set, with sane bounds. Everything is optional; empty
# string / 0 means "no default".
_PREF_FIELDS = {
    "package_weight_lb": (float, 0, 150),
    "package_weight_oz": (float, 0, 15.9),
    "package_length_in": (float, 0, 120),
    "package_width_in": (float, 0, 120),
    "package_height_in": (float, 0, 120),
    "quantity": (int, 1, 999),
    "condition": (str, None, None),  # "" = let the AI decide
    # How the AI prices drafts and comp suggestions: "quick_flip" (low end,
    # sell fast), "median" (typical market), "long_sale" (high end, patient).
    "pricing_strategy": (str, None, None),
    # Promote every newly published listing at eBay's recommended ad rate.
    # Missing = ON (see _auto_promote_enabled); 0 turns it off.
    "auto_promote": (int, 0, 1),
}
_PRICING_STRATEGIES = {"", "quick_flip", "median", "long_sale"}


@app.get("/api/prefs")
def get_prefs(request: Request) -> dict:
    """The user's new-listing defaults (weight/dims/quantity/condition)."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    return {"prefs": db.get_prefs(uid)}


@app.post("/api/prefs")
def save_prefs(request: Request, payload: dict) -> dict:
    """Save new-listing defaults. Only known fields are stored, clamped to
    sane ranges; they pre-fill every future AI draft."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    clean: dict = {}
    for key, (typ, lo, hi) in _PREF_FIELDS.items():
        if key not in payload:
            continue
        raw = payload.get(key)
        if typ is str:
            value = str(raw or "").strip()[:40]
            if key == "pricing_strategy" and value not in _PRICING_STRATEGIES:
                continue  # unknown strategy — ignore rather than store garbage
            clean[key] = value
            continue
        try:
            val = typ(float(raw or 0))
        except (TypeError, ValueError):
            val = typ(0) if typ is float else lo
        clean[key] = min(max(val, lo), hi) if val else val
    if not clean:
        raise HTTPException(400, "No settings provided.")
    merged = db.save_prefs(uid, clean)
    if not merged and not db.enabled():
        raise HTTPException(503, "No database configured — defaults need DATABASE_URL set.")
    return {"ok": True, "prefs": merged}


def _auto_promote_enabled(uid: Optional[str]) -> bool:
    """Account default: promote every newly published listing at eBay's
    recommended ad rate. ON unless explicitly turned off in Settings — sellers
    reported publishes landing unpromoted and only discovering it later from
    the Dashboard nags. Anonymous/env-token publishes stay explicit-only."""
    if not uid:
        return False
    try:
        value = db.get_prefs(uid).get("auto_promote")
    except Exception:  # noqa: BLE001 - prefs are optional
        return True
    return True if value is None else bool(value)


def _promote(record_id: str, listing: Listing, creds: Optional[dict],
             rate: Optional[float] = None,
             ebay_listing_id: Optional[str] = None) -> dict:
    """Turn Promoted Listings on for one listing and run the ad call.

    The single place that decides an ad rate: an explicit `rate` wins, else
    eBay's recommendation for `ebay_listing_id`, else the default. A 0% rate
    makes promote_listing silently no-op — which is exactly why listings with
    Promote toggled on were never actually promoted — so the rate is always
    filled in here. Mutates listing.promote/ad_rate_percent so the caller can
    persist what really ran, and never raises: a promotion problem must not
    fail the publish that preceded it."""
    try:
        listing.promote = True
        if not rate or rate <= 0:
            rate = None
            if ebay_listing_id:
                recommended = promotions.suggested_ad_rates(
                    creds, [str(ebay_listing_id)])
                rate = recommended.get(str(ebay_listing_id))
        listing.ad_rate_percent = round(rate or promotions.DEFAULT_AD_RATE, 1)
        return promotions.promote_listing(record_id, listing, creds)
    except Exception as exc:  # noqa: BLE001 - promotion must never break publish
        log.warning("promote failed (%s): %s", record_id, exc)
        return {"promoted": False, "message": f"Promotion failed: {exc}"}


def _pricing_strategy(uid: Optional[str]) -> str:
    """The account's pricing strategy ("" when unset/anonymous). Never raises."""
    if not uid:
        return ""
    try:
        value = str(db.get_prefs(uid).get("pricing_strategy") or "")
        return value if value in _PRICING_STRATEGIES else ""
    except Exception:  # noqa: BLE001 - prefs are optional
        return ""


def _apply_listing_defaults(listing: Listing, uid: Optional[str]) -> Listing:
    """Fill gaps in a fresh AI draft from the user's saved defaults — the
    fields the photos can't tell us (package weight/dims, quantity) plus an
    explicit condition override. Never touches a field the AI populated."""
    if not uid:
        return listing
    prefs = db.get_prefs(uid)
    if not prefs:
        return listing
    def _f(key):  # noqa: E306
        try:
            return float(prefs.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
    if (listing.package_weight_lb or 0) + (listing.package_weight_oz or 0) <= 0 \
            and _f("package_weight_lb") + _f("package_weight_oz") > 0:
        listing.package_weight_lb = _f("package_weight_lb")
        listing.package_weight_oz = _f("package_weight_oz")
    dims = ("package_length_in", "package_width_in", "package_height_in")
    if all((getattr(listing, d) or 0) <= 0 for d in dims) \
            and all(_f(d) > 0 for d in dims):
        for d in dims:
            setattr(listing, d, _f(d))
    if int(prefs.get("quantity") or 0) > 1 and (listing.quantity or 1) <= 1:
        listing.quantity = int(prefs["quantity"])
    # Condition is an explicit "always use this" override (the Settings UI
    # defaults it to 'Let the AI decide' = empty).
    if (prefs.get("condition") or "").strip():
        listing.condition = str(prefs["condition"]).strip()
    return listing


@app.post("/api/profile/sync-ebay")
def sync_profile_from_ebay(request: Request) -> dict:
    """Auto-pull profile info from the connected eBay account: identity
    (username/email), business policies, and inventory location."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    access = creds["access_token"]
    fields: dict = {}
    try:
        ident = ebay_auth.identity_display(ebay_auth.fetch_user_identity(access))
        fields["ebay_username"] = ident["username"]
        fields["ebay_email"] = ident["email"]
    except Exception as exc:  # noqa: BLE001 - identity scope may be missing
        log.info("profile sync: identity fetch failed for %s: %s", uid, exc)
    # Only fill policy/location gaps — never overwrite explicit selections.
    acct = db.get_ebay_account(uid) or {}
    discovered = ebay_auth.fetch_policies_and_location(access)
    for key, val in discovered.items():
        if val and not acct.get(key):
            fields[key] = val
    if fields:
        db.save_ebay_account(uid, **fields)
    # Default the display name to the eBay username if none is set yet.
    user = auth.current_user(request) or {}
    if fields.get("ebay_username") and not user.get("display_name"):
        db.update_user(uid, display_name=fields["ebay_username"])
    return get_profile(request)


@app.post("/api/ebay/disconnect")
def ebay_disconnect(request: Request) -> dict:
    """Unlink the current user's eBay account so they can connect a different
    one (or the correct one, if the wrong account got linked)."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    # Keep saved policy/location prefs so reconnecting the same account restores
    # them; a different account overwrites them on connect (see the callback).
    db.disconnect_ebay_account(uid)
    return {"ok": True}


@app.get("/api/ebay/payments-status")
def ebay_payments_status(request: Request) -> dict:
    """Live check of the connected eBay account's payments onboarding.

    Answers "did my bank account link actually work?": eBay reports the
    account as OPTED_IN to the payments program once payout setup is done.
    """
    if not _uid(request):
        raise HTTPException(401, "Log in first.")
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "eBay is not connected for this account.")
    try:
        program = ebay_auth.fetch_payments_program(creds["access_token"])
    except httpx.HTTPStatusError as exc:
        return {
            "env": config.EBAY_ENV,
            "opted_in": False,
            "error": f"eBay API error: {exc.response.status_code}",
            "detail": exc.response.text,
        }
    status = str(program.get("status", "")).upper()
    return {
        "env": config.EBAY_ENV,
        "status": status,
        "opted_in": status == "OPTED_IN",
        "program": program,
    }


# --- eBay marketplace account deletion notifications ------------------------
# eBay requires every *Production* keyset to expose this endpoint (developer
# portal -> Alerts & Notifications). eBay first validates it with a GET
# challenge, then POSTs a notification whenever an eBay user requests account
# deletion; we must ack with a 2xx.

def _deletion_endpoint_url(request: Request) -> str:
    """The endpoint URL eBay hashes: as registered in the portal, no query."""
    if config.EBAY_DELETION_ENDPOINT:
        return config.EBAY_DELETION_ENDPOINT
    return str(request.url.remove_query_params("challenge_code"))


@app.get("/api/ebay/account-deletion")
def ebay_account_deletion_challenge(request: Request, challenge_code: str = "") -> dict:
    """Answer eBay's endpoint-validation challenge.

    eBay calls GET <endpoint>?challenge_code=... and expects
    {"challengeResponse": sha256(challengeCode + verificationToken + endpointUrl)}.
    """
    if not config.EBAY_VERIFICATION_TOKEN:
        raise HTTPException(
            503,
            "EBAY_VERIFICATION_TOKEN is not set. Set it to the same value you "
            "entered on eBay's Alerts & Notifications page.",
        )
    if not challenge_code:
        raise HTTPException(400, "Missing challenge_code query parameter.")
    digest = hashlib.sha256(
        (challenge_code + config.EBAY_VERIFICATION_TOKEN
         + _deletion_endpoint_url(request)).encode("utf-8")
    ).hexdigest()
    return {"challengeResponse": digest}


@app.post("/api/ebay/account-deletion")
async def ebay_account_deletion_notice(request: Request) -> Response:
    """Acknowledge an account-deletion notification (and keep an audit copy).

    We key stored eBay connections by *our* user ids, not eBay usernames, so
    there is no per-user data to purge here — but the notification is recorded
    under data/exports/ so there's an audit trail of every notice received.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed body; ack anyway
        payload = {}
    notif_id = ((payload.get("notification") or {}).get("notificationId")
                or "unknown")
    try:
        storage.write_export(f"account-deletion-{notif_id}",
                             "ebay_notification", payload)
    except Exception as exc:  # noqa: BLE001 - never fail the ack
        log.warning(f"ebay: failed to record deletion notice: {exc}")
    return Response(status_code=200)


# Caps raised on request. A high bound stays so a pathological huge upload
# can't OOM the box; per-image dimension downscale (MAX_WORK_SIDE) bounds pixel
# memory regardless, and bulk upload has no count cap at all.
MAX_UPLOAD_FILES = 40   # per single listing (eBay itself accepts up to 24 live)
MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # per file


@app.post("/api/upload")
async def upload(
    files: list[UploadFile] = File(...),
    remove_bg: str = Form("false"),
) -> dict:
    """Accept images, optimize them, and return a session id.

    remove_bg: when "true", each photo's background is removed and replaced
    with a solid white canvas before the usual optimization pass.
    """
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"Too many files (max {MAX_UPLOAD_FILES} per listing)")

    strip_bg = str(remove_bg).lower() in ("true", "1", "yes", "on")

    session_id = storage.new_session_id()
    orig = storage.original_dir(session_id)
    for i, f in enumerate(files):
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                400, f"'{f.filename or 'image'}' is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB per image)")
        suffix = Path(f.filename or f"upload_{i}").suffix or ".jpg"
        (orig / f"src_{i:02d}{suffix}").write_bytes(data)

    # Pillow work is CPU-bound and the R2 push is blocking I/O; run both off
    # the event loop so photo processing doesn't stall every other request.
    opt_results = await run_in_threadpool(
        images.optimize_all, orig, storage.optimized_dir(session_id), strip_bg)
    optimized = storage.list_optimized(session_id)
    if not optimized:
        errs = "; ".join(r["error"] for r in opt_results if r.get("error"))
        raise HTTPException(
            400,
            "Could not process the uploaded image(s)"
            + (f": {errs}" if errs else ". Unsupported or corrupt file format."),
        )
    # Push optimized images to durable object storage (R2) when configured.
    await run_in_threadpool(
        objstore.upload_optimized, session_id, storage.optimized_dir(session_id), optimized)
    return {
        "session_id": session_id,
        "optimized": optimized,
        "optimize_results": opt_results,
    }


@app.post("/api/upload-more/{session_id}")
async def upload_more(
    session_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    remove_bg: str = Form("false"),
) -> dict:
    """Add more photos to an existing listing. Optimizes each new file into the
    session with non-colliding names and returns the new filenames, so the
    client can append them to the listing's image order."""
    _assert_session_owner(session_id, request)
    if not files:
        raise HTTPException(400, "No files uploaded")
    existing = storage.list_optimized(session_id)
    if len(existing) + len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"That would exceed {MAX_UPLOAD_FILES} photos on this listing.")
    strip_bg = str(remove_bg).lower() in ("true", "1", "yes", "on")

    def _idx(n: str) -> int:
        try:
            return int(n.replace("img_", "").replace(".jpg", ""))
        except ValueError:
            return -1
    start = max((_idx(n) for n in existing), default=-1) + 1

    orig = storage.original_dir(session_id)
    opt_dir = storage.optimized_dir(session_id)
    new_names: list[str] = []
    for j, f in enumerate(files):
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                400, f"'{f.filename or 'image'}' is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB per image)")
        idx = start + j
        suffix = Path(f.filename or f"add_{idx}").suffix or ".jpg"
        src = orig / f"add_{idx:02d}{suffix}"
        try:
            src.write_bytes(data)
            await run_in_threadpool(images.optimize, src, opt_dir / f"img_{idx:02d}.jpg", strip_bg)
            new_names.append(f"img_{idx:02d}.jpg")
        except OSError:
            raise HTTPException(507, "The server is out of storage space — try again shortly.")
        except Exception as exc:  # noqa: BLE001 - skip a bad file, keep the rest
            log.warning("upload-more: couldn't process %s: %s", f.filename, exc)
    if not new_names:
        raise HTTPException(400, "Could not process the uploaded image(s).")
    await run_in_threadpool(objstore.upload_optimized, session_id, opt_dir, new_names)
    log.info("upload-more: session=%s added=%d", session_id, len(new_names))
    return {"added": new_names, "optimized": storage.list_optimized(session_id)}


@app.post("/api/edit-image")
async def edit_image(
    request: Request,
    session_id: str = Form(...),
    name: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    """Overwrite one optimized image with a user-edited version (from the
    in-browser background clean-up tool). Re-encodes through Pillow to a clean
    JPEG so eBay always gets a valid file, and re-pushes to R2 if configured.

    session_id/name are form fields (not URL path segments) so an empty value
    can't fall through to the static handler and surface as an opaque 405.
    """
    session_id = (session_id or "").strip()
    name = (name or "").strip()
    if not session_id or not name:
        log.warning("edit-image: missing session_id=%r or name=%r", session_id, name)
        raise HTTPException(400, "Lost track of which photo to save — reopen the clean-up editor.")
    _assert_session_owner(session_id, request)
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    # Guard against path traversal in `name`.
    if opt_dir not in path.parents or not path.is_file():
        log.warning("edit-image: image not found (session=%s name=%s)", session_id, name)
        raise HTTPException(404, "That photo isn’t on the server anymore — re-upload it.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Edited image too large")

    def _save() -> None:
        from io import BytesIO
        import os
        from PIL import Image
        img = Image.open(BytesIO(data)).convert("RGB")
        # Write to a temp file and atomically replace, so a concurrent reader
        # (eBay fetching /media, or a thumbnail request) never sees a
        # half-written JPEG.
        tmp = path.with_name(path.name + ".tmp")
        img.save(tmp, "JPEG", quality=88, optimize=True)
        os.replace(tmp, path)

    try:
        await run_in_threadpool(_save)
    except Exception as exc:  # noqa: BLE001
        log.warning("edit-image: could not process (session=%s name=%s): %s", session_id, name, exc)
        raise HTTPException(400, f"Could not process the edited image: {exc}") from exc
    # When R2 is the source eBay fetches from, a failed re-push means the live
    # listing would keep the OLD photo — surface it instead of reporting success.
    if objstore.enabled():
        url = await run_in_threadpool(
            objstore.upload, path, objstore.key_for(session_id, name))
        if not url:
            log.warning("edit-image: R2 re-push failed (session=%s name=%s)", session_id, name)
            raise HTTPException(
                502, "Saved locally, but couldn’t update the stored copy eBay "
                     "uses. Try saving again in a moment.")
    db.touch_listing(session_id)  # bump updated_at so list thumbnails refetch
    log.info("edit-image saved: session=%s name=%s", session_id, name)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------------------
# Photo studio: AI-assisted clean-up + smart crop for the in-browser editor.
# All three endpoints accept an optional `file` (the editor's current canvas,
# including unsaved brush strokes); without it they read the saved photo.
# Nothing here writes to disk — the editor previews the result and saves via
# /api/edit-image, so every AI action stays reviewable and cancellable.
# ---------------------------------------------------------------------------

def _studio_load(session_id: str, name: str, data: Optional[bytes]):
    from io import BytesIO
    from PIL import Image

    if data:
        img = Image.open(BytesIO(data))
        img.load()
        return img
    session_id = (session_id or "").strip()
    name = (name or "").strip()
    if not session_id or not name:
        raise HTTPException(400, "Lost track of which photo this is — reopen the editor.")
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    if opt_dir not in path.parents or not path.is_file():
        raise HTTPException(404, "That photo isn’t on the server anymore — re-upload it.")
    return Image.open(path)


def _data_url(img, fmt: str = "JPEG") -> str:
    import base64
    from io import BytesIO

    buf = BytesIO()
    if fmt == "PNG":
        img.save(buf, "PNG", optimize=True)
        mime = "image/png"
    else:
        img.save(buf, "JPEG", quality=88, optimize=True)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode()}"


@app.post("/api/rotate-image")
async def rotate_image(payload: dict, request: Request) -> dict:
    """Quick-rotate an optimized photo 90° clockwise, in place. Atomic replace
    + R2 re-push, mirroring /api/edit-image, so eBay always fetches the
    rotated copy."""
    session_id = str(payload.get("session_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not session_id or not name:
        raise HTTPException(400, "session_id and name are required")
    _assert_session_owner(session_id, request)
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    if opt_dir not in path.parents or not path.is_file():
        raise HTTPException(404, "That photo isn’t on the server anymore — re-upload it.")

    def _rotate() -> None:
        import os
        from PIL import Image
        with Image.open(path) as img:
            rotated = img.convert("RGB").transpose(Image.Transpose.ROTATE_270)
        tmp = path.with_name(path.name + ".tmp")
        # No optimize=True here: the two-pass encode nearly doubles the time
        # for a few KB — a one-tap rotate should feel instant.
        rotated.save(tmp, "JPEG", quality=88)
        os.replace(tmp, path)

    try:
        await run_in_threadpool(_rotate)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't rotate that photo: {exc}") from exc
    # Mirror + bookkeeping off the critical path: the rotate is already live
    # locally (which /media serves first), so the R2 re-push and the
    # updated_at bump don't need to hold the spinner. Each was a sequential
    # network round-trip that made a one-tap rotate feel like seconds.
    if objstore.enabled():
        _in_background(objstore.upload, path, objstore.key_for(session_id, name),
                       what="rotate R2 push")
    _in_background(db.touch_listing, session_id, what="rotate touch")
    return {"ok": True}


@app.post("/api/image/analyze")
async def image_analyze(
    session_id: str = Form(""),
    name: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    """Re-check the item's borders: returns a mask of leftover background
    (non-white areas outside the detected subject) for the editor to highlight."""
    data = await file.read() if file else None
    if data and len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image too large")

    def _run() -> dict:
        img = _studio_load(session_id, name, data)
        res = images.analyze_cleanup(img)
        return {
            "ok": True,
            "residue_pct": res["residue_pct"],
            "bbox": res["bbox"],
            "mask": _data_url(res["residue_mask"], "PNG") if res["residue_pct"] > 0 else None,
            "width": res["residue_mask"].width,
            "height": res["residue_mask"].height,
        }

    return await run_in_threadpool(_run)


@app.post("/api/image/auto-clean")
async def image_auto_clean(
    session_id: str = Form(""),
    name: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    """AI clean-up: re-detect the subject and whiten everything outside it.
    Returns the cleaned image for the editor to preview (not saved yet)."""
    data = await file.read() if file else None
    if data and len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image too large")

    def _run() -> dict:
        img = _studio_load(session_id, name, data)
        return {"ok": True, "image": _data_url(images.auto_clean(img))}

    return await run_in_threadpool(_run)


@app.post("/api/image/remove-bg")
async def image_remove_bg(
    session_id: str = Form(""),
    name: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    """Full background removal composited onto pure white — Photoroom by
    default, Adobe Photoshop's Remove Background as the backup, the in-house
    model when neither is configured. Returns the processed image for the
    editor to preview (not saved)."""
    data = await file.read() if file else None
    if data and len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image too large")

    def _run() -> dict:
        img = _studio_load(session_id, name, data)
        out, engine = images.remove_background_white(img)
        # engine = which remover actually ran — the editor names it so a
        # misconfigured key can't hide behind a silently-degraded result.
        return {"ok": True, "image": _data_url(out), "engine": engine}

    try:
        return await run_in_threadpool(_run)
    except ValueError as exc:
        # Cutout failure OR an Adobe/Photoroom problem (bad credentials / out
        # of credits / rate limit) — the message tells the user exactly which.
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/image/smart-crop")
async def image_smart_crop(
    session_id: str = Form(""),
    name: str = Form(""),
    file: Optional[UploadFile] = File(None),
) -> dict:
    """Crop to the detected subject with a clean margin, padded to a square.
    Returns the cropped image for preview, or applied=False if the frame is
    already tight (so the UI can say so instead of degrading the photo)."""
    data = await file.read() if file else None
    if data and len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image too large")

    def _run() -> dict:
        img = _studio_load(session_id, name, data)
        cropped = images.smart_crop(img)
        if cropped is None:
            return {"ok": True, "applied": False,
                    "message": "Already nicely framed — no crop needed."}
        return {"ok": True, "applied": True, "image": _data_url(cropped)}

    return await run_in_threadpool(_run)


@app.post("/api/identify/{session_id}")
def identify(session_id: str, request: Request) -> dict:
    """Run Claude vision over the optimized images and draft a listing."""
    if not config.anthropic_ready():
        raise HTTPException(
            400, "ANTHROPIC_API_KEY not configured; cannot identify images."
        )
    opt_dir = storage.optimized_dir(session_id)
    names = storage.list_optimized(session_id)
    if not names:
        raise HTTPException(404, "No optimized images found for this session.")
    paths = [opt_dir / n for n in names]
    try:
        result = claude_ai.identify(paths, names,
                                    strategy=_pricing_strategy(_uid(request)))
    except Exception as exc:  # noqa: BLE001 - surface a clear reason to the UI
        code, message = claude_ai.ai_error_message(exc)
        log.warning("identify failed (session=%s): %s", session_id, exc)
        raise HTTPException(code, message) from exc
    _apply_listing_defaults(result.listing, _uid(request))

    # Auto-resolve a numeric eBay category ID when Taxonomy creds are present.
    if config.taxonomy_ready() and not result.listing.category_id:
        try:
            best = taxonomy.best_category_id(_category_query(result.listing))
            if best.get("category_id"):
                result.listing.category_id = best["category_id"]
                if best.get("path"):
                    result.listing.category_suggestion = best["path"]
        except Exception:  # noqa: BLE001 - never block identify on taxonomy
            pass

    storage.save_listing(session_id, result.listing)
    db.upsert_listing(session_id, result.listing.model_dump(), status="draft", user_id=_uid(request))
    return result.model_dump()


@app.post("/api/autofill-specifics/{session_id}")
def autofill_specifics(session_id: str, req: PublishRequest, request: Request) -> dict:
    """Fill eBay's required/recommended item specifics for the listing's
    category from the product photos — choosing fixed-value ("checkbox")
    aspects from eBay's own allowed values — and merge them in without
    overwriting anything the seller already set."""
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    _assert_session_owner(session_id, request)
    listing = req.listing
    if not listing.category_id:
        raise HTTPException(400, "Pick an eBay category first — specifics are per category.")
    if not config.taxonomy_ready():
        raise HTTPException(400, "eBay taxonomy not configured (need EBAY_CLIENT_ID/SECRET).")
    try:
        aspects = taxonomy.item_aspects(listing.category_id).get("aspects", [])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Couldn't load eBay item specifics: {exc}") from exc
    opt_dir = storage.optimized_dir(session_id)
    names = listing.images or storage.list_optimized(session_id)
    paths = [opt_dir / n for n in names if (opt_dir / n).is_file()]
    if not paths:
        raise HTTPException(400, "This listing's photos aren't on the server anymore.")
    try:
        filled = claude_ai.fill_aspects(paths, listing, aspects)
    except Exception as exc:  # noqa: BLE001
        code, message = claude_ai.ai_error_message(exc)
        log.warning("autofill-specifics failed (session=%s): %s", session_id, exc)
        raise HTTPException(code, message) from exc
    # Merge: keep the seller's existing non-empty values; add the rest.
    have = {s.name.strip().lower() for s in listing.item_specifics if s.value.strip()}
    added = 0
    for f in filled:
        if f.name.strip().lower() not in have:
            listing.item_specifics.append(f)
            have.add(f.name.strip().lower())
            added += 1
    storage.save_listing(session_id, listing)
    prev_status = (db.get_listing(session_id) or {}).get("status", "draft")
    db.upsert_listing(session_id, listing.model_dump(),
                      status=prev_status if prev_status in ("published", "ended") else "draft",
                      user_id=_uid(request))
    log.info("autofill-specifics: session=%s added=%d", session_id, added)
    return {"item_specifics": [s.model_dump() for s in listing.item_specifics], "added": added}


@app.post("/api/category-suggestions")
def category_suggestions(payload: dict) -> dict:
    """Return ranked eBay category suggestions for a free-text query."""
    if not config.taxonomy_ready():
        raise HTTPException(
            400,
            "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured; cannot resolve "
            "categories. Add them to .env to enable automatic category IDs.",
        )
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(400, "query is required")
    try:
        return taxonomy.suggest(query, limit=int(payload.get("limit", 5)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"eBay Taxonomy API error: {exc}") from exc


@app.post("/api/price-suggestions")
def price_suggestions(payload: dict, request: Request) -> dict:
    """Market-price suggestion for the listing from live eBay comps.

    Uses the same application token as taxonomy (no seller login needed).
    Sources are pluggable — see services/pricing.py. The headline suggestion
    honors the account's pricing strategy (Quick Flip / Median / Long Sale).
    """
    if not config.taxonomy_ready():
        raise HTTPException(
            400,
            "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured; cannot look "
            "up market prices.",
        )
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(400, "query is required")
    try:
        return pricing.suggest(
            query,
            category_id=str(payload.get("category_id") or "").strip() or None,
            condition=str(payload.get("condition") or "").strip() or None,
            strategy=_pricing_strategy(_uid(request)),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"eBay price lookup failed: {exc}") from exc


@app.post("/api/refine")
def refine(req: RefineRequest, request: Request) -> dict:
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    try:
        updated = claude_ai.refine(req.listing, req.prompt)
    except Exception as exc:  # noqa: BLE001 - surface a clear reason to the UI
        code, message = claude_ai.ai_error_message(exc)
        log.warning("refine failed (session=%s): %s", req.session_id, exc)
        raise HTTPException(code, message) from exc
    storage.save_listing(req.session_id, updated)
    db.upsert_listing(req.session_id, updated.model_dump(), status="draft", user_id=_uid(request))
    return updated.model_dump()


@app.post("/api/save/{session_id}")
def save_listing(session_id: str, listing: Listing, request: Request) -> dict:
    _assert_session_owner(session_id, request)
    storage.save_listing(session_id, listing)
    # A save must never demote a listing's lifecycle status: bulk publish and
    # image edits auto-save, and flattening 'published'/'ended' to 'draft'
    # here made live listings vanish from the Live bucket.
    prev = db.get_listing(session_id) or {}
    status = prev.get("status") if prev.get("status") in ("published", "ended") else "draft"
    db.upsert_listing(session_id, listing.model_dump(), status=status, user_id=_uid(request))
    return {"saved": True}


@app.post("/api/item-aspects")
def item_aspects(payload: dict) -> dict:
    """Required + recommended item specifics eBay defines for a category."""
    if not config.taxonomy_ready():
        raise HTTPException(400, "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured.")
    cid = str(payload.get("category_id", "")).strip()
    if not cid:
        raise HTTPException(400, "category_id is required")
    try:
        return taxonomy.item_aspects(cid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"eBay aspects lookup failed: {exc}") from exc


@app.post("/api/item-conditions")
def item_conditions(payload: dict, request: Request) -> dict:
    """The conditions eBay allows for a category (prevents publish error 25021).
    Uses the connected seller's token when available, else the app token."""
    if not config.taxonomy_ready():
        raise HTTPException(400, "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured.")
    cid = str(payload.get("category_id", "")).strip()
    if not cid:
        raise HTTPException(400, "category_id is required")
    creds = _ebay_creds_for(request)
    token = creds.get("access_token") if creds else None
    try:
        return taxonomy.item_conditions(cid, access_token=token)
    except Exception as exc:  # noqa: BLE001 - optional enhancement; fail soft
        log.info("item-conditions(cat=%s) failed: %s", cid, exc)
        return {"conditions": []}


@app.post("/api/delete-image")
def delete_image(payload: dict, request: Request) -> dict:
    """Remove one optimized image from a session (local disk + R2)."""
    session_id = str(payload.get("session_id", "")).strip()
    name = str(payload.get("name", "")).strip()
    if not session_id or not name:
        raise HTTPException(400, "session_id and name are required")
    _assert_session_owner(session_id, request)
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    if opt_dir not in path.parents:  # path-traversal guard
        raise HTTPException(400, "Invalid image name")
    if path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(500, f"Couldn't delete the image: {exc}") from exc
    # R2 mirror delete is a network round-trip the user shouldn't wait on —
    # the local file (which /media serves first) is already gone.
    if objstore.enabled():
        _in_background(objstore.delete, objstore.key_for(session_id, name),
                       what="delete-image R2")
    log.info("delete-image: session=%s name=%s", session_id, name)
    return {"ok": True, "remaining": storage.list_optimized(session_id)}


# ---------- Bulk mode: one photo dump -> many listings ----------
# Jobs are in-memory: the app runs a single always-on machine (fly.toml), and a
# lost job only means re-running the upload — listings themselves persist.
_BULK_JOBS: dict[str, dict] = {}
_BULK_LOCK = threading.Lock()
# Claude vision accepts at most 100 images per request; bigger piles are
# grouped in chunks of this size (no cap on the upload itself).
BULK_GROUP_CHUNK = 100
# Bound the in-memory job store. identify runs as a job on EVERY upload, so
# without eviction this dict would grow monotonically until restart. Dicts keep
# insertion order, so we drop the oldest past this cap — a client polls a job
# only briefly, and the resulting listings persist to the DB regardless.
_BULK_JOBS_MAX = 200


def _register_bulk_job(job_id: str, data: dict) -> None:
    with _BULK_LOCK:
        _BULK_JOBS[job_id] = data
        while len(_BULK_JOBS) > _BULK_JOBS_MAX:
            _BULK_JOBS.pop(next(iter(_BULK_JOBS)))


def _bulk_set(job_id: str, **fields) -> None:
    with _BULK_LOCK:
        job = _BULK_JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _run_bulk_job(job_id: str, staging_id: str, strip_bg: bool, mode: str,
                  uid: Optional[str], creds: Optional[dict], base_url: str) -> None:
    """Background worker: optimize -> group -> per-item identify (-> publish)."""
    strategy = _pricing_strategy(uid)          # once, not per item
    auto_promote = _auto_promote_enabled(uid)  # ditto
    try:
        _bulk_set(job_id, phase="optimizing", current=0)
        opt_results = images.optimize_all(
            storage.original_dir(staging_id), storage.optimized_dir(staging_id),
            strip_bg,
            progress=lambda done, total: _bulk_set(job_id, current=done,
                                                   total_photos=total))
        # Surface a background-removal failure (out of credits, bad key, rate
        # limit) on the job so the UI can say WHY the photos came back with
        # their backgrounds intact — silence here reads as "the feature is
        # broken" when the photo was deliberately kept unchanged.
        bg_failed = [r for r in opt_results if r.get("bg_error")]
        if bg_failed:
            _bulk_set(job_id, bg_error=bg_failed[0]["bg_error"],
                      bg_failed=len(bg_failed))
        names = storage.list_optimized(staging_id)
        if not names:
            _bulk_set(job_id, done=True, error="No usable photos in the upload.")
            return
        opt_dir = storage.optimized_dir(staging_id)

        _bulk_set(job_id, phase="grouping", total_photos=len(names), current=0)
        thumbs = [images.thumb_jpeg(opt_dir / n) for n in names]
        # Group in API-sized chunks. Resellers shoot item-by-item, so photos of
        # the same item land in the same chunk except right at a boundary —
        # worst case a boundary item shows up as two entries to merge by hand.
        groups: list[dict] = []
        for base in range(0, len(thumbs), BULK_GROUP_CHUNK):
            part = claude_ai.group_photos(thumbs[base:base + BULK_GROUP_CHUNK])["groups"]
            groups.extend({"name": g["name"],
                           "indices": [base + i for i in g["indices"]]}
                          for g in part)
        _bulk_set(job_id, total_items=len(groups))

        items: list[dict] = []
        for gi, group in enumerate(groups):
            _bulk_set(job_id, phase="identifying", current=gi + 1, items=list(items))
            sid = storage.new_session_id()
            item_dir = storage.optimized_dir(sid)
            item_names = []
            for j, idx in enumerate(group["indices"]):
                src = opt_dir / names[idx]
                dst_name = f"img_{j:02d}.jpg"
                shutil.copyfile(src, item_dir / dst_name)
                item_names.append(dst_name)
            objstore.upload_optimized(sid, item_dir, item_names)

            item = {"session_id": sid, "name": group["name"], "status": "draft",
                    "error": None, "listing_id": None,
                    "thumb": f"/media/{sid}/optimized/{item_names[0]}"}
            try:
                result = claude_ai.identify([item_dir / n for n in item_names],
                                            item_names, strategy=strategy)
                listing = _apply_listing_defaults(result.listing, uid)
                # Carry the account's Promote default onto the draft itself, so
                # the queue card shows what will actually happen at publish
                # rather than an unchecked box that promotes anyway.
                listing.promote = listing.promote or auto_promote
                if config.taxonomy_ready() and not listing.category_id:
                    try:
                        best = taxonomy.best_category_id(_category_query(listing))
                        if best.get("category_id"):
                            listing.category_id = best["category_id"]
                            if best.get("path"):
                                listing.category_suggestion = best["path"]
                    except Exception:  # noqa: BLE001
                        pass
                # Fill item specifics BEFORE publishing — bulk 'live' mode goes
                # straight to eBay here, so without this the listing lands with
                # only the generic first-pass specifics.
                _fill_category_specifics(listing, [item_dir / n for n in item_names])
                _fill_maker(listing, [item_dir / n for n in item_names])
                storage.save_listing(sid, listing)
                status = "draft"
                if mode == "live":
                    # Same reason as the single-listing path: publish through
                    # Trading so the seller can edit these in Seller Hub too,
                    # not just in this app.
                    if creds:
                        try:
                            urls = ebay.image_urls_for(sid, listing, base_url)
                            res = listing_sync.create_on_ebay(
                                creds["access_token"], listing, urls, creds=creds)
                            pub = {"published": True, "listing_id": res["listing_id"]}
                        except ValueError as exc:  # TradingError
                            pub = {"published": False, "message": str(exc)}
                    else:
                        pub = ebay.publish(sid, listing, "live", base_url, creds=creds)
                    if pub.get("published"):
                        status = "published"
                        item["status"] = "published"
                        item["listing_id"] = pub.get("listing_id")
                        # Auto-promote each just-published listing (account
                        # default, Settings) — the bulk path used to skip
                        # promotion entirely, so live batches landed unpromoted.
                        if creds and auto_promote:
                            _promote(sid, listing, creds,
                                     rate=listing.ad_rate_percent,
                                     ebay_listing_id=pub.get("listing_id"))
                    else:
                        # Stay a draft; surface why it couldn't go live.
                        item["error"] = pub.get("message") or "Couldn't publish automatically."
                db.upsert_listing(sid, listing.model_dump(), status=status, user_id=uid)
                item["listing"] = listing.model_dump()
                item["title"] = listing.title
            except Exception as exc:  # noqa: BLE001 - one bad item shouldn't kill the batch
                log.warning("bulk %s: item %d failed: %s", job_id, gi, exc)
                item["status"] = "error"
                item["error"] = str(exc)
                item["listing"] = None
                item["title"] = group["name"]
            items.append(item)

        _bulk_set(job_id, phase="done", done=True, items=items, current=len(groups))
        log.info("bulk %s: %d photos -> %d items (%s)", job_id, len(names), len(items), mode)
    except Exception as exc:  # noqa: BLE001 - job-level failure
        log.warning("bulk %s failed: %s", job_id, exc)
        _bulk_set(job_id, done=True, error=f"Bulk processing failed: {exc}")
    finally:
        # Staging photos were only needed to optimize + split into per-item
        # sessions; drop them so the volume doesn't grow with every batch.
        storage.purge_session(staging_id)


@app.post("/api/bulk/upload")
async def bulk_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    mode: str = Form("draft"),
    remove_bg: str = Form("false"),
) -> dict:
    """Bulk mode: accept a photo dump spanning multiple items, then process in
    the background (poll /api/bulk/status/{job_id}). mode: 'draft' queues every
    item for review; 'live' also attempts to publish each one."""
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    if mode not in ("draft", "live"):
        raise HTTPException(400, "mode must be 'draft' or 'live'")
    if not files:
        raise HTTPException(400, "No files uploaded")

    staging_id = storage.new_session_id()
    orig = storage.original_dir(staging_id)
    try:
        for i, f in enumerate(files):
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    400, f"'{f.filename or 'image'}' is too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB per image)")
            suffix = Path(f.filename or f"upload_{i}").suffix or ".jpg"
            (orig / f"src_{i:02d}{suffix}").write_bytes(data)
    except OSError as exc:
        # Disk full / write failure — clean up the partial staging and report it
        # clearly instead of a raw 500. Old orphans are swept on restart.
        storage.purge_session(staging_id)
        log.error("bulk upload: disk write failed (%s)", exc)
        raise HTTPException(
            507, "The server is low on storage right now, so the upload couldn't "
                 "be saved. Space is reclaimed automatically — try again in a "
                 "minute, or delete a few old listings to free some up.") from exc

    # Capture per-request context now — the worker thread has no Request.
    uid = _uid(request)
    creds = _ebay_creds_for(request) if mode == "live" else None
    job_id = storage.new_session_id()
    _register_bulk_job(job_id, {
        "id": job_id, "mode": mode, "phase": "uploading", "done": False,
        "error": None, "items": [], "total_items": 0, "current": 0,
        "total_photos": len(files),
    })
    threading.Thread(
        target=_run_bulk_job,
        args=(job_id, staging_id, str(remove_bg).lower() in ("true", "1", "yes", "on"),
              mode, uid, creds, _base_url(request)),
        daemon=True,
    ).start()
    log.info("bulk %s: started (%d files, mode=%s)", job_id, len(files), mode)
    return {"job_id": job_id}


@app.get("/api/bulk/status/{job_id}")
def bulk_status(job_id: str) -> dict:
    with _BULK_LOCK:
        job = _BULK_JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown bulk job (the server may have restarted).")
        return json.loads(json.dumps(job))  # deep copy, thread-safe snapshot


def _run_identify_job(job_id: str, session_id: str, uid: Optional[str]) -> None:
    """Background worker for a single-item identify. Claude vision over several
    photos can take long enough that a synchronous request outlives the
    proxy/browser timeout ('server taking too long'); running it as a job the
    client polls avoids that entirely and still saves the draft when done."""
    try:
        opt_dir = storage.optimized_dir(session_id)
        names = storage.list_optimized(session_id)
        if not names:
            _bulk_set(job_id, done=True, error="No optimized images found for this session.")
            return
        result = claude_ai.identify([opt_dir / n for n in names], names,
                                    strategy=_pricing_strategy(uid))
        _apply_listing_defaults(result.listing, uid)
        # Auto-resolve a numeric eBay category ID when Taxonomy creds are present.
        if config.taxonomy_ready() and not result.listing.category_id:
            try:
                best = taxonomy.best_category_id(_category_query(result.listing))
                if best.get("category_id"):
                    result.listing.category_id = best["category_id"]
                    if best.get("path"):
                        result.listing.category_suggestion = best["path"]
            except Exception:  # noqa: BLE001 - never block identify on taxonomy
                pass
        # Fill the category's item specifics now so the draft is SEO-ready.
        _fill_category_specifics(result.listing, [opt_dir / n for n in names])
        # Second-layer maker ID — only runs when Brand/Maker/Manufacturer are
        # still blank after the passes above.
        _fill_maker(result.listing, [opt_dir / n for n in names])
        storage.save_listing(session_id, result.listing)
        db.upsert_listing(session_id, result.listing.model_dump(), status="draft", user_id=uid)
        _bulk_set(job_id, done=True, phase="done", result=result.model_dump())
    except Exception as exc:  # noqa: BLE001 - surface a clear reason to the UI
        log.warning("identify job %s failed: %s", job_id, exc)
        _bulk_set(job_id, done=True, error=claude_ai.ai_error_message(exc)[1])


@app.post("/api/identify-async/{session_id}")
def identify_async(session_id: str, request: Request) -> dict:
    """Start a background identify; poll /api/bulk/status/{job_id} for the
    result. Same outcome as POST /api/identify, but it never holds a long
    synchronous request open, so slow vision calls can't time out the browser."""
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured; cannot identify images.")
    if not storage.list_optimized(session_id):
        raise HTTPException(404, "No optimized images found for this session.")
    uid = _uid(request)
    job_id = storage.new_session_id()
    _register_bulk_job(job_id, {
        "id": job_id, "kind": "identify", "phase": "identifying",
        "done": False, "error": None, "result": None,
    })
    threading.Thread(
        target=_run_identify_job, args=(job_id, session_id, uid), daemon=True,
    ).start()
    log.info("identify job %s: started (session=%s)", job_id, session_id)
    return {"job_id": job_id}


@app.post("/api/shelf-scan")
async def shelf_scan(files: list[UploadFile] = File(...)) -> dict:
    """Shop Mode 'Scan a shelf': the client samples frames from a recorded
    video and posts them here; Claude flags items worth a closer look. No
    pricing, no persistence — pure triage."""
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    if not files:
        raise HTTPException(400, "No frames provided.")
    frames: list[bytes] = []
    for f in files[:8]:
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "A frame was too large.")
        if data:
            frames.append(data)
    if not frames:
        raise HTTPException(400, "No readable frames.")
    try:
        result = await run_in_threadpool(claude_ai.scan_shelf, frames)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Shelf scan failed: {exc}") from exc
    log.info("shelf scan: %d frames -> %d candidates", len(frames),
             len(result.get("items", [])))
    return result


@app.post("/api/inventory/add")
def inventory_add(req: PublishRequest, request: Request) -> dict:
    """Shop Mode 'Buy': save a scanned item to the user's unlisted inventory
    (status='unlisted'), so it shows up in the Sell dashboard to finish + list
    later. Reuses the listing record; mode is ignored."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in to save items to your inventory.")
    storage.save_listing(req.session_id, req.listing)
    db.upsert_listing(req.session_id, req.listing.model_dump(),
                      status="unlisted", user_id=uid)
    log.info("inventory add: session=%s user=%s", req.session_id, uid)
    return {"ok": True, "id": req.session_id}


@app.get("/api/listings")
def listings(request: Request, limit: int = 50) -> dict:
    """History of the current user's saved listings (most recent first)."""
    user = auth.current_user(request)
    items = db.list_listings(limit=limit, user_id=user["id"]) if user else []
    return {"listings": items, "db": db.db_status(), "authed": bool(user)}


def _live_ebay_id_map(items: list) -> dict:
    """{eBay listing id: our record id} for the user's live listings."""
    out = {}
    for it in items:
        if it.get("status") in ("published", "live"):
            eid = (it.get("listing") or {}).get("ebay_listing_id")
            if eid:
                out[str(eid)] = it["id"]
    return out


def _metrics_by_record_id(creds: Optional[dict], items: list) -> dict:
    """eBay views/watchers for the user's live listings, keyed by OUR listing
    record id. Best-effort — {} when eBay isn't connected / scope not granted."""
    id_by_ebay = _live_ebay_id_map(items)
    if not creds or not id_by_ebay:
        return {}
    try:
        raw = metrics.listing_metrics(creds, list(id_by_ebay))
    except Exception as exc:  # noqa: BLE001 - metrics never break a request
        log.info("listing metrics unavailable: %s", exc)
        return {}
    return {id_by_ebay[eid]: m for eid, m in raw.items() if eid in id_by_ebay}


def _rates_by_record_id(creds: Optional[dict], items: list) -> dict:
    """eBay's recommended ad rate for the user's live listings, keyed by OUR
    record id. Best-effort — {} when unavailable."""
    id_by_ebay = _live_ebay_id_map(items)
    if not creds or not id_by_ebay:
        return {}
    try:
        raw = promotions.suggested_ad_rates(creds, list(id_by_ebay))
    except Exception as exc:  # noqa: BLE001 - recommendations are optional
        log.info("ad-rate recommendations unavailable: %s", exc)
        return {}
    return {id_by_ebay[eid]: r for eid, r in raw.items() if eid in id_by_ebay}


def _promoted_record_ids(creds: Optional[dict], items: list) -> set:
    """Record ids whose live listing currently has an ACTIVE eBay ad — ours OR
    one created directly in Seller Hub — so we never suggest promoting an item
    that's already promoted. Best-effort; empty when the scope isn't granted."""
    if not creds:
        return set()
    ads = promotions.active_ads(creds)
    if not ads:
        return set()
    promoted = set()
    for it in items:
        if it.get("status") not in ("published", "live"):
            continue
        listing = it.get("listing") or {}
        eid = str(listing.get("ebay_listing_id") or "")
        try:
            sku = ebay._sku(it["id"], Listing(**listing))
        except Exception:  # noqa: BLE001
            sku = ""
        if (eid and eid in ads) or (sku and sku in ads):
            promoted.add(it["id"])
    return promoted


@app.get("/api/ebay/listing-metrics")
def listing_metrics_route(request: Request) -> dict:
    """eBay views/impressions/watchers for the user's live listings, keyed by
    our listing record id. Empty when eBay isn't connected."""
    user = auth.current_user(request)
    if not user:
        return {"metrics": {}}
    items = db.list_listings(limit=200, user_id=user["id"])
    return {"metrics": _metrics_by_record_id(_ebay_creds_for(request), items)}


@app.get("/api/insights")
def insights(request: Request) -> dict:
    """Ranked 'what to do next' actions across the signed-in user's listings —
    finish drafts, relist ended items, promote/reprice stale live ones. Folds in
    eBay views/watchers and recommended ad rates when available. Returns an empty
    list for logged-out users. Never raises."""
    user = auth.current_user(request)
    if not user:
        return {"recommendations": []}
    try:
        items = db.list_listings(limit=200, user_id=user["id"])
        creds = _ebay_creds_for(request)
        metrics_by_id = _metrics_by_record_id(creds, items)
        rates_by_id = _rates_by_record_id(creds, items)
        promoted_ids = _promoted_record_ids(creds, items)
        return {"recommendations": recommender.recommendations(
            items, metrics_by_id=metrics_by_id, rates_by_id=rates_by_id,
            promoted_ids=promoted_ids)}
    except Exception as exc:  # noqa: BLE001 - insights must never break the app
        log.warning("insights failed for user=%s: %s", user["id"], exc)
        return {"recommendations": []}


@app.post("/api/ebay/promote")
def promote_one(payload: dict, request: Request) -> dict:
    """One-click promote a single LIVE listing via Promoted Listings Standard,
    using the given ad rate, else eBay's recommended rate, else the default."""
    user = auth.current_user(request)
    creds = _ebay_creds_for(request)
    if not user or not creds:
        raise HTTPException(400, "Connect eBay first.")
    lid = str(payload.get("listing_id") or "").strip()
    rec = db.get_listing(lid)
    if not rec or (rec.get("user_id") and rec["user_id"] != user["id"]):
        raise HTTPException(404, "Listing not found")
    if rec.get("status") not in ("published", "live"):
        raise HTTPException(400, "Only live listings can be promoted.")
    listing = Listing(**(rec.get("listing") or {}))
    try:
        rate = float(payload.get("ad_rate_percent") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0:
        rate = _rates_by_record_id(creds, [rec]).get(lid) or 0
    status = _promote(lid, listing, creds, rate=rate)
    if status.get("promoted"):
        storage.save_listing(lid, listing)
        db.upsert_listing(lid, listing.model_dump(), status=rec.get("status"),
                          user_id=user["id"])
    return {"ok": bool(status.get("promoted")), "ad_rate": listing.ad_rate_percent,
            "needs_reconnect": bool(status.get("needs_reconnect")),
            "message": status.get("message")}


@app.post("/api/ebay/promote-all")
def promote_all(request: Request) -> dict:
    """Promote every live, not-yet-promoted listing at eBay's recommended rate
    (falling back to the default). Best-effort per item; stops early and asks the
    user to reconnect if the token lacks ad permissions."""
    user = auth.current_user(request)
    creds = _ebay_creds_for(request)
    if not user or not creds:
        raise HTTPException(400, "Connect eBay first.")
    items = [i for i in db.list_listings(limit=200, user_id=user["id"])
             if i.get("status") in ("published", "live")
             and not (i.get("listing") or {}).get("promote")]
    rates = _rates_by_record_id(creds, items)
    promoted = 0
    needs_reconnect = False
    for it in items:
        listing = Listing(**(it.get("listing") or {}))
        status = _promote(it["id"], listing, creds, rate=rates.get(it["id"]))
        if status.get("promoted"):
            storage.save_listing(it["id"], listing)
            db.upsert_listing(it["id"], listing.model_dump(), status=it.get("status"),
                              user_id=user["id"])
            promoted += 1
        elif status.get("needs_reconnect"):
            needs_reconnect = True
            break
    return {"promoted": promoted, "total": len(items), "needs_reconnect": needs_reconnect}


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: str, request: Request) -> dict:
    rec = db.get_listing(listing_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    # Enforce ownership for listings that belong to an account.
    if rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")
    return rec


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: str, request: Request) -> dict:
    """Remove a saved listing/draft and clean up its files. A missing (or
    not-owned) listing is a 404. One DB round-trip — delete_listing does its
    own ownership check — and the disk/R2 cleanup runs in the background, so
    the button doesn't hang on a cold database + file I/O."""
    if not db.delete_listing(listing_id, _uid(request)):
        raise HTTPException(404, "Listing not found")
    _in_background(_purge_session_images, listing_id, what="delete cleanup")
    log.info("listing deleted: id=%s user=%s", listing_id, _uid(request))
    return {"ok": True}


@app.post("/api/listings/bulk-delete")
def bulk_delete_listings(payload: dict, request: Request) -> dict:
    """Mass-delete listings (drafts) in ONE request: each id is deleted with
    the same per-row ownership check as the single delete; file/R2 cleanup for
    all of them runs in the background. Ids that don't exist or aren't owned
    are skipped and reported back."""
    ids = [str(s).strip() for s in (payload.get("ids") or []) if str(s).strip()]
    ids = list(dict.fromkeys(ids))[:200]
    if not ids:
        raise HTTPException(400, "No listings selected.")
    uid = _uid(request)
    deleted = [lid for lid in ids if db.delete_listing(lid, uid)]
    for lid in deleted:
        _in_background(_purge_session_images, lid, what="bulk-delete cleanup")
    log.info("bulk delete: %d/%d removed user=%s", len(deleted), len(ids), uid)
    return {"ok": True, "deleted": deleted,
            "skipped": [i for i in ids if i not in deleted]}


@app.post("/api/listings/merge")
def merge_listings(payload: dict, request: Request) -> dict:
    """Merge duplicate drafts into one listing: every source listing's photos
    are appended to the target (order preserved), then the sources are deleted
    (DB + disk + R2). The fix-up for bulk grouping splitting one item's photos
    into several draft listings."""
    target_id = str(payload.get("target_id") or "").strip()
    raw_sources = [str(s).strip() for s in (payload.get("source_ids") or [])]
    source_ids = [s for s in dict.fromkeys(raw_sources) if s and s != target_id]
    if not target_id or not source_ids:
        raise HTTPException(400, "Pick a target and at least one duplicate to merge.")
    _assert_session_owner(target_id, request)
    for sid in source_ids:
        _assert_session_owner(sid, request)
    uid = _uid(request)

    trec = db.get_listing(target_id)
    if not trec:
        raise HTTPException(404, "Listing not found")
    if trec.get("status") in ("published", "live"):
        raise HTTPException(400, "Merge into a draft — this target is already live on eBay.")
    listing = Listing(**(trec.get("listing") or {}))
    tdir = storage.optimized_dir(target_id)
    tdir.mkdir(parents=True, exist_ok=True)

    def _idx(n: str) -> int:
        try:
            return int(n.replace("img_", "").replace(".jpg", ""))
        except ValueError:
            return -1

    base = list(listing.images) or storage.list_optimized(target_id)
    nxt = max([_idx(n) for n in base]
              + [_idx(n) for n in storage.list_optimized(target_id)], default=-1) + 1
    added: list[str] = []
    for sid in source_ids:
        srec = db.get_listing(sid) or {}
        s_listing = srec.get("listing") or {}
        sdir = storage.optimized_dir(sid)
        for n in (s_listing.get("images") or storage.list_optimized(sid)):
            src = sdir / n
            if not src.is_file():
                continue  # photo already lost from disk — skip, keep merging
            dst_name = f"img_{nxt:02d}.jpg"
            try:
                shutil.copyfile(src, tdir / dst_name)
            except OSError:
                raise HTTPException(507, "The server is out of storage space — try again shortly.")
            added.append(dst_name)
            nxt += 1

    listing.images = base + added
    if added:
        objstore.upload_optimized(target_id, tdir, added)
    storage.save_listing(target_id, listing)
    db.upsert_listing(target_id, listing.model_dump(), status="draft", user_id=uid)
    # Sources are consumed: remove their records and reclaim their storage.
    for sid in source_ids:
        db.delete_listing(sid, uid)
        _purge_session_images(sid)
    log.info("merged %d listing(s) into %s (+%d photos) user=%s",
             len(source_ids), target_id, len(added), uid)
    return {"ok": True, "added": len(added), "removed": source_ids,
            "listing": listing.model_dump()}


@app.post("/api/publish")
def publish(req: PublishRequest, request: Request) -> JSONResponse:
    if req.mode not in ("draft", "live"):
        raise HTTPException(400, "mode must be 'draft' or 'live'")
    _assert_session_owner(req.session_id, request)
    storage.save_listing(req.session_id, req.listing)
    creds = _ebay_creds_for(request)

    prev_rec = db.get_listing(req.session_id) or {}
    already_live = prev_rec.get("status") in ("published", "live")

    # A listing IMPORTED from eBay (or published by us through Trading) isn't
    # Inventory-API managed, so edits go back through the Trading API instead
    # of the publish path below.
    if listing_sync.is_imported(req.listing):
        uid = _uid(request)
        if req.mode == "draft":
            db.upsert_listing(req.session_id, req.listing.model_dump(),
                              status="published", user_id=uid)
            return JSONResponse({"dry_run": False, "mode": "draft",
                                 "message": "Saved. Choose Update on eBay to push "
                                            "these changes to your live listing."})
        if not creds:
            raise HTTPException(400, "Connect eBay first.")
        try:
            res = listing_sync.push_edit(creds["access_token"], req.listing)
        except ValueError as exc:  # TradingError — eBay's own reason
            log.warning("revise (imported) failed: session=%s: %s", req.session_id, exc)
            db.upsert_listing(req.session_id, req.listing.model_dump(),
                              status="published", user_id=uid)
            return JSONResponse({
                "dry_run": False, "error": True, "mode": "live",
                "message": str(exc),
                "issues": ebay_errors.from_response(str(exc)),
            })
        db.upsert_listing(req.session_id, req.listing.model_dump(),
                          status="published", user_id=uid)
        log.info("revise (imported) ok: session=%s item=%s",
                 req.session_id, res.get("listing_id"))
        return JSONResponse({"published": True, "revised": True, "mode": "live",
                             "listing_id": res.get("listing_id"),
                             "message": "Your eBay listing has been updated."})

    # A listing that's already live must NEVER lose its 'published' status in
    # our records just because a revise attempt was blocked or errored — the
    # listing is still live on eBay either way.
    was_live = already_live
    # Pre-publish checklist: catch everything eBay would reject BEFORE the
    # round-trip, with field-targeted fixes. Only gates a real (connected)
    # live publish — dry-runs and drafts stay permissive.
    if req.mode == "live" and (creds or config.ebay_ready()):
        problems = preflight.errors_only(_preflight_issues(request, req.listing, "live"))
        if problems:
            db.upsert_listing(req.session_id, req.listing.model_dump(),
                              status="published" if was_live else "draft",
                              user_id=_uid(request))
            log.info("publish blocked by preflight: session=%s issues=%d",
                     req.session_id, len(problems))
            return JSONResponse({
                "dry_run": False,
                "error": True,
                "mode": req.mode,
                "message": f"Not quite ready — {len(problems)} thing"
                           f"{'s' if len(problems) != 1 else ''} to fix before eBay will accept it:",
                "issues": problems,
            })
    # Self-heal the ship-from location on a live publish: re-ensure it from the
    # saved ZIP so a location missing its country (eBay 'Item.Country empty')
    # gets repaired without the user re-saving settings.
    if req.mode == "live" and creds and creds.get("ship_from_postal"):
        try:
            key = ebay_auth.ensure_inventory_location(
                creds["access_token"], creds["ship_from_postal"])
            if key:
                creds["merchant_location_key"] = key
                db.save_ebay_account(creds["_uid"], merchant_location_key=key)
        except Exception as exc:  # noqa: BLE001 - don't block publish on this
            log.warning(f"ebay: location re-ensure failed: {exc}")
    # NEW live listings go out through the Trading API, not the Inventory API.
    # An Inventory-API listing is "inventory-based" and eBay refuses to let the
    # seller edit it anywhere but the tool that made it — Seller Hub answers
    # "Inventory-based listing management is not currently supported by this
    # tool." Publishing through Trading produces an ordinary listing they can
    # edit in Seller Hub, the eBay app, or here; source="ebay" then routes later
    # edits from this app down the same revise path imported listings use.
    if (req.mode == "live" and creds and not already_live
            and not listing_sync.is_imported(req.listing)
            and not req.listing.ebay_listing_id):
        urls = ebay.image_urls_for(req.session_id, req.listing, _base_url(request))
        try:
            res = listing_sync.create_on_ebay(
                creds["access_token"], req.listing, urls, creds=creds)
        except ValueError as exc:  # TradingError — eBay's own reason
            log.warning("trading publish failed: session=%s: %s", req.session_id, exc)
            db.upsert_listing(req.session_id, req.listing.model_dump(),
                              status="draft", user_id=_uid(request))
            return JSONResponse({
                "dry_run": False, "error": True, "mode": "live",
                "message": str(exc),
                "issues": ebay_errors.from_response(str(exc)),
            })
        storage.save_listing(req.session_id, req.listing)
        result = {"published": True, "mode": "live",
                  "listing_id": res["listing_id"],
                  "message": "Your listing is live on eBay."}
        if req.listing.promote or _auto_promote_enabled(_uid(request)):
            result["promote_status"] = _promote(
                req.session_id, req.listing, creds,
                rate=req.listing.ad_rate_percent,
                ebay_listing_id=res["listing_id"])
        db.upsert_listing(req.session_id, req.listing.model_dump(),
                          status="published", user_id=_uid(request))
        log.info("trading publish ok: session=%s item=%s",
                 req.session_id, res["listing_id"])
        return JSONResponse(result)

    log.info("publish request: session=%s mode=%s connected=%s", req.session_id,
             req.mode, bool(creds))
    result = ebay.publish(req.session_id, req.listing, req.mode, _base_url(request),
                          creds=creds, is_revise=was_live)
    # Record the outcome: published (live), draft, or dry-run. An errored
    # attempt never demotes a live listing, and never records "live" for a
    # listing that isn't (the old status=req.mode did exactly that).
    if result.get("published"):
        status = "published"
        log.info("publish OK: session=%s listing_id=%s revised=%s",
                 req.session_id, result.get("listing_id"), result.get("revised"))
    elif result.get("error"):
        status = "published" if was_live else "draft"
        log.warning("publish error: session=%s step=%s", req.session_id, result.get("step"))
    elif result.get("dry_run"):
        status = "dry_run"
    else:
        status = "published" if was_live else req.mode
    dump = req.listing.model_dump()
    # Persist the eBay item id so the app can link to (and keep tracking) the
    # live listing across sessions.
    if result.get("listing_id"):
        dump["ebay_listing_id"] = str(result["listing_id"])
    db.upsert_listing(req.session_id, dump, status=status, user_id=_uid(request))
    # Promoted Listings: once the item is live, best-effort create/refresh its
    # ad. Runs when the listing's Promote toggle is on OR the account's
    # auto-promote default (Settings) is — at the chosen rate, else eBay's
    # recommended rate. Never blocks or fails the publish; the status is
    # attached for the UI to show (incl. 'reconnect to grant ad permissions').
    if result.get("published") and (req.listing.promote
                                    or _auto_promote_enabled(_uid(request))):
        result["promote_status"] = _promote(
            req.session_id, req.listing, creds,
            rate=req.listing.ad_rate_percent,
            ebay_listing_id=result.get("listing_id"))
        if result["promote_status"].get("promoted"):
            # Re-record with the promote flag + actual rate so the Dashboard
            # and recommender see it as promoted.
            dump = req.listing.model_dump()
            if result.get("listing_id"):
                dump["ebay_listing_id"] = str(result["listing_id"])
            db.upsert_listing(req.session_id, dump, status=status,
                              user_id=_uid(request))
    return JSONResponse(result)


@app.post("/api/ebay/end-listing")
def end_listing(req: SessionOnlyRequest, request: Request) -> dict:
    """End (withdraw) this session's live eBay listing. The listing stays in
    the app as status 'ended' so it can be edited and relisted later."""
    rec = db.get_listing(req.session_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    if rec.get("user_id") and rec["user_id"] != _uid(request):
        raise HTTPException(404, "Listing not found")
    creds = _ebay_creds_for(request)
    if not (creds or config.ebay_ready()):
        raise HTTPException(400, "Connect eBay first.")
    listing = Listing(**(rec.get("listing") or {}))
    try:
        # Imported listings live outside the Inventory API — end them through
        # the Trading API instead.
        if listing_sync.is_imported(listing):
            if not creds:
                raise HTTPException(400, "Connect eBay first.")
            res = listing_sync.end(creds["access_token"], listing)
        else:
            res = ebay.withdraw(req.session_id, listing, creds=creds)
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc
    if res.get("ended") or res.get("not_live"):
        db.upsert_listing(req.session_id, rec.get("listing") or {},
                          status="ended", user_id=_uid(request))
    return res


@app.post("/api/ebay/sync-listings")
def sync_listings(request: Request) -> dict:
    """Reconcile our 'live' listings with eBay: a sold item is auto-archived
    (status 'sold', its photos purged to reclaim storage), a listing that
    otherwise disappeared flips to 'ended', and missing eBay item ids are
    backfilled. Definitive answers only — an API blip changes nothing."""
    creds = _ebay_creds_for(request)
    user = auth.current_user(request)
    if not (creds or config.ebay_ready()) or not user:
        return {"checked": 0, "changed": 0, "archived": 0}
    live = [i for i in db.list_listings(limit=200, user_id=user["id"])
            if i.get("status") in ("published", "live")]
    # Imported listings are reconciled through the Trading API (the Inventory
    # API can't see them at all); app-created ones keep the offer check below.
    # EVERY imported listing must be excluded from `items` — not just the ones
    # this run refreshes — or the offer check would call an eBay listing it
    # can't see "ended". Each side is capped so one sync click can't fan out
    # into hundreds of eBay calls; the rest are picked up by the next sync.
    imported_ids = {i["id"] for i in live
                    if listing_sync.is_imported(i.get("listing") or {})}
    imported = [i for i in live if i["id"] in imported_ids][:60]
    items = [i for i in live if i["id"] not in imported_ids][:40]
    changed = 0
    archived = 0
    if imported and creds:
        try:
            changed += listing_sync.refresh_statuses(
                creds["access_token"], user["id"], imported)
        except Exception as exc:  # noqa: BLE001 - sync is best-effort
            log.info("ebay sync: imported refresh failed: %s", exc)
    for it in items:
        listing = Listing(**(it.get("listing") or {}))
        status, lid = ebay.live_status(it["id"], listing, creds=creds)
        if status == "sold":
            db.upsert_listing(it["id"], it.get("listing") or {},
                              status="sold", user_id=user["id"])
            _purge_session_images(it["id"])  # archived — reclaim the storage
            changed += 1
            archived += 1
        elif status == "ended":
            db.upsert_listing(it["id"], it.get("listing") or {},
                              status="ended", user_id=user["id"])
            changed += 1
        elif status == "published" and lid and not listing.ebay_listing_id:
            data = {**(it.get("listing") or {}), "ebay_listing_id": lid}
            db.upsert_listing(it["id"], data, status="published", user_id=user["id"])
    if changed:
        log.info("ebay sync: %d listing(s) updated (%d archived as sold) for user=%s",
                 changed, archived, user["id"])
    return {"checked": len(items) + len(imported), "changed": changed,
            "archived": archived}


# Bounds one import run. A store bigger than this imports across repeated
# syncs rather than tying up a single request indefinitely.
IMPORT_LIMIT = 300


@app.post("/api/ebay/import-listings")
def import_listings(request: Request) -> dict:
    """Pull the seller's ENTIRE active eBay store into the app.

    The Inventory API only knows about listings this app published, so listings
    created on eBay directly (or with another tool) are fetched through the
    Trading API instead. Imported listings become normal records the seller can
    open, edit, and push back — see services/listing_sync.
    """
    user = auth.current_user(request)
    creds = _ebay_creds_for(request)
    if not user:
        raise HTTPException(401, "Log in first.")
    if not creds:
        raise HTTPException(400, "Connect eBay first — Settings → Connect eBay.")
    if not db.enabled():
        raise HTTPException(503, "No database configured — imported listings need "
                                 "DATABASE_URL set.")
    try:
        result = listing_sync.import_active(
            creds["access_token"], user["id"], limit=IMPORT_LIMIT)
    except ebay_trading.TradingError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface a clear reason
        log.warning("import-listings failed for user=%s: %s", user["id"], exc)
        raise HTTPException(502, f"Couldn't import your eBay listings: {exc}") from exc
    return result


@app.get("/media/{session_id}/optimized/{name}")
def media(session_id: str, name: str, v: str = ""):
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    # Guard against path traversal in `name` (e.g. "../../etc/passwd").
    if opt_dir not in path.parents:
        raise HTTPException(404, "Not found")
    if path.is_file():
        return FileResponse(path)
    # Local file gone (e.g. after a restart) — fall back to R2 if available.
    # Carry the client's cache-bust version onto the R2 URL, otherwise the CDN
    # can keep serving a pre-edit copy (e.g. a photo rotated after upload).
    if objstore.enabled():
        url = objstore.public_url(objstore.key_for(session_id, name))
        safe_v = "".join(c for c in v if c.isalnum())[:24]
        if safe_v:
            url += ("&" if "?" in url else "?") + "v=" + safe_v
        return RedirectResponse(url)
    raise HTTPException(404, "Not found")


# Clean URLs for the static pages eBay's app settings link to (StaticFiles
# only serves them under their exact .html filenames).
@app.get("/privacy-policy")
def privacy_policy():
    return FileResponse(FRONTEND_DIR / "privacy-policy.html")


@app.get("/about")
def about():
    return FileResponse(FRONTEND_DIR / "about.html")


# Serve the frontend (index.html + assets) at the root.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

"""FastAPI application wiring the eBay listing pipeline together.

Pipeline:
  upload images -> optimize (Pillow) -> identify (Claude vision) ->
  edit/refine in preview -> publish (eBay, or dry-run).
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, ebay_auth, objstore, storage
from .config import log
from .models import Listing, PublishRequest, RefineRequest
from .services import claude_ai, ebay, images, preflight, pricing, taxonomy

app = FastAPI(title="eBay Listing Generator")


class _QuietDeletionPings(logging.Filter):
    """eBay pings /api/ebay/account-deletion ~40×/hour to keep our compliance
    endpoint validated. Logged at INFO by uvicorn's access logger, those pings
    bury every other request in the Fly logs. Drop only the *successful* ones —
    any non-2xx (a real problem with the endpoint) still gets logged."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            path, status = args[2], args[4]
            if (path == "/api/ebay/account-deletion"
                    and isinstance(status, int) and status < 400):
                return False
        return True


logging.getLogger("uvicorn.access").addFilter(_QuietDeletionPings())

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


@app.on_event("startup")
def _warm_models() -> None:
    """Warm the background-removal model in a daemon thread so uvicorn binds
    the port immediately (machine stays reachable) while the ~60s first-use
    JIT/import cost happens in the background."""
    import threading

    threading.Thread(target=images.warm, daemon=True).start()


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
        "ebay_extended": config.EBAY_EXTENDED_SCOPES,
        "storage": "r2" if objstore.enabled() else "local",
        "db": db.db_status(),
    }


def _category_query(listing) -> str:
    parts = [listing.brand, listing.title, listing.category_suggestion]
    return " ".join(p for p in parts if p).strip()


def _uid(request: Request):
    user = auth.current_user(request)
    return user["id"] if user else None


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
        db.save_ebay_account(
            uid, refresh_token=tokens["refresh_token"],
            ebay_username=ident["username"], ebay_email=ident["email"],
            **policies)
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
    return {
        "oauth_ready": config.ebay_oauth_ready(),
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


@app.post("/api/ebay/ensure-defaults")
def ensure_ebay_defaults(request: Request) -> dict:
    """Make sure the account has sane policy defaults without any setup work:
    a USPS Ground Advantage shipping policy (cheapest broadly-applicable
    service; created if missing) and an eBay Managed Payments payment policy —
    each saved as the account default only where none is set yet."""
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    token = creds["access_token"]
    out: dict = {"fulfillment": None, "payment": None, "return": None}
    saves: dict = {}
    try:
        ground = ebay_auth.ensure_ground_policy(token)
        out["fulfillment"] = ground
        if ground.get("id") and not creds.get("fulfillment_policy_id"):
            saves["fulfillment_policy_id"] = ground["id"]
    except Exception as exc:  # noqa: BLE001 - each default is best-effort
        log.warning("ensure-defaults: ground policy failed: %s", exc)
    try:
        payment = ebay_auth.ensure_payment_policy(token)
        out["payment"] = payment
        if payment.get("id") and not creds.get("payment_policy_id"):
            saves["payment_policy_id"] = payment["id"]
    except Exception as exc:  # noqa: BLE001
        log.warning("ensure-defaults: payment policy failed: %s", exc)
    try:
        ret = ebay_auth.ensure_return_policy(token)
        out["return"] = ret
        if ret.get("id") and not creds.get("return_policy_id"):
            saves["return_policy_id"] = ret["id"]
    except Exception as exc:  # noqa: BLE001
        log.warning("ensure-defaults: return policy failed: %s", exc)
    if saves:
        db.save_ebay_account(creds["_uid"], **saves)
    return out


@app.get("/api/ebay/stats")
def ebay_stats(request: Request) -> dict:
    """Dashboard tile data pulled live from eBay (items sold). Cheap + cached
    client-side; returns {sold: {count, days}} or nulls when not connected."""
    creds = _ebay_creds_for(request)
    if not creds:
        return {"connected": False, "sold": {"count": None}}
    sold = ebay.fetch_sold_count(creds)
    return {"connected": True, "sold": sold}


@app.get("/api/ebay/live-listings")
async def ebay_live_listings(request: Request) -> dict:
    """Every active listing on the connected eBay account (flagged from_ebay),
    for the inventory manager. Optional — the UI only calls this when the user
    turns on 'Sync all eBay listings' in Settings. Uses the Trading API for the
    full inventory, falling back to Inventory-API offers if that comes back
    empty (e.g. a keyset without Trading access)."""
    creds = _ebay_creds_for(request)
    if not creds:
        return {"listings": []}
    items = await run_in_threadpool(ebay.fetch_active_inventory, creds)
    if not items:
        items = await run_in_threadpool(ebay.fetch_live_listings, creds)
    return {"listings": items}


@app.post("/api/ebay/listing/{item_id}/revise")
async def ebay_revise_listing(item_id: str, request: Request, payload: dict) -> dict:
    """Update price and/or quantity of a live eBay listing (inventory manager)."""
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    price = payload.get("price", None)
    quantity = payload.get("quantity", None)
    try:
        price = float(price) if price is not None and price != "" else None
        quantity = int(quantity) if quantity is not None and quantity != "" else None
    except (TypeError, ValueError):
        raise HTTPException(400, "Price must be a number and quantity a whole number.")
    if price is None and quantity is None:
        raise HTTPException(400, "Nothing to update.")
    if price is not None and price <= 0:
        raise HTTPException(400, "Price must be greater than 0.")
    if quantity is not None and quantity < 0:
        raise HTTPException(400, "Quantity can't be negative.")
    result = await run_in_threadpool(
        ebay.revise_live_listing, creds, item_id, price, quantity)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message") or "Couldn't update the listing.")
    return result


@app.post("/api/ebay/listing/{item_id}/end")
async def ebay_end_listing(item_id: str, request: Request) -> dict:
    """End a live eBay listing (inventory manager)."""
    creds = _ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    result = await run_in_threadpool(ebay.end_item, creds, item_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("message") or "Couldn't end the listing.")
    return result


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
    services = (ebay_auth.fulfillment_policy_services(
                    creds["access_token"], fulfillment, timeout=8)
                if creds and fulfillment else [])

    required = None
    if config.taxonomy_ready() and (listing.category_id or "").strip().isdigit():
        try:
            asp = taxonomy.item_aspects(listing.category_id, timeout=8)
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
        except RuntimeError as exc:  # our own friendly message
            raise HTTPException(400, str(exc)) from exc
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


# Per-user listing defaults stored in users.prefs. Package defaults pre-fill
# the Shipping card when the AI didn't measure anything.
PREF_FIELDS = ("default_weight_lb", "default_weight_oz", "default_length_in",
               "default_width_in", "default_height_in")
BOOL_PREF_FIELDS = ("sync_ebay_listings", "auto_promote")


@app.post("/api/profile")
def save_profile(request: Request, payload: dict) -> dict:
    """Save profile customizations: display name and/or listing defaults."""
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in first.")
    kwargs: dict = {}
    if "display_name" in payload:
        kwargs["display_name"] = str(payload.get("display_name", "")).strip()[:80]
    prefs = {}
    for key in PREF_FIELDS:
        if key in payload:
            try:
                prefs[key] = max(0.0, float(payload[key] or 0))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} must be a number")
    for key in BOOL_PREF_FIELDS:
        if key in payload:
            prefs[key] = bool(payload[key])
    if prefs:
        kwargs["prefs"] = prefs
    if not kwargs:
        raise HTTPException(400, "Nothing to save.")
    updated = db.update_user(uid, **kwargs)
    if not updated:
        raise HTTPException(503, "Couldn't save your profile — try again shortly.")
    return {"ok": True, "user": updated}


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
    db.delete_ebay_account(uid)
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


MAX_UPLOAD_FILES = 12   # eBay allows up to 24 photos; keep memory bounded
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # per file


@app.post("/api/upload")
async def upload(
    files: list[UploadFile] = File(...),
    remove_bg: str = Form("false"),
    add_shadow: str = Form("false"),
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
    shadow = strip_bg and str(add_shadow).lower() in ("true", "1", "yes", "on")

    session_id = storage.new_session_id()
    orig = storage.original_dir(session_id)
    for i, f in enumerate(files):
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                400, f"'{f.filename or 'image'}' is too large (max 20MB per image)")
        suffix = Path(f.filename or f"upload_{i}").suffix or ".jpg"
        (orig / f"src_{i:02d}{suffix}").write_bytes(data)

    # Pillow work is CPU-bound and the R2 push is blocking I/O; run both off
    # the event loop so photo processing doesn't stall every other request.
    opt_results = await run_in_threadpool(
        images.optimize_all, orig, storage.optimized_dir(session_id),
        strip_bg, shadow)
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


@app.post("/api/edit-image")
async def edit_image(
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
        from PIL import Image as _Image

        img = _studio_load(session_id, name, data)
        res = images.analyze_cleanup(img)
        # The editor tints via canvas source-in, which keys on the ALPHA
        # channel — an opaque grayscale mask would tint the ENTIRE photo red.
        # Encode the mask as the alpha of an otherwise-solid PNG.
        mask_url = None
        if res["residue_pct"] > 0:
            m = res["residue_mask"]
            rgba = _Image.new("RGBA", m.size, (255, 255, 255, 0))
            rgba.putalpha(m)
            mask_url = _data_url(rgba, "PNG")
        return {
            "ok": True,
            "residue_pct": res["residue_pct"],
            "bbox": res["bbox"],
            "mask": mask_url,
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


def _apply_package_prefs(listing: Listing, uid: Optional[str],
                         prefs: Optional[dict] = None) -> None:
    """Auto-apply the account's saved package defaults (Settings → Package
    defaults) to a freshly identified listing, overriding the AI's estimate —
    the seller asked for these to be THE default; they can still edit any
    single listing. Zeros/blanks in prefs mean "not set" and change nothing."""
    if prefs is None:
        user = db.get_user_by_id(uid) if uid else None
        prefs = (user or {}).get("prefs") or {}

    def _pf(key: str) -> float:
        try:
            return max(0.0, float(prefs.get(key) or 0))
        except (TypeError, ValueError):
            return 0.0

    lb, oz = _pf("default_weight_lb"), _pf("default_weight_oz")
    if lb or oz:
        listing.package_weight_lb = lb
        listing.package_weight_oz = oz
    length, width, height = (_pf("default_length_in"), _pf("default_width_in"),
                             _pf("default_height_in"))
    if length and width and height:  # eBay needs all three or none
        listing.package_length_in = length
        listing.package_width_in = width
        listing.package_height_in = height


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
        result = claude_ai.identify(paths, names)
    except Exception as exc:  # noqa: BLE001 - surface the real reason to the UI
        raise HTTPException(502, f"AI identification failed: {exc}") from exc

    # Auto-correct photos the vision model flagged as sideways/upside-down. Purely
    # best-effort — the seller can rotate any photo manually in the editor — so a
    # rotation failure or a stray R2 re-push must never break identify.
    for name, deg in zip(names, result.orientations or []):
        if not deg:
            continue
        try:
            if images.rotate_saved(opt_dir / name, deg):
                log.info("identify: auto-rotated %s by %d° (session=%s)", name, deg, session_id)
                if objstore.enabled():
                    objstore.upload(opt_dir / name, objstore.key_for(session_id, name))
        except Exception as exc:  # noqa: BLE001
            log.warning("identify: auto-rotate failed (session=%s name=%s): %s",
                        session_id, name, exc)

    _apply_package_prefs(result.listing, _uid(request))

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
def price_suggestions(payload: dict) -> dict:
    """Market-price suggestion for the listing from live eBay comps.

    Uses the same application token as taxonomy (no seller login needed).
    Sources are pluggable — see services/pricing.py.
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
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"eBay price lookup failed: {exc}") from exc


@app.post("/api/refine")
def refine(req: RefineRequest, request: Request) -> dict:
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    updated = claude_ai.refine(req.listing, req.prompt)
    storage.save_listing(req.session_id, updated)
    db.upsert_listing(req.session_id, updated.model_dump(), status="draft", user_id=_uid(request))
    return updated.model_dump()


@app.post("/api/save/{session_id}")
def save_listing(session_id: str, listing: Listing, request: Request) -> dict:
    storage.save_listing(session_id, listing)
    db.upsert_listing(session_id, listing.model_dump(), status="draft", user_id=_uid(request))
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
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    if opt_dir not in path.parents:  # path-traversal guard
        raise HTTPException(400, "Invalid image name")
    if path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(500, f"Couldn't delete the image: {exc}") from exc
    if objstore.enabled():
        objstore.delete(objstore.key_for(session_id, name))
    log.info("delete-image: session=%s name=%s", session_id, name)
    return {"ok": True, "remaining": storage.list_optimized(session_id)}


# ---------- Bulk mode: one photo dump -> many listings ----------
# Jobs are in-memory: the app runs a single always-on machine (fly.toml), and a
# lost job only means re-running the upload — listings themselves persist.
_BULK_JOBS: dict[str, dict] = {}
_BULK_LOCK = threading.Lock()
BULK_MAX_FILES = 40


def _bulk_set(job_id: str, **fields) -> None:
    with _BULK_LOCK:
        job = _BULK_JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _run_bulk_job(job_id: str, staging_id: str, strip_bg: bool, add_shadow: bool,
                  mode: str, uid: Optional[str], creds: Optional[dict],
                  base_url: str) -> None:
    """Background worker: optimize -> group -> per-item identify (-> publish)."""
    try:
        _bulk_set(job_id, phase="optimizing")
        images.optimize_all(storage.original_dir(staging_id),
                            storage.optimized_dir(staging_id), strip_bg, add_shadow)
        names = storage.list_optimized(staging_id)
        if not names:
            _bulk_set(job_id, done=True, error="No usable photos in the upload.")
            return
        opt_dir = storage.optimized_dir(staging_id)

        _bulk_set(job_id, phase="grouping", total_photos=len(names))
        thumbs = [images.thumb_jpeg(opt_dir / n) for n in names]
        groups = claude_ai.group_photos(thumbs)["groups"]
        _bulk_set(job_id, total_items=len(groups))
        # Account package defaults, fetched once and applied to every item.
        pkg_prefs = ((db.get_user_by_id(uid) or {}).get("prefs") or {}) if uid else {}

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
                result = claude_ai.identify([item_dir / n for n in item_names], item_names)
                listing = result.listing
                _apply_package_prefs(listing, uid, prefs=pkg_prefs)
                if config.taxonomy_ready() and not listing.category_id:
                    try:
                        best = taxonomy.best_category_id(_category_query(listing))
                        if best.get("category_id"):
                            listing.category_id = best["category_id"]
                            if best.get("path"):
                                listing.category_suggestion = best["path"]
                    except Exception:  # noqa: BLE001
                        pass
                storage.save_listing(sid, listing)
                status = "draft"
                if mode == "live":
                    pub = ebay.publish(sid, listing, "live", base_url, creds=creds)
                    if pub.get("published"):
                        status = "published"
                        item["status"] = "published"
                        item["listing_id"] = pub.get("listing_id")
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


@app.post("/api/bulk/upload")
async def bulk_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    mode: str = Form("draft"),
    remove_bg: str = Form("false"),
    add_shadow: str = Form("false"),
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
    if len(files) > BULK_MAX_FILES:
        raise HTTPException(400, f"Too many files (max {BULK_MAX_FILES} in bulk mode)")

    staging_id = storage.new_session_id()
    orig = storage.original_dir(staging_id)
    for i, f in enumerate(files):
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                400, f"'{f.filename or 'image'}' is too large (max 20MB per image)")
        suffix = Path(f.filename or f"upload_{i}").suffix or ".jpg"
        (orig / f"src_{i:02d}{suffix}").write_bytes(data)

    # Capture per-request context now — the worker thread has no Request.
    uid = _uid(request)
    creds = _ebay_creds_for(request) if mode == "live" else None
    job_id = storage.new_session_id()
    with _BULK_LOCK:
        _BULK_JOBS[job_id] = {
            "id": job_id, "mode": mode, "phase": "uploading", "done": False,
            "error": None, "items": [], "total_items": 0, "current": 0,
            "total_photos": len(files),
        }
    threading.Thread(
        target=_run_bulk_job,
        args=(job_id, staging_id,
              str(remove_bg).lower() in ("true", "1", "yes", "on"),
              (str(remove_bg).lower() in ("true", "1", "yes", "on")
               and str(add_shadow).lower() in ("true", "1", "yes", "on")),
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
def delete_listing_route(listing_id: str, request: Request) -> dict:
    """Delete a draft/listing from QuickFlip. Best-effort withdraws a live eBay
    offer too, so a delete here doesn't leave an orphaned live listing."""
    uid = _uid(request)
    rec = db.get_listing(listing_id)
    if not rec:
        raise HTTPException(404, "Listing not found")
    if rec.get("user_id") and rec["user_id"] != uid:
        raise HTTPException(404, "Listing not found")
    # If it went live, try to end the eBay listing first (never block delete).
    if rec.get("status") in ("published", "live"):
        creds = _ebay_creds_for(request)
        if creds:
            try:
                ebay.withdraw(listing_id, rec.get("listing", {}), creds)
            except Exception as exc:  # noqa: BLE001
                log.warning("delete: eBay withdraw failed for %s: %s", listing_id, exc)
    ok = db.delete_listing(listing_id, user_id=uid)
    if not ok:
        raise HTTPException(400, "Couldn't delete that listing.")
    return {"ok": True}


@app.post("/api/publish")
def publish(req: PublishRequest, request: Request) -> JSONResponse:
    if req.mode not in ("draft", "live"):
        raise HTTPException(400, "mode must be 'draft' or 'live'")
    storage.save_listing(req.session_id, req.listing)
    creds = _ebay_creds_for(request)
    # Pre-publish checklist: catch everything eBay would reject BEFORE the
    # round-trip, with field-targeted fixes. Only gates a real (connected)
    # live publish — dry-runs and drafts stay permissive.
    import time as _time
    _t0 = _time.monotonic()
    if req.mode == "live" and (creds or config.ebay_ready()):
        problems = preflight.errors_only(_preflight_issues(request, req.listing, "live"))
        log.info("publish preflight took %.1fs (session=%s)",
                 _time.monotonic() - _t0, req.session_id)
        if problems:
            db.upsert_listing(req.session_id, req.listing.model_dump(),
                              status="draft", user_id=_uid(request))
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
    log.info("publish request: session=%s mode=%s connected=%s", req.session_id,
             req.mode, bool(creds))
    _t1 = _time.monotonic()
    result = ebay.publish(req.session_id, req.listing, req.mode, _base_url(request),
                          creds=creds)
    log.info("publish eBay round-trip took %.1fs (session=%s, total %.1fs)",
             _time.monotonic() - _t1, req.session_id, _time.monotonic() - _t0)
    # Record the outcome: published (live), draft, or dry-run.
    if result.get("published"):
        status = "published"
        log.info("publish OK: session=%s listing_id=%s", req.session_id, result.get("listing_id"))
        # Promoted Listings, best-effort (never fails the publish).
        if req.listing.promote_enabled and creds:
            try:
                result["promotion"] = ebay.promote(
                    req.session_id, req.listing, result.get("listing_id"), creds)
            except Exception as exc:  # noqa: BLE001
                log.warning("promote failed: %s", exc)
    elif result.get("error"):
        status = req.mode
        log.warning("publish error: session=%s step=%s", req.session_id, result.get("step"))
    elif result.get("dry_run"):
        status = "dry_run"
    else:
        status = req.mode
    db.upsert_listing(req.session_id, req.listing.model_dump(), status=status, user_id=_uid(request))
    return JSONResponse(result)


@app.get("/media/{session_id}/optimized/{name}")
def media(session_id: str, name: str):
    opt_dir = storage.optimized_dir(session_id).resolve()
    path = (opt_dir / name).resolve()
    # Guard against path traversal in `name` (e.g. "../../etc/passwd").
    if opt_dir not in path.parents:
        raise HTTPException(404, "Not found")
    if path.is_file():
        return FileResponse(path)
    # Local file gone (e.g. after a restart) — fall back to R2 if available.
    if objstore.enabled():
        return RedirectResponse(objstore.public_url(objstore.key_for(session_id, name)))
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

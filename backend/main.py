"""FastAPI application wiring the eBay listing pipeline together.

Pipeline:
  upload images -> optimize (Pillow) -> identify (Claude vision) ->
  edit/refine in preview -> publish (eBay, or dry-run).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, ebay_auth, objstore, storage
from .config import log
from .models import Listing, PublishRequest, RefineRequest
from .services import claude_ai, ebay, images, pricing, taxonomy

app = FastAPI(title="eBay Listing Generator")

FRONTEND_DIR = config.ROOT_DIR / "frontend"


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


@app.get("/api/ebay/connect")
def ebay_connect(request: Request):
    if not config.ebay_oauth_ready():
        raise HTTPException(400, "eBay OAuth not configured (EBAY_CLIENT_ID/SECRET/RUNAME).")
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in before connecting eBay.")
    return RedirectResponse(ebay_auth.authorize_url(state=auth.make_state(uid)))


@app.get("/api/ebay/callback")
def ebay_callback(request: Request, code: str = "", state: str = ""):
    uid = _uid(request) or auth.verify_state(state)
    if not code or not uid:
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
        return RedirectResponse("/?ebay=connected")
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
                400, f"'{f.filename or 'image'}' is too large (max 20MB per image)")
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
        from PIL import Image
        img = Image.open(BytesIO(data)).convert("RGB")
        img.save(path, "JPEG", quality=88, optimize=True)

    try:
        await run_in_threadpool(_save)
    except Exception as exc:  # noqa: BLE001
        log.warning("edit-image: could not process (session=%s name=%s): %s", session_id, name, exc)
        raise HTTPException(400, f"Could not process the edited image: {exc}") from exc
    await run_in_threadpool(
        objstore.upload_optimized, session_id, opt_dir, [name])
    log.info("edit-image saved: session=%s name=%s", session_id, name)
    return {"ok": True, "name": name}


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


@app.post("/api/publish")
def publish(req: PublishRequest, request: Request) -> JSONResponse:
    if req.mode not in ("draft", "live"):
        raise HTTPException(400, "mode must be 'draft' or 'live'")
    storage.save_listing(req.session_id, req.listing)
    creds = _ebay_creds_for(request)
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
    result = ebay.publish(req.session_id, req.listing, req.mode, _base_url(request),
                          creds=creds)
    # Record the outcome: published (live), draft, or dry-run.
    if result.get("published"):
        status = "published"
        log.info("publish OK: session=%s listing_id=%s", req.session_id, result.get("listing_id"))
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

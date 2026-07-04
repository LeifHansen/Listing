"""FastAPI application wiring the eBay listing pipeline together.

Pipeline:
  upload images -> optimize (Pillow) -> identify (Claude vision) ->
  edit/refine in preview -> publish (eBay, or dry-run).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, db, ebay_auth, objstore, storage
from .models import Listing, PublishRequest, RefineRequest
from .services import claude_ai, ebay, images, taxonomy

app = FastAPI(title="eBay Listing Generator")

FRONTEND_DIR = config.ROOT_DIR / "frontend"


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
    if not user:
        raise HTTPException(409, "An account with that email already exists")
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
    except Exception:  # noqa: BLE001
        return None
    return {
        "access_token": fresh["access_token"],
        "fulfillment_policy_id": acct.get("fulfillment_policy_id", ""),
        "payment_policy_id": acct.get("payment_policy_id", ""),
        "return_policy_id": acct.get("return_policy_id", ""),
        "merchant_location_key": acct.get("merchant_location_key", ""),
    }


@app.get("/api/ebay/connect")
def ebay_connect(request: Request):
    if not config.ebay_oauth_ready():
        raise HTTPException(400, "eBay OAuth not configured (EBAY_CLIENT_ID/SECRET/RUNAME).")
    uid = _uid(request)
    if not uid:
        raise HTTPException(401, "Log in before connecting eBay.")
    return RedirectResponse(ebay_auth.authorize_url(state=uid))


@app.get("/api/ebay/callback")
def ebay_callback(request: Request, code: str = "", state: str = ""):
    uid = _uid(request) or state
    if not code or not uid:
        return RedirectResponse("/?ebay=error")
    try:
        tokens = ebay_auth.exchange_code(code)
        policies = ebay_auth.fetch_policies_and_location(tokens["access_token"])
        db.save_ebay_account(uid, refresh_token=tokens["refresh_token"], **policies)
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
        "policies": {
            "fulfillment": bool(acct and acct.get("fulfillment_policy_id")),
            "payment": bool(acct and acct.get("payment_policy_id")),
            "return": bool(acct and acct.get("return_policy_id")),
            "location": bool(acct and acct.get("merchant_location_key")),
        } if connected else {},
    }


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict:
    """Accept images, optimize them, and return a session id."""
    if not files:
        raise HTTPException(400, "No files uploaded")

    session_id = storage.new_session_id()
    orig = storage.original_dir(session_id)
    for i, f in enumerate(files):
        suffix = Path(f.filename or f"upload_{i}").suffix or ".jpg"
        dest = orig / f"src_{i:02d}{suffix}"
        dest.write_bytes(await f.read())

    opt_results = images.optimize_all(orig, storage.optimized_dir(session_id))
    optimized = storage.list_optimized(session_id)
    if not optimized:
        errs = "; ".join(r["error"] for r in opt_results if r.get("error"))
        raise HTTPException(
            400,
            "Could not process the uploaded image(s)"
            + (f": {errs}" if errs else ". Unsupported or corrupt file format."),
        )
    # Push optimized images to durable object storage (R2) when configured.
    objstore.upload_optimized(session_id, storage.optimized_dir(session_id), optimized)
    return {
        "session_id": session_id,
        "optimized": optimized,
        "optimize_results": opt_results,
    }


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
    result = ebay.publish(req.session_id, req.listing, req.mode, _base_url(request),
                          creds=_ebay_creds_for(request))
    # Record the outcome: published (live), draft, or dry-run.
    if result.get("published"):
        status = "published"
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


# Serve the frontend (index.html + assets) at the root.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

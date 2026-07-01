"""FastAPI application wiring the eBay listing pipeline together.

Pipeline:
  upload images -> optimize (Pillow) -> identify (Claude vision) ->
  edit/refine in preview -> publish (eBay, or dry-run).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, db, storage
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
    return {"user": user}


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
    return {"user": user}


@app.post("/api/auth/logout")
def auth_logout(response: Response) -> dict:
    auth.clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    return {"user": auth.current_user(request)}


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
    result = ebay.publish(req.session_id, req.listing, req.mode, _base_url(request))
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
    if opt_dir not in path.parents or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)


# Serve the frontend (index.html + assets) at the root.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

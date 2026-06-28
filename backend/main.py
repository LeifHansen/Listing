"""FastAPI application wiring the eBay listing pipeline together.

Pipeline:
  upload images -> optimize (Pillow) -> identify (Claude vision) ->
  edit/refine in preview -> publish (eBay, or dry-run).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, storage
from .models import Listing, PublishRequest, RefineRequest
from .services import claude_ai, ebay, images

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
        "ebay_env": config.EBAY_ENV,
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
    return {
        "session_id": session_id,
        "optimized": optimized,
        "optimize_results": opt_results,
    }


@app.post("/api/identify/{session_id}")
def identify(session_id: str) -> dict:
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
    result = claude_ai.identify(paths, names)
    storage.save_listing(session_id, result.listing)
    return result.model_dump()


@app.post("/api/refine")
def refine(req: RefineRequest) -> dict:
    if not config.anthropic_ready():
        raise HTTPException(400, "ANTHROPIC_API_KEY not configured.")
    updated = claude_ai.refine(req.listing, req.prompt)
    storage.save_listing(req.session_id, updated)
    return updated.model_dump()


@app.post("/api/save/{session_id}")
def save_listing(session_id: str, listing: Listing) -> dict:
    storage.save_listing(session_id, listing)
    return {"saved": True}


@app.post("/api/publish")
def publish(req: PublishRequest, request: Request) -> JSONResponse:
    if req.mode not in ("draft", "live"):
        raise HTTPException(400, "mode must be 'draft' or 'live'")
    storage.save_listing(req.session_id, req.listing)
    result = ebay.publish(req.session_id, req.listing, req.mode, _base_url(request))
    return JSONResponse(result)


@app.get("/media/{session_id}/optimized/{name}")
def media(session_id: str, name: str):
    path = storage.optimized_dir(session_id) / name
    if not path.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(path)


# Serve the frontend (index.html + assets) at the root.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

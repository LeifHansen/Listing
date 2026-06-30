"""Tiny filesystem-backed session store.

Each session gets a directory under data/sessions/<id>/ containing:
  original/   - uploaded source images
  optimized/  - processed images served to the UI
  listing.json - the latest saved listing draft
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import config
from .models import Listing


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def session_dir(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum())
    if not safe:
        raise ValueError("invalid session id")
    return config.SESSIONS_DIR / safe


def ensure_session(session_id: str) -> Path:
    d = session_dir(session_id)
    (d / "original").mkdir(parents=True, exist_ok=True)
    (d / "optimized").mkdir(parents=True, exist_ok=True)
    return d


def original_dir(session_id: str) -> Path:
    return ensure_session(session_id) / "original"


def optimized_dir(session_id: str) -> Path:
    return ensure_session(session_id) / "optimized"


def save_listing(session_id: str, listing: Listing) -> None:
    d = ensure_session(session_id)
    (d / "listing.json").write_text(listing.model_dump_json(indent=2))


def list_optimized(session_id: str) -> list[str]:
    d = optimized_dir(session_id)
    return sorted(p.name for p in d.glob("*") if p.is_file())


def write_export(session_id: str, name: str, payload: dict) -> Path:
    config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.EXPORTS_DIR / f"{session_id}_{name}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path

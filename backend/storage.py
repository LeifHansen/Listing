"""Tiny filesystem-backed session store.

Each session gets a directory under data/sessions/<id>/ containing:
  original/   - uploaded source images
  optimized/  - processed images served to the UI
  listing.json - the latest saved listing draft
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from . import config
from .config import log
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


def purge_session(session_id: str) -> None:
    """Delete a session's whole directory (best-effort). Used to drop bulk
    staging once its job is done, so it doesn't accumulate on the volume."""
    try:
        d = session_dir(session_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - cleanup must never raise
        log.warning(f"storage: purge_session failed for {session_id}: {exc}")


def sweep_orphan_sessions(valid_ids: set[str], max_age_seconds: int) -> int:
    """Delete session dirs that aren't a known listing and haven't been touched
    in `max_age_seconds` — i.e. leftover bulk staging and abandoned uploads that
    were never saved. This reclaims volume space (bulk staging was never cleaned
    up, growing until writes fail with a 500). Returns how many were removed.
    Never raises. The caller MUST pass a real id set (never on a DB outage), or
    live listings' images would look like orphans."""
    removed = 0
    try:
        base = config.SESSIONS_DIR
        if not base.exists():
            return 0
        cutoff = time.time() - max_age_seconds
        for d in base.iterdir():
            try:
                if not d.is_dir() or d.name in valid_ids:
                    continue
                if d.stat().st_mtime > cutoff:
                    continue  # too recent — may be an in-flight upload/session
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
            except Exception:  # noqa: BLE001 - keep sweeping the rest
                continue
    except Exception as exc:  # noqa: BLE001
        log.warning(f"storage: orphan sweep failed: {exc}")
    return removed

"""Tiny filesystem-backed session store.

Each session gets a directory under data/sessions/<id>/ containing:
  original/   - uploaded source images
  optimized/  - processed images served to the UI
  listing.json - the latest saved listing draft
"""
from __future__ import annotations

import json
import re
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


def optimized_path(session_id: str) -> Path:
    """The optimized dir WITHOUT creating anything — for read-only callers
    (the public /media route, studio loads, listings): a GET must never be a
    disk write, and re-creating empty dirs for purged sessions kept undoing
    the orphan sweep."""
    return session_dir(session_id) / "optimized"


def history_dir(session_id: str) -> Path:
    d = session_dir(session_id) / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_image(session_id: str, name: str, keep: int = 10) -> None:
    """Preserve the current working copy of a photo before it's overwritten —
    edits never destroy a version. Snapshots land in history/<name>.<ms>.jpg,
    oldest pruned beyond `keep` per photo. Best-effort: a failed snapshot must
    never block the save itself."""
    try:
        src = optimized_dir(session_id) / name
        if not src.is_file():
            return
        hist = history_dir(session_id)
        shutil.copy2(src, hist / f"{name}.{int(time.time() * 1000)}")
        stem = f"{name}."
        old = sorted((p for p in hist.iterdir() if p.name.startswith(stem)),
                     key=lambda p: p.name)
        for p in old[:-keep]:
            p.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"storage: snapshot failed for {session_id}/{name}: {exc}")


def save_listing(session_id: str, listing: Listing) -> None:
    d = ensure_session(session_id)
    (d / "listing.json").write_text(listing.model_dump_json(indent=2))


def natural_key(name: str) -> list:
    """Sort key that orders embedded numbers numerically, so "img_2" comes
    before "img_10". Plain lexicographic sorting scrambles photo order past
    file 99 ("img_100" < "img_20"), which matters to bulk batches of up to
    250 photos: grouping leans on shooting order to keep one item's shots
    adjacent."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def list_optimized(session_id: str) -> list[str]:
    d = optimized_path(session_id)  # read-only — never mkdir on a lookup
    if not d.is_dir():
        return []
    return sorted((p.name for p in d.glob("*") if p.is_file()), key=natural_key)


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

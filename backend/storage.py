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


def safe_session_name(session_id: str) -> str:
    """The canonical name for a session id: alphanumerics only.

    This is THE naming rule for everything keyed by session — the on-disk
    directory and the R2 object key alike. Imported listings carry ids like
    "ebay-123", so any keyer that skips this rule ends up with split-brain
    names (dir "ebay123" vs key ".../ebay-123/...") that no lookup ever
    reunites. Raises ValueError when nothing usable remains."""
    safe = "".join(c for c in session_id if c.isalnum())
    if not safe:
        raise ValueError("invalid session id")
    return safe


def session_dir(session_id: str) -> Path:
    return config.SESSIONS_DIR / safe_session_name(session_id)


def image_index(name: str) -> int:
    """The N in "img_NNN.jpg", or -1 for anything else. Used to mint the next
    non-colliding filename when photos are added to an existing session."""
    try:
        return int(name.replace("img_", "").replace(".jpg", ""))
    except ValueError:
        return -1


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


def disk_free_bytes() -> int:
    """Free bytes on the volume holding the session store (0 if unknown)."""
    try:
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        st = shutil.disk_usage(config.SESSIONS_DIR)
        return int(st.free)
    except Exception as exc:  # noqa: BLE001 - a stat failure must not break a request
        log.warning(f"storage: disk_usage failed: {exc}")
        return 0


def prune_originals(max_age_seconds: int) -> int:
    """Delete source uploads (session original/ dirs) older than the cutoff.

    Nothing reads these after the optimize pass — the optimized JPEGs are what
    the app, the browser, and eBay use — but they're the BIGGEST thing on the
    volume: a phone photo is several MB against a few hundred KB optimized. A
    full volume takes the whole app down ("No space left on device" on every
    upload), so old originals are reclaimed on a timer. Returns bytes freed.
    Never raises."""
    freed = 0
    try:
        base = config.SESSIONS_DIR
        if not base.exists():
            return 0
        cutoff = time.time() - max_age_seconds
        for d in base.iterdir():
            orig = d / "original"
            try:
                if not orig.is_dir():
                    continue
                for p in orig.iterdir():
                    if p.is_file() and p.stat().st_mtime <= cutoff:
                        size = p.stat().st_size
                        p.unlink(missing_ok=True)
                        freed += size
            except Exception:  # noqa: BLE001 - keep pruning the rest
                continue
    except Exception as exc:  # noqa: BLE001
        log.warning(f"storage: prune_originals failed: {exc}")
    return freed


def prune_history(max_age_seconds: int) -> int:
    """Delete edit snapshots (session history/) older than the cutoff. Recent
    versions stay; ancient ones aren't worth a full volume. Returns bytes
    freed. Never raises."""
    freed = 0
    try:
        base = config.SESSIONS_DIR
        if not base.exists():
            return 0
        cutoff = time.time() - max_age_seconds
        for d in base.iterdir():
            hist = d / "history"
            try:
                if not hist.is_dir():
                    continue
                for p in hist.iterdir():
                    if p.is_file() and p.stat().st_mtime <= cutoff:
                        size = p.stat().st_size
                        p.unlink(missing_ok=True)
                        freed += size
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        log.warning(f"storage: prune_history failed: {exc}")
    return freed


def prune_exports(max_age_seconds: int) -> int:
    """Delete dry-run/export payloads older than the cutoff. These are debug
    artifacts written on every dry-run publish and never read again, but they
    accumulated into the second-largest thing on the volume. Returns bytes
    freed. Never raises."""
    freed = 0
    try:
        base = config.EXPORTS_DIR
        if not base.exists():
            return 0
        cutoff = time.time() - max_age_seconds
        for p in base.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime <= cutoff:
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    freed += size
            except Exception:  # noqa: BLE001 - keep pruning the rest
                continue
    except Exception as exc:  # noqa: BLE001
        log.warning(f"storage: prune_exports failed: {exc}")
    return freed


def sweep_orphan_sessions(valid_ids: set[str], max_age_seconds: int) -> list[str]:
    """Delete session dirs that aren't a known listing and haven't been touched
    in `max_age_seconds` — i.e. leftover bulk staging and abandoned uploads that
    were never saved. This reclaims volume space (bulk staging was never cleaned
    up, growing until writes fail with a 500). Returns the removed dir names.
    Never raises. The caller MUST pass a real id set (never on a DB outage), or
    live listings' images would look like orphans.

    Returns names (not just a count) because the same photos may already have
    been mirrored to R2 — an upload reaches the bucket before any listing row
    exists — and the caller has to purge them there too. Nothing else can:
    with no listing row, no id set and no user record ever names them again.
    """
    removed: list[str] = []
    try:
        base = config.SESSIONS_DIR
        if not base.exists():
            return removed
        cutoff = time.time() - max_age_seconds
        for d in base.iterdir():
            try:
                if not d.is_dir() or d.name in valid_ids:
                    continue
                if d.stat().st_mtime > cutoff:
                    continue  # too recent — may be an in-flight upload/session
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
            except Exception:  # noqa: BLE001 - keep sweeping the rest
                continue
    except Exception as exc:  # noqa: BLE001
        log.warning(f"storage: orphan sweep failed: {exc}")
    return removed

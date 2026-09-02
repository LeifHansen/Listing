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
from .errors import InvalidSessionId
from .models import Listing


def new_session_id() -> str:
    """A fresh session id: a full uuid4 hex, not a 12-character prefix.

    12 hex characters is 48 bits with no uniqueness check against the
    database. Ids are not the security boundary — they travel in public
    /media URLs by design, and the guard is _assert_session_owner — but a
    birthday collision would silently merge two sellers' photos into one
    directory, which no error would ever report.
    """
    return uuid.uuid4().hex


# What a session id may contain. Leading character is alphanumeric so an id
# can never start with "-" or "_" and be mistaken for a flag or a hidden file;
# the rest also allows "-" and "_" because imported listings are minted as
# "ebay-<item id>" (services/listing_sync.py) and job mirrors use "_".
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def safe_session_name(session_id: str) -> str:
    """The canonical name for a session id — which is the id itself.

    This is THE naming rule for everything keyed by session: the on-disk
    directory and the R2 object key alike. Split naming is how imported
    listings' photos once became invisible to the offload sweep (dir
    "ebay123" vs key ".../ebay-123/..."), so there is exactly one rule.

    It REJECTS rather than rewrites, and that is the security property. The
    old rule deleted every non-alphanumeric character, which made the mapping
    lossy: "abc123" and "abc123-" were different database rows and the same
    directory. The ownership guard asks the database and the file operation
    asks storage, so an id with one character appended missed the row —
    reading as "unowned, allow" — and then landed in the victim's directory.
    Session ids are in every public /media URL, so knowing one is ordinary.

    Accepting or rejecting makes the mapping injective: name(x) == name(y)
    now implies x == y, so an alias cannot be constructed at all. Raises
    ValueError on anything outside the accepted form.
    """
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        # InvalidSessionId is a ValueError, so callers that already catch one
        # are unchanged; the distinct type is what lets the API answer 400
        # instead of letting this escape as a 500.
        raise InvalidSessionId("invalid session id")
    return session_id


def legacy_session_name(session_id: str) -> str:
    """The name this session's files were stored under BEFORE the rule above.

    Used ONLY by the one-shot migration (scripts/migrate_session_ids.py),
    which walks the real session ids in the database and renames each one's
    directory and R2 prefix into the canonical name.

    Deliberately NOT consulted on the request path. A read that fell back to
    this name would reintroduce the whole bug: "3aaeb40637a1-" is a perfectly
    valid id under the rule above, it has no directory of its own, and its
    legacy name is "3aaeb40637a1" — so the fallback would hand the caller the
    victim's photos again, through the front door this time. The migration is
    safe because it is driven by ids that actually exist as rows, not by
    whatever a request asks for.
    """
    return "".join(c for c in (session_id or "") if c.isalnum())


def session_dir(session_id: str) -> Path:
    """The one directory this session's files live in. No fallbacks."""
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


def load_listing(session_id: str) -> dict | None:
    """The saved draft as a plain dict, or None when this session has none.

    The mirror of save_listing, and the reason it exists: a database is
    OPTIONAL (README: set DATABASE_URL "to persist every listing"), so the
    routes that read-modify-write a listing cannot treat "no row" as "no
    listing" -- on a machine without a database that is every listing, and
    on one with a database it is any listing whose upsert never landed.

    Never raises: an unreadable or half-written listing.json answers None,
    which leaves the caller where it would have been without a disk copy.
    """
    try:
        raw = (session_dir(session_id) / "listing.json").read_text()
    except (OSError, InvalidSessionId):
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        log.warning(f"storage: listing.json unreadable for {session_id}: {exc}")
        return None
    return data if isinstance(data, dict) else None


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
    """Write a dry-run payload under EXPORTS_DIR.

    The session id goes through safe_session_name like every other path keyed
    by session. It is the ONE place that skipped it, and the id here arrives
    straight from the request body: `POST /api/publish` needs no login, and an
    unconnected app falls through to the dry run, so a session_id of
    "../../etc/cron.d/x" wrote an attacker-controlled JSON file anywhere the
    process could reach — as root, since the image sets no USER.

    The containment check below is deliberate belt-and-braces: it costs one
    resolve and it is what makes "this path cannot escape" true by
    construction rather than by trusting the sanitizer above it.
    """
    config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_") or "export"
    path = config.EXPORTS_DIR / f"{safe_session_name(session_id)}_{safe_name}.json"
    root = config.EXPORTS_DIR.resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError("export path escapes the exports directory")
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


def writable() -> bool:
    """Can the session store actually be written to right now?

    Existence is not the question — a read-only remount or a full volume both
    leave the directory sitting there looking fine, and the first thing to
    notice is a seller's upload failing halfway through a batch. So this
    writes a byte and deletes it.
    """
    try:
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.SESSIONS_DIR / ".write-probe"
        probe.write_bytes(b"1")
        probe.unlink()
        return True
    except Exception as exc:  # noqa: BLE001 - a probe failure IS the answer
        log.warning(f"storage: not writable: {exc}")
        return False


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


def session_touched_at(d: Path) -> float:
    """When anything in this session last changed.

    A session's own mtime is NOT that. A directory's mtime only moves when an
    entry is added or removed from it directly, and photos land one level down
    in original/ and optimized/ — so a session dir's mtime is really "when the
    session was created", and it never moves again no matter how long the batch
    runs. The sweep used that as its idle test, which meant a batch still
    working through its photos looked idle from the moment it passed the age
    cutoff, and could have the staging photos it was mid-way through reading
    deleted out from under it.

    Checking the immediate children fixes it, because adding a photo to
    original/ does move original/'s mtime. One level deep is enough for the
    shape we actually write, and keeps this to a handful of stats per session.
    """
    times = [d.stat().st_mtime]
    try:
        times.extend(child.stat().st_mtime for child in d.iterdir())
    except OSError:  # vanished mid-scan, or unreadable — the dir's own is fine
        pass
    return max(times)


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
                if session_touched_at(d) > cutoff:
                    continue  # too recent — may be an in-flight upload/session
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
            except Exception:  # noqa: BLE001 - keep sweeping the rest
                continue
    except Exception as exc:  # noqa: BLE001
        log.warning(f"storage: orphan sweep failed: {exc}")
    return removed

"""Status store for the background jobs the client polls.

Bulk uploads and async identifies both run in a worker thread while the browser
polls ``/api/bulk/status/{job_id}``. The status lives in memory, mirrored to a
small file per job so that a restart leaves an ANSWER rather than a hole.

That mirror is the whole point of this module. The status dict used to be
process memory alone, so a machine that went away mid-batch (an OOM-kill during
background removal, a deploy) took the job with it: every later poll 404'd, and
the client had no way to tell an interrupted batch from a mistyped id — it sat
on a progress bar frozen at the photo the batch died on, waiting for a job that
no longer existed. A mirrored job comes back from a restart marked finished,
carrying the phase and count it stopped at.

The mirror is deliberately NOT a resume log: it holds status only, never the
drafted items or the identify result. Those are saved as listings as they are
produced and survive on their own, and re-writing a 250-photo batch's payload
on every progress tick would churn megabytes through the volume for data
nothing reads back.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .. import config, storage
from ..config import log

_JOBS: dict[str, dict] = {}
# job id -> (revision, serialized body). Clients poll every 1.5s and a 30-item
# batch is a ~1MB dict, so the JSON is built once per CHANGE rather than once
# per poll — this runs on the same shared CPUs as photo inference.
_SNAPSHOTS: dict[str, tuple[int, str]] = {}
_LOCK = threading.Lock()

# Bound the in-memory store. An identify runs as a job on EVERY upload, so
# without eviction this dict would grow monotonically until restart.
JOBS_MAX = 200
# The fields worth mirroring: enough to explain how a job ended, and the owner
# so the privacy rule survives the restart too.
MIRROR_FIELDS = (
    "id", "kind", "phase", "done", "error", "current", "total_items",
    "total_photos", "bg_error", "bg_failed", "tokens_exhausted", "_uid",
)
# Mirrors are a few hundred bytes each and only ever read at startup, so that
# is where they're pruned — by age, then by count. A machine that never
# restarts never prunes, which at this size beats scanning a directory on the
# upload path.
MIRROR_TTL = 7 * 86400
MIRROR_MAX = 500


def _mirror_dir() -> Path:
    # Resolved per call, not at import: config.DATA_DIR is reloadable (tests),
    # and this must never be captured against a stale data root.
    return config.DATA_DIR / "jobs"


def _mirror_path(job_id: str) -> Path:
    return _mirror_dir() / f"{storage.safe_session_name(job_id)}.json"


def _write_mirror(job_id: str, record: dict) -> None:
    """Write one job's mirror. Best-effort and never raises — a batch must not
    fail because the volume is full or read-only. The write is atomic: progress
    ticks arrive from several worker threads at once, and a half-written file
    would come back from a restart as an unreadable record."""
    try:
        _mirror_dir().mkdir(parents=True, exist_ok=True)
        path = _mirror_path(job_id)
        tmp = path.with_suffix(f".{threading.get_ident():x}.tmp")
        tmp.write_text(json.dumps(record))
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 - bookkeeping, never the work itself
        log.debug("jobstore: couldn't mirror job %s: %s", job_id, exc)


def register(job_id: str, data: dict, uid: Optional[str] = None) -> None:
    """Start tracking a job. `uid` is the account that started it; job status
    carries drafted titles and prices, so a logged-in user's job is readable
    only by them (see `snapshot`). Anonymous jobs have no owner and stay
    readable by whoever holds the id, matching how the rest of the app treats
    logged-out sessions."""
    with _LOCK:
        job = {**data, "id": job_id, "_uid": uid, "_rev": 0}
        _JOBS[job_id] = job
        _SNAPSHOTS.pop(job_id, None)
        # Evict FINISHED jobs first. Dicts keep insertion order, and dropping
        # the oldest blindly could evict a bulk batch that is still running —
        # its own poller would then be told the batch doesn't exist, which is
        # the failure this module exists to prevent.
        while len(_JOBS) > JOBS_MAX:
            finished = next((k for k, v in _JOBS.items() if v.get("done")), None)
            dropped = finished if finished is not None else next(iter(_JOBS))
            _JOBS.pop(dropped)
            _SNAPSHOTS.pop(dropped, None)
        record = _record(job)
    _write_mirror(job_id, record)


def update(job_id: str, **fields) -> None:
    """Merge `fields` into a job's status. A no-op for an unknown job."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["_rev"] = job.get("_rev", 0) + 1
        # Snapshot under the lock, write outside it: the mirror is disk I/O and
        # every progress tick in the batch would otherwise queue behind it.
        record = _record(job)
    _write_mirror(job_id, record)


def snapshot(job_id: str, uid: Optional[str] = None) -> Optional[dict]:
    """A caller-safe copy of a job's status, or None when `uid` may not see it.

    None covers both "no such job" and "someone else's job" on purpose: the
    endpoint answers 404 either way rather than confirming that an id exists.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        owner = job.get("_uid")
        if owner and owner != uid:
            return None
        copy = json.loads(json.dumps(job))  # deep copy, safe to hand out
    # Every underscore key is internal bookkeeping (_uid, _rev), not API.
    return {k: v for k, v in copy.items() if not k.startswith("_")}


def snapshot_json(job_id: str, uid: Optional[str] = None) -> Optional[str]:
    """`snapshot` as a ready-to-serve JSON body, cached until the job changes.
    Same None semantics (unknown job or not yours)."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        owner = job.get("_uid")
        if owner and owner != uid:
            return None
        rev = job.get("_rev", 0)
        hit = _SNAPSHOTS.get(job_id)
        if hit is None or hit[0] != rev:
            public = {k: v for k, v in job.items() if not k.startswith("_")}
            hit = (rev, json.dumps(public))
            _SNAPSHOTS[job_id] = hit
        return hit[1]


def _record(job: dict) -> dict:
    return {k: job[k] for k in MIRROR_FIELDS if k in job}


def interrupted_message(record: dict) -> str:
    """Why a mirrored job that was still running has no result: the process it
    ran in went away. Names the phase and count it reached, so the answer is
    "it stopped optimizing photo 37 of 38" rather than a shrug."""
    if record.get("kind") == "identify":
        # A single item, not a pile — there is no partial batch to salvage and
        # nothing was written, so don't send the seller looking in Drafts.
        return ("The server restarted before the AI finished this item, so no "
                "draft was saved. Please try again.")
    phase = record.get("phase")
    current = record.get("current") or 0
    photos = record.get("total_photos") or 0
    items = record.get("total_items") or 0
    if phase == "optimizing" and photos:
        where = f" while optimizing photo {min(current, photos)} of {photos}"
    elif phase == "identifying" and items:
        where = f" while identifying item {min(current, items)} of {items}"
    elif phase == "grouping":
        where = " while sorting the photos into items"
    elif phase == "uploading":
        where = " while receiving the photos"
    else:
        where = ""
    return (f"The server restarted{where}, so this batch stopped early. "
            "Anything it finished is saved in Drafts — please run the rest "
            "again.")


def adopt_mirrors() -> int:
    """Load mirrored jobs back into memory at startup and return how many were
    interrupted. A job still marked running died with the process that owned
    it, so it is adopted as a finished job carrying that explanation.

    Two rules keep this from ever destroying a live job: a job already in
    memory is left alone (memory is always fresher than its own mirror), and
    the interrupted verdict is applied in memory only, never written back. The
    mirror stays a record of last-known state, which is what makes the verdict
    safe to re-derive on the next restart — and what stops this pass from
    rewriting a job that some other process is still working on."""
    try:
        files = sorted(_mirror_dir().glob("*.json"), key=lambda p: p.stat().st_mtime)
        # Half-written mirrors from the crash we're recovering from: os.replace
        # never got to rename them, and nothing else ever will.
        strays = list(_mirror_dir().glob("*.tmp"))
    except Exception as exc:  # noqa: BLE001 - no mirror dir yet, or unreadable
        log.debug("jobstore: no job mirrors to load (%s)", exc)
        return 0
    cutoff = time.time() - MIRROR_TTL
    fresh = [p for p in files if p.stat().st_mtime > cutoff]
    for path in strays + [p for p in files if p not in fresh] + fresh[:-MIRROR_MAX]:
        path.unlink(missing_ok=True)
    interrupted = 0
    for path in fresh[-JOBS_MAX:]:
        try:
            record = json.loads(path.read_text())
        except Exception:  # noqa: BLE001 - a torn or hand-edited file
            continue
        if not isinstance(record, dict) or not record.get("id"):
            continue
        with _LOCK:
            if record["id"] in _JOBS:
                continue  # live in this process — the mirror is the stale copy
            if not record.get("done"):
                record["done"] = True
                record["error"] = record.get("error") or interrupted_message(record)
                interrupted += 1
            _JOBS[record["id"]] = record
    if _JOBS:
        log.info("jobstore: adopted %d job record(s) from disk (%d interrupted "
                 "by the restart)", len(_JOBS), interrupted)
    return interrupted


def reset() -> None:
    """Drop everything held in memory. For tests — the store is process-wide."""
    with _LOCK:
        _JOBS.clear()
        _SNAPSHOTS.clear()

"""Turn /api/health into a pass/fail for the Health watch workflow.

Kept out of the workflow YAML so it can be read and changed without fighting
block-scalar indentation, and so the thresholds are reviewable in a diff.
Exits non-zero (failing the run, which notifies) on anything meaning a seller
is about to hit a wall.
"""
from __future__ import annotations

import json
import sys


def load(path: str) -> dict | None:
    """The body, or None if it isn't the JSON we expect.

    Fly's edge serves an HTML error page when the machine is down or wedged --
    the single most important case this alarm covers -- so a bare json.load
    would answer it with a decoder traceback instead of a readable message.
    """
    try:
        with open(path) as fh:
            body = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::/api/health did not return JSON ({exc}) - "
              "the app is probably down or serving an edge error page")
        return None
    if not isinstance(body, dict):
        print(f"::error::/api/health returned {type(body).__name__}, not an object")
        return None
    return body


def problems(health: dict, min_free_mb: int) -> list[str]:
    found = []

    free = health.get("disk_free_mb")
    if not isinstance(free, int):
        found.append(f"disk_free_mb missing or not a number: {free!r}")
    elif free < min_free_mb:
        found.append(
            f"only {free}MB free on the volume (want >= {min_free_mb}MB) - "
            "extend it or reclaim before a batch hits ENOSPC")

    # A swallowed R2 error means photos are silently not reaching the bucket.
    # The volume then cannot be reclaimed and publishes start failing.
    if health.get("objstore_error"):
        found.append(f"objstore_error: {health['objstore_error']}")
    elif not health.get("objstore_configured"):
        # Only a real credential gap, not the latch: objstore.enabled() also
        # goes false for 600s after any init failure, and reporting that as
        # "not configured; missing=[]" sends the operator hunting for
        # variables that are all present. objstore_error covers the latch.
        found.append(
            f"object storage not configured; missing={health.get('objstore_missing')}")

    db = health.get("db") or {}
    if db.get("configured") and not db.get("connected"):
        found.append(f"database not connected: {db}")

    return found


def main(path: str, min_free_mb: int) -> int:
    health = load(path)
    if health is None:
        return 1
    found = problems(health, min_free_mb)
    for p in found:
        print(f"::error::{p}")
    print(f"disk_free_mb={health.get('disk_free_mb')} "
          f"storage={health.get('storage')} "
          f"db_connected={(health.get('db') or {}).get('connected')}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2])))

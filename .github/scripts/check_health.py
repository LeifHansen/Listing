"""Turn /api/health into a pass/fail for the Health watch workflow.

Kept out of the workflow YAML so it can be read and changed without fighting
block-scalar indentation, and so the thresholds are reviewable in a diff.
Exits non-zero (failing the run, which notifies) on anything that means a
seller is about to hit a wall.
"""
from __future__ import annotations

import json
import sys


def main(path: str, min_free_mb: int) -> int:
    health = json.load(open(path))
    problems = []

    free = health.get("disk_free_mb")
    if not isinstance(free, int):
        problems.append(f"disk_free_mb missing or not a number: {free!r}")
    elif free < min_free_mb:
        problems.append(
            f"only {free}MB free on the volume (want >= {min_free_mb}MB) - "
            "extend it or reclaim before a batch hits ENOSPC")

    # A swallowed R2 error means photos are silently not reaching the bucket.
    # The volume then cannot be reclaimed and publishes start failing.
    if health.get("objstore_error"):
        problems.append(f"objstore_error: {health['objstore_error']}")
    if not health.get("objstore_configured"):
        problems.append(
            f"object storage not configured; missing={health.get('objstore_missing')}")

    db = health.get("db") or {}
    if db.get("configured") and not db.get("connected"):
        problems.append(f"database not connected: {db}")

    for p in problems:
        print(f"::error::{p}")
    print(f"disk_free_mb={free} storage={health.get('storage')} "
          f"db_connected={db.get('connected')}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2])))

"""Turn /api/ready into a pass/fail for the Health watch workflow.

Kept out of the workflow YAML so it can be read and changed without fighting
block-scalar indentation, and so the thresholds are reviewable in a diff.
Exits non-zero (failing the run, which notifies) on anything meaning a seller
is about to hit a wall.

It reads /api/ready, NOT /api/health. /api/health went back to liveness plus
the handful of capability flags the UI reads: it is anonymous and unrate
limited, so the 26 operator keys it used to publish -- the R2 bucket name, the
NAMES of unset environment variables, free disk, which Stripe mode the keys
were in, and the raw database and object-store exception text (which carries
the Neon host and role, and the R2 account id in its endpoint) -- moved behind
a token on /api/admin/diagnostics.

This script went on reading them there and got None for every one, so from
2026-08-31 the alarm failed on EVERY schedule with "disk_free_mb missing or
not a number: None" and "object storage not configured; missing=None" against
a production that was entirely healthy -- 493MB free, R2 fine, database
connected. An alarm that is always red is worse than no alarm: it cannot
report the real thing when it happens, and it teaches its operator to ignore
the notification.

/api/ready is the right source regardless. It exists to answer "can this
machine do the work right now", it already carried the disk number and the
storage and database checks, and it is public -- so this workflow stays free
of any credential, which is what lets it keep working across a token rotation.
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
        print(f"::error::/api/ready did not return JSON ({exc}) - "
              "the app is probably down or serving an edge error page")
        return None
    if not isinstance(body, dict):
        print(f"::error::/api/ready returned {type(body).__name__}, not an object")
        return None
    return body


def problems(ready: dict, min_free_mb: int) -> list[str]:
    found = []

    # The checks that gate the 503. Each is already a considered "this machine
    # cannot do the work", so name whichever is false: the workflow's own
    # readiness step only has the status code to report, which is the gap this
    # step exists to fill.
    checks = ready.get("checks")
    if not isinstance(checks, dict) or not checks:
        found.append(f"checks missing from /api/ready: {checks!r}")
    else:
        found += [f"readiness check {name!r} is failing"
                  for name, ok in sorted(checks.items()) if not ok]

    free = ready.get("disk_free_mb")
    if not isinstance(free, int):
        found.append(f"disk_free_mb missing or not a number: {free!r}")
    elif free < min_free_mb:
        found.append(
            f"only {free}MB free on the volume (want >= {min_free_mb}MB) - "
            "extend it or reclaim before a batch hits ENOSPC")

    # A swallowed R2 error means photos are silently not reaching the bucket.
    # The volume then cannot be reclaimed and publishes start failing.
    store = ready.get("object_storage")
    if not isinstance(store, dict):
        found.append(f"object_storage missing from /api/ready: {store!r}")
    elif store.get("degraded"):
        # The latch: credentials are fine, the bucket is not answering. Why is
        # deliberately not here -- the reason text names the account -- so the
        # alarm says where to read it.
        found.append(
            "object storage is degraded - photos are staying on the volume "
            "and it cannot be reclaimed; objstore_error on "
            "/api/admin/diagnostics says why")
    elif not store.get("configured"):
        # Only ever a real credential gap. Reporting the 600s latch as "not
        # configured" is what used to send an operator hunting for variables
        # that were all present, so `configured` stays true through it and the
        # branch above owns that case.
        found.append(
            "object storage is not configured - photos are not being "
            "offloaded to R2 at all")

    return found


def main(path: str, min_free_mb: int) -> int:
    ready = load(path)
    if ready is None:
        return 1
    found = problems(ready, min_free_mb)
    for p in found:
        print(f"::error::{p}")
    print(f"ready={ready.get('ready')} "
          f"checks={ready.get('checks')} "
          f"disk_free_mb={ready.get('disk_free_mb')} "
          f"object_storage={ready.get('object_storage')}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2])))

"""Turn the error feed into the short list of bugs worth opening a PR for.

Kept out of the workflow YAML for the reason check_health.py is: so the
thresholds can be read and changed in a diff instead of inside a block scalar.
`.github/scripts/` is not a package and never will be, so the test that covers
this loads it by path.

**Exit code 1 means "I could not look", never "I found bugs."** That
distinction is the whole design. health-watch.yml fails the run to alert,
because there a failing probe IS the news. Here a red run every morning that
production has a bug would be a notification nobody reads by the second week —
the same alarm fatigue check_health.py's docstring was written about, arriving
by a different route. Finding work is a normal, quiet, exit-0 day; the workflow
branches on the candidate COUNT, not on the status. Being unable to read the
feed is the genuine failure, and that is the only thing that goes red.

The bar for "worth a pull request" is deliberately narrow, because the cost of
the two mistakes is not symmetric. A missed bug is seen again tomorrow — the
feed is a running total, not a queue that drains. A wrong PR costs a human's
review, and enough of them cost the whole idea its credibility.
"""
from __future__ import annotations

import json
import os
import sys

# Seen this many times, or carrying a traceback. A single sentence logged once
# with no stack is not something anybody can act on; a real traceback is worth
# a look the first time it happens.
MIN_COUNT = 3

# Third-party unreachability. An httpx.ConnectError against eBay is an OUTAGE,
# and nothing in a pull request against this repository fixes it. The feed
# already grades these "low"; this is the belt to that pair of braces, and it
# is here rather than in the app so the list is reviewable without a deploy.
EXTERNAL = frozenset((
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "RemoteProtocolError", "ReadError", "TimeoutException",
    "SSLError", "ProxyError", "StorageUnavailable",
))

# How many fixes to propose in one run. Not a judgement about how many bugs
# exist — a bound on how much review one morning can create. The rest keep
# their place in the feed and come back tomorrow, highest count first.
MAX_CANDIDATES = 3


def load(path: str, what: str) -> dict | None:
    """The body, or None with an ::error:: naming what went wrong.

    A bare json.load would answer an edge error page — the app being down is
    exactly when this runs and finds nothing — with a decoder traceback
    instead of a sentence.
    """
    try:
        with open(path) as fh:
            body = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::{what} did not return JSON ({exc}) - the app is "
              "probably down, or the token was rejected")
        return None
    if not isinstance(body, dict):
        print(f"::error::{what} returned {type(body).__name__}, not an object")
        return None
    return body


def load_known(path: str) -> dict:
    """Fingerprints somebody has decided not to hear about again.

    A checked-in file rather than a database flag: silencing an alarm is a
    decision worth seeing in a diff, and it survives a branch being deleted.
    Missing is the normal state and means nothing is silenced.
    """
    try:
        with open(path) as fh:
            body = json.load(fh)
        return body if isinstance(body, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def module_file(module: str) -> str | None:
    """The repository path a recorded module name points at, if any.

    `backend.services.listing_sync` -> `backend/services/listing_sync.py`.
    Frontend rows record "browser" and have no such path; they are judged on
    the component stack instead, so they return None and skip the check.
    """
    if not module or module == "browser" or "." not in module:
        return None
    return module.replace(".", "/") + ".py"


def why_not(row: dict, known: dict, repo_root: str) -> str | None:
    """The reason this row is not worth a pull request, or None if it is."""
    fingerprint = row.get("fingerprint") or ""
    if not fingerprint:
        return "no fingerprint"
    if fingerprint in known:
        return f"silenced in known-errors.json ({known[fingerprint]})"
    if row.get("resolved_at"):
        return f"already has a fix ({row.get('fix_pr') or 'unnamed'})"
    if row.get("exc_type") in EXTERNAL:
        return f"{row.get('exc_type')} is a third party being unreachable"
    if row.get("severity") == "low":
        return "graded low - no traceback and nothing that reads as a failure"
    if not row.get("traceback") and (row.get("count") or 0) < MIN_COUNT:
        return f"seen {row.get('count')}x with no traceback (want {MIN_COUNT})"

    # An error recorded by an image that no longer matches the tree cannot be
    # fixed against the tree. Same reasoning as health-watch.yml's build-drift
    # step, one layer in: acting on a stale fact is worse than not acting.
    path = module_file(str(row.get("module") or ""))
    if path and not os.path.exists(os.path.join(repo_root, path)):
        return f"{path} is not in the tree - recorded by an older build"
    return None


def candidates(feed: dict, known: dict, repo_root: str) -> list[dict]:
    """The bugs worth proposing a fix for, most frequent first."""
    rows = feed.get("errors")
    if not isinstance(rows, list):
        return []
    keep = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        reason = why_not(row, known, repo_root)
        if reason is None:
            keep.append(row)
        else:
            print(f"  skipped {row.get('fingerprint', '?')}: {reason}")
    keep.sort(key=lambda r: (r.get("severity") != "high", -(r.get("count") or 0)))
    return keep[:MAX_CANDIDATES]


def work_item(row: dict) -> dict:
    """The subset handed to the agent. Explicitly a subset, not the whole row:
    everything here is text that arrived from production, some of it from a
    browser and therefore from whoever was using one."""
    return {
        "fingerprint": row.get("fingerprint"),
        "severity": row.get("severity"),
        "kind": row.get("kind"),
        "exc_type": row.get("exc_type"),
        "message": row.get("message"),
        "where": ".".join(x for x in (row.get("module"), row.get("func")) if x),
        "line": row.get("lineno"),
        "route": row.get("route"),
        "method": row.get("method"),
        "count": row.get("count"),
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "build": row.get("build"),
        "traceback": row.get("traceback"),
        "component_stack": (row.get("data") or {}).get("component_stack"),
    }


def emit_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as fh:
        fh.write(f"{name}={value}\n")


def main(feed_path: str, known_path: str, out_path: str,
         repo_root: str = ".") -> int:
    feed = load(feed_path, "/api/ops/error-feed")
    if feed is None:
        return 1

    sink = feed.get("sink") or {}
    if sink.get("dropped"):
        # Not a failure, but it must be said: the feed is incomplete, so a
        # quiet run today does not mean a quiet day.
        print(f"::warning::the error sink dropped {sink['dropped']} report(s) "
              "- this feed is incomplete")

    picked = candidates(feed, load_known(known_path), repo_root)
    with open(out_path, "w") as fh:
        json.dump({"candidates": [work_item(r) for r in picked]}, fh, indent=2)

    total = len(feed.get("errors") or [])
    emit_output("count", str(len(picked)))
    emit_output("path", out_path)
    print(f"{total} distinct error(s) in the window, {len(picked)} worth a fix")
    for row in picked:
        print(f"::notice::{row.get('fingerprint')} {row.get('exc_type')} "
              f"x{row.get('count')} - {row.get('message', '')[:120]}")
    # Zero either way. Finding work is an ordinary day; only being unable to
    # look is a failure. See the module docstring.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3],
                  sys.argv[4] if len(sys.argv) > 4 else "."))

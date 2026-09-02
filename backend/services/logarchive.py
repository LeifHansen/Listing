"""A daily copy of the error log in object storage.

The `error_events` table is pruned (ERROR_TTL_DAYS, 30 by default) so that a
fixed bug stops being listed forever, and Fly's own retained log window is
measured in hours. Between them, nothing in this system remembers what
production was doing two months ago. That is fine for triage and wrong for the
questions asked after an incident: was this happening before the deploy, is
this the same failure as the one in July, how long had it been building.

So once a day the distinct failures seen in the last day are written to R2 as
gzipped JSONL. Small — one line per DISTINCT failure, not per occurrence — and
already redacted, because everything in the row went through redact.scrub on
the way in.

It writes under its own `ops/` prefix, and that is load-bearing rather than
tidy. `sessions/` is walked by objstore.delete_prefix during the reclaim
sweep, and by the strict delete an account erasure runs. A log archive under
that prefix would be destroyed by housekeeping AND would place log data inside
the scope of somebody's right-to-erasure request — wrong in both directions.

Best-effort throughout. R2 being unreachable is not a reason to fail anything;
the table is still the live record and the next day's pass will run.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import json

from .. import objstore
from ..config import log

PREFIX = "ops/errors"
# One line per distinct failure, so a busy day is hundreds of lines rather
# than the millions a per-occurrence log would be. The cap is a backstop
# against a pathological day, not an expected limit.
MAX_ROWS = 5000


def key_for(day: _dt.date) -> str:
    """`ops/errors/YYYY/MM/DD.jsonl.gz` — sorted by date when listed."""
    return f"{PREFIX}/{day:%Y/%m/%d}.jsonl.gz"


def bundle(rows: list[dict]) -> bytes:
    """The rows as gzipped JSONL. One JSON object per line, so the archive can
    be read a line at a time without parsing the whole file."""
    body = "\n".join(json.dumps(row, separators=(",", ":"), default=str)
                     for row in rows[:MAX_ROWS])
    return gzip.compress(body.encode("utf-8"))


def archive_day(day: _dt.date | None = None) -> bool:
    """Write yesterday's distinct failures to R2. Returns whether it landed.

    NEVER raises: this runs from the error writer's daily tick, and a failure
    to archive must not take down the thread that is recording live failures.
    """
    if not objstore.enabled():
        return False
    from .. import db

    try:
        day = day or (_dt.datetime.now(_dt.timezone.utc).date()
                      - _dt.timedelta(days=1))
        rows = db.error_events_list(limit=MAX_ROWS, since_hours=48)
        if not rows:
            return False
        objstore.put_bytes(bundle(rows), key_for(day), "application/gzip")
        log.info("logarchive: wrote %d error(s) to %s", len(rows), key_for(day))
        return True
    except Exception as exc:  # noqa: BLE001 - archiving is never load-bearing
        # debug, not warning: this runs inside the error writer, and a warning
        # here would be captured, queued, and reported as a new failure every
        # single day that R2 is unreachable.
        log.debug("logarchive: could not write the daily bundle: %s", exc)
        return False

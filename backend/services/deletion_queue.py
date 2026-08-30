"""Finish the erasures that were promised and interrupted.

Two things in this app promise a deletion and then do the work afterwards:

  - a seller deleting their account. The rows go in one transaction; the
    photos — a local directory and an R2 prefix per listing — are erased in a
    pass that follows;
  - an eBay marketplace account-deletion notice. It is recorded, acknowledged
    to eBay, and then carried out.

In both cases the promise is made before the work is done, deliberately: the
alternative is holding a request open across thousands of object deletes, and
in eBay's case answering only after the erasure, which loses the notice
entirely if the answer is late.

What was missing is the other half. Neither had anything that resumed. A
deploy, restart, OOM or crash part-way left the remaining photos in the
bucket with the rows that named them already deleted — nothing would look for
them again — and left an acknowledged eBay notice pending forever, because
eBay stops resending once it has its 2xx.

This is that half. It reads what is still owed and does it, and it is safe to
run repeatedly: both purges are by prefix and idempotent, and a row is only
dropped once its work raised nothing.

It does NOT make the RUNNER durable. The pass is called at startup and from
the housekeeping loop, which are still process-local (that is P1-02). What is
durable is the WORK: the obligation survives the process, so the next one
finds it. That is the half that decides whether a seller's photos come back.
"""
from __future__ import annotations

from typing import Callable, Optional

from .. import db
from ..config import log
from . import ebay_deletion

PurgeMedia = Callable[[str], None]


def _run_media(purge_media: PurgeMedia, limit: int) -> int:
    done = 0
    for row in db.pending_media_purges(limit=limit):
        lid = row["listing_id"]
        try:
            purge_media(lid)
        except Exception as exc:  # noqa: BLE001 - one stuck object, not the pass
            # Keep going. One prefix R2 will not delete must not strand every
            # other seller's erasure behind it.
            db.note_media_purge_failure(lid, str(exc))
            log.warning("deletion: media purge still failing for %s (%d "
                        "attempt(s)): %s", lid, (row["attempts"] or 0) + 1, exc)
            continue
        db.finish_media_purge(lid)
        done += 1
    return done


def _run_notices(purge_media: Optional[PurgeMedia], limit: int) -> int:
    done = 0
    for notice in db.pending_deletion_notices(limit=limit):
        nid = notice.get("notification_id") or ""
        subject = notice.get("ebay_user_id") or ""
        if not subject:
            # Nothing to resolve it against, and it cannot become resolvable.
            # Recorded as failed so it stops being retried every pass and
            # starts showing up as a thing an operator has to look at.
            db.finish_deletion_notice(nid, "failed", "no eBay user id on the notice")
            continue
        try:
            result = ebay_deletion.purge(subject, purge_media=purge_media)
        except Exception as exc:  # noqa: BLE001
            db.finish_deletion_notice(nid, "failed", str(exc))
            log.warning("deletion: notice %s still failing: %s", nid, exc)
            continue
        db.finish_deletion_notice(nid, result.get("state", "failed"),
                                  result.get("error", ""))
        if result.get("state") in ("done", "no_match"):
            done += 1
    return done


def run_pending(purge_media: PurgeMedia, limit: int = 500) -> dict:
    """Do whatever erasure is still outstanding. Never raises.

    Returns {"media": n, "notices": n} — what this pass actually completed,
    not what it attempted, so a caller logging it is reporting finished work.
    """
    media = notices = 0
    try:
        media = _run_media(purge_media, limit)
    except Exception as exc:  # noqa: BLE001 - the notices half still runs
        log.warning("deletion: media pass failed: %s", exc)
    try:
        notices = _run_notices(purge_media, limit)
    except Exception as exc:  # noqa: BLE001
        log.warning("deletion: notice pass failed: %s", exc)
    if media or notices:
        log.info("deletion: completed %d media purge(s) and %d eBay notice(s) "
                 "left over from an earlier run", media, notices)
    return {"media": media, "notices": notices}


def backlog() -> dict:
    """What is still owed, for the operator diagnostics. Counts only —
    listing ids belong to people who asked to be forgotten.

    `None` for a count that could not be taken, and never a zero standing in
    for one. The operator is told to watch these numbers because one that
    does not come back down is a promise already made to somebody; a read
    that failed answering "nothing owed" is the worst possible time to be
    wrong, since it is exactly during an outage that they are looking.

    It does not raise, deliberately. Refusing the whole diagnostics page
    because one table is unreadable takes away the thing being used to
    diagnose the outage. Each count fails on its own.
    """
    def count(fn, what: str):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - unknown is the answer here
            log.warning("deletion: couldn't count the %s backlog: %s", what, exc)
            return None

    return {
        "media_purges": count(db.count_pending_media_purges, "media purge"),
        "deletion_notices": count(db.count_pending_deletion_notices,
                                  "deletion notice"),
    }

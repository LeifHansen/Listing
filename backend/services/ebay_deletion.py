"""Carry out an eBay marketplace account-deletion notice.

eBay requires an application that stores eBay data to process account
deletion/closure notifications. Acknowledging one is a promise that the
erasure will happen, so the shape here is:

    1. verify the signature      (services/ebay_notify.py)
    2. resolve the immutable eBay userId to our users
    3. record the notice durably (db.record_deletion_notice)
    4. answer eBay
    5. erase, and mark the notice done

Steps 3 and 4 are the load-bearing pair. eBay resends until it gets a 2xx and
stops afterwards, so a 200 sent before the notice is recorded is a promise
with nothing behind it: a crash a moment later leaves no trace that anyone
ever asked. Recording first means a crash leaves a 'pending' row, which is
recoverable.

What gets erased is everything keyed to the matched user: their listings and
the photos behind them, their marketplace connections and stored tokens, and
their account row. db.delete_user already does that in one transaction and
hands back the listing ids so their media can be purged after.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Optional

from .. import db
from ..config import log


def payload_digest(raw_body: bytes) -> str:
    """A digest of the notice, for spotting a changed redelivery.

    A digest and not the body: the payload is personal data about someone who
    has just asked to be forgotten, so retaining it to prove we deleted it
    would be its own violation.
    """
    return hashlib.sha256(raw_body or b"").hexdigest()


def subject_of(payload: dict) -> str:
    """The immutable eBay user id this notice is about, or "".

    Only `userId` is used. `username` is mutable — matching on it would miss a
    seller who renamed and could match the WRONG seller if a handle were
    reused — and `eiasToken` is legacy.
    """
    data = ((payload or {}).get("notification") or {}).get("data") or {}
    return str(data.get("userId") or "").strip()


def notification_id_of(payload: dict) -> str:
    return str(((payload or {}).get("notification") or {})
               .get("notificationId") or "").strip()


def purge(ebay_user_id: str,
          purge_media: Optional[Callable[[str], None]] = None) -> dict:
    """Erase every user connected to this eBay account.

    Returns {"users": n, "listings": n, "state": ...}. `state` is what the
    notice row should be set to: "done", "no_match" (nobody here was
    connected to that account — a legitimate outcome, not a failure), or
    "failed".
    """
    matched = db.find_users_by_ebay_user_id(ebay_user_id)
    if matched is db.UNAVAILABLE:
        # The question could not be answered, so nothing may be claimed. The
        # notice stays pending and is retried rather than being written off
        # as "nobody matched".
        return {"users": 0, "listings": 0, "state": "failed",
                "error": "could not look up the account"}
    if not matched:
        return {"users": 0, "listings": 0, "state": "no_match"}

    listings = 0
    failures = []
    for uid in matched:
        listing_ids = db.delete_user(uid)
        if listing_ids is None:
            failures.append(uid)
            continue
        listings += len(listing_ids)
        if purge_media:
            for lid in listing_ids:
                try:
                    purge_media(lid)
                except Exception as exc:  # noqa: BLE001 - rows are already gone
                    # The durable record is deleted; a stuck object is a
                    # cleanup problem, not a reason to redo the erasure.
                    log.warning("ebay deletion: media purge failed for %s: %s",
                                lid, exc)
    if failures:
        return {"users": len(matched) - len(failures), "listings": listings,
                "state": "failed",
                "error": f"{len(failures)} account(s) did not delete"}
    return {"users": len(matched), "listings": listings, "state": "done"}

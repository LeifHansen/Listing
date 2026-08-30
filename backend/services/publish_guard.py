"""One listing, one live listing — the guard against publishing twice.

Creating an eBay listing is the only step in the publish pipeline that isn't
naturally idempotent. Everything else keys off something stable (the Inventory
API keys off the session's SKU, a revise keys off the item id), but
AddFixedPriceItem has no natural key: call it twice and the seller has two live
listings for one item. That is exactly what sellers were seeing — a pair of
identical cards, one created here and one "imported", because the store sync
faithfully pulled back the second listing the app had created without knowing.

Two independent things had to line up for that:

  * Publishing was decided from the listing the BROWSER sent. A payload from
    before the first publish carries no ebay_listing_id and no source, so it
    reads as "never listed" no matter what the stored record says.
  * Nothing serialized publishes of the same listing. A publish takes tens of
    seconds (eBay ingests every photo), which is long enough for a seller to
    reload and press the button again — and a reload resets the browser's own
    double-submit guard. Both requests then read the pre-publish record and
    both create.

This module supplies the two pieces that close it: `session_lock` so publishes
of one listing run one at a time, and `idempotency_key` so eBay itself refuses
the second create even when the two attempts don't share a process — a second
Fly machine, a retried request, or a record whose write was lost.
"""
from __future__ import annotations

import threading
from typing import Optional

from .. import storage

# One lock per listing, created on demand. Publishes of DIFFERENT listings must
# still run concurrently (bulk publish leans on that), so this can't be a
# single global lock.
#
# Entries are never evicted, which is only sound because the KEY is bounded.
# It is `storage.safe_session_name`, not the raw id off the request: the
# argument for never evicting ("a lock is ~50 bytes, one per listing the
# process has published") assumed a listing-shaped id, while the raw string
# arrives from an unauthenticated request body and can be any length. Keyed on
# that, this dict was an unauthenticated memory leak — and keying on the
# canonical name is also more correct, since it is the identity every other
# session-keyed thing already uses.
#
# Evicting them safely would mean proving nobody is about to take the lock,
# which is the same race this exists to prevent.
_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def session_lock(session_id: str) -> threading.Lock:
    """The publish lock for one listing. Hold it around the read-decide-create
    sequence so two publishes of the same listing can't both decide to create.

    The lock is process-local, so it can't be the only defence — a second
    machine has its own. It handles the common case (one server, a seller
    pressing publish twice) cheaply, and `idempotency_key` covers the rest.
    """
    key = storage.safe_session_name(session_id)
    with _registry_lock:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def idempotency_key(session_id: str, replacing_item_id: str = "") -> str:
    """The key that makes creating THIS listing safe to repeat.

    Deterministic, so a retry of the same publish computes the same key and
    eBay can recognise it. It travels as UUID and, on a fixed-price create, as
    Item.SKU with InventoryTrackingMethod=SKU; see ebay_trading.create_listing.

    The lock below is process-local, so it does not serialize two machines.
    What actually prevents a duplicate across processes is eBay's own UUID
    check, which is server-side and is the guard being relied on; the lock
    only saves a wasted round trip for the common double-tap. (An earlier
    version claimed a second server-side guard via InventoryTrackingNumber,
    which eBay does not implement.)

    `replacing_item_id` distinguishes a relist from the publish that came
    before it. A relist is a genuinely new listing — the seller ended the old
    one and asked for a fresh item id — so it must NOT collide with the
    original key. Deriving it from the item being replaced keeps a *retried*
    relist idempotent while letting an intentional one through.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        return ""
    # The `qf-` prefix is the app's old name and stays that way: this string
    # is stamped on live eBay listings as Item.SKU, and eBay is holding the
    # ones already published. Renaming it matches none of them, so every such
    # listing imports as a stranger's on the next sync -- a duplicate card for
    # an item the seller already has, which is the exact failure this module
    # exists to prevent. Pinned by tests/test_the_rename_stops_at_stored_data.py.
    key = f"qf-{session_id}"
    if replacing_item_id:
        key += f"-r{str(replacing_item_id).strip()}"
    return key[:50]  # eBay's SKU length ceiling


def stored_item_id(record: Optional[dict]) -> str:
    """The eBay item id the SERVER has for this listing, ignoring the browser.

    The decision to create rather than revise has to be made from this, not
    from the submitted payload: a stale tab, a resumed draft, or a client that
    simply doesn't round-trip the field all look like "never published"
    otherwise, and the cost of believing them is a duplicate live listing.
    """
    listing = ((record or {}).get("listing") or {})
    return str(listing.get("ebay_listing_id") or "").strip()

"""In-app notifications — today that means "your item sold, go ship it".

Sold events are detected wherever the app learns about a sale (the store
import, the imported-listing status sweep, the app-listing offer check), so
the recording is centralized here. Every path funnels through notify_sold(),
which builds a stable dedupe key from the listing record id — the DB's unique
constraint guarantees one notification per sale no matter how many sync paths
notice it, or how many times they run.

Everything is best-effort: a notification failure must never break the sync
that triggered it.
"""
from __future__ import annotations

from typing import Optional

from .. import db
from ..config import log


def notify_sold(user_id: Optional[str], record_id: str, listing: dict,
                sold_quantity: int = 0) -> None:
    """Record a "sold" notification for one listing. Never raises.

    The dedupe key includes the sold quantity so a later sale of another unit
    of a multi-quantity listing produces a fresh notification, while re-syncs
    that report the same quantity stay silent.
    """
    if not user_id or not record_id:
        return
    try:
        title = (listing.get("title") or "").strip() or "An item"
        # What it SOLD for when eBay reported the transaction (an accepted
        # offer settles below the asking price), else the asking price.
        price = listing.get("sold_price")
        if price is None:
            price = listing.get("price")
        try:
            price_part = f" for ${float(price):,.2f}" if price else ""
        except (TypeError, ValueError):
            price_part = ""
        qty = max(0, int(sold_quantity or 0))
        db.add_notification(
            user_id,
            kind="sold",
            title=f"Sold: {title}"[:255],
            body=(f"“{title}” sold{price_part} on eBay. "
                  "Print a shipping label and get it on its way."),
            listing_id=record_id,
            data={"price": price, "sold_quantity": qty,
                  "ebay_listing_id": str(listing.get("ebay_listing_id") or "")},
            # qty rides the key so unit 2 of a multi-quantity listing notifies
            # again; a plain re-sync reporting the same qty dedupes away.
            dedupe_key=f"sold:{record_id}:{qty or 1}",
        )
    except Exception as exc:  # noqa: BLE001 - notifications never break a sync
        log.info("notifications: couldn't record sale for %s: %s", record_id, exc)

"""Bi-directional sync between the seller's eBay store and this app.

eBay -> app (import): every ACTIVE listing on the account is pulled in via the
Trading API (see ebay_trading) and stored as a normal listing record, so the
app shows the seller's WHOLE store — not just what it created. Imported records
carry source="ebay" and eBay's item id.

app -> eBay (push): edits to an imported listing go back out through the
Trading API's ReviseItem, because those listings aren't Inventory-API managed.
Listings this app created keep using the Inventory API path in services/ebay.

The record id for an imported listing is "ebay-<itemId>", which is stable
across syncs (so re-importing updates in place instead of duplicating) and can
never collide with a session id.

Every function is best-effort about individual listings: one bad item logs and
is skipped rather than failing the whole sync.
"""
from __future__ import annotations

from typing import Optional

from .. import db
from ..config import log
from ..models import Listing
from . import ebay_trading

# Listing fields the seller owns in THIS app. On a re-sync we refresh the
# live/market facts from eBay but keep everything else the record already has,
# so a local edit isn't silently reverted by a background sync.
_LIVE_FIELDS = ("price", "quantity", "watch_count", "sold_quantity",
                "view_url", "image_urls")


def record_id(item_id: str) -> str:
    return f"ebay-{item_id}"


def is_imported(listing: Listing | dict) -> bool:
    source = (listing.get("source") if isinstance(listing, dict)
              else getattr(listing, "source", ""))
    return (source or "").lower() == "ebay"


def _merge(existing: Optional[dict], fresh: dict) -> dict:
    """Fresh eBay data merged over an existing record.

    A first import takes everything. A re-sync only refreshes the fields eBay
    owns (price, quantity, counters, photos) plus anything still blank locally
    — otherwise a sync would wipe the seller's in-app edits.
    """
    if not existing:
        return fresh
    merged = dict(existing)
    for key in _LIVE_FIELDS:
        if key in fresh:
            merged[key] = fresh[key]
    for key, value in fresh.items():
        if key in _LIVE_FIELDS:
            continue
        current = merged.get(key)
        if current in (None, "", [], 0) and value not in (None, "", []):
            merged[key] = value
    merged["source"] = "ebay"
    merged["ebay_listing_id"] = fresh.get("ebay_listing_id") or merged.get("ebay_listing_id", "")
    return merged


def import_active(token: str, user_id: str, limit: int = 300,
                  progress=None) -> dict:
    """Import every active eBay listing for this user.

    Returns {"found", "imported", "updated", "failed"}. `progress(done, total)`
    is called after each listing so a long first sync can show a live count.
    """
    ids = ebay_trading.active_listing_ids(token)[:limit]
    known = {r["id"]: r for r in db.list_listings(limit=1000, user_id=user_id)}
    imported = updated = failed = 0
    for i, item_id in enumerate(ids, start=1):
        rid = record_id(item_id)
        try:
            fresh = ebay_trading.get_listing(token, item_id)
        except Exception as exc:  # noqa: BLE001 - skip one bad listing
            log.info("sync: couldn't import eBay item %s: %s", item_id, exc)
            failed += 1
            continue
        prior = known.get(rid)
        data = _merge(prior.get("listing") if prior else None, fresh)
        # Validate through the model so a malformed field can't poison the DB.
        try:
            data = Listing(**{k: v for k, v in data.items()
                              if k in Listing.model_fields}).model_dump()
        except Exception as exc:  # noqa: BLE001
            log.info("sync: eBay item %s didn't validate: %s", item_id, exc)
            failed += 1
            continue
        db.upsert_listing(rid, data, status="published", user_id=user_id)
        if prior:
            updated += 1
        else:
            imported += 1
        if progress:
            try:
                progress(i, len(ids))
            except Exception:  # noqa: BLE001 - display only
                pass
    log.info("sync: user=%s found=%d imported=%d updated=%d failed=%d",
             user_id, len(ids), imported, updated, failed)
    return {"found": len(ids), "imported": imported, "updated": updated,
            "failed": failed}


def refresh_statuses(token: str, user_id: str, records: list[dict]) -> int:
    """Re-check imported listings that are still marked live: sold/ended items
    get their status corrected, and watch/sold counters refreshed. Returns how
    many records changed. A None status (API blip) changes nothing."""
    changed = 0
    for rec in records:
        data = rec.get("listing") or {}
        item_id = str(data.get("ebay_listing_id") or "")
        if not item_id:
            continue
        status, sold, watch = ebay_trading.listing_status(token, item_id)
        if status is None:
            continue
        updates = dict(data)
        updates["sold_quantity"] = sold
        updates["watch_count"] = watch
        if status != rec.get("status") or updates != data:
            db.upsert_listing(rec["id"], updates, status=status, user_id=user_id)
            changed += 1
    return changed


def push_edit(token: str, listing: Listing) -> dict:
    """Send an edited imported listing back to eBay. Raises TradingError with
    eBay's own reason on failure."""
    return ebay_trading.revise_listing(
        token, listing.ebay_listing_id, listing,
        image_urls=listing.image_urls or None)


def end(token: str, listing: Listing) -> dict:
    """End an imported listing on eBay."""
    return ebay_trading.end_listing(token, listing.ebay_listing_id)

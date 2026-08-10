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

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from .. import db, ebay_auth, storage
from ..config import log
from ..models import Listing
from . import ebay_trading, taxonomy

# Listing fields the seller owns in THIS app. On a re-sync we refresh the
# live/market facts from eBay but keep everything else the record already has,
# so a local edit isn't silently reverted by a background sync.
_LIVE_FIELDS = ("price", "quantity", "watch_count", "sold_quantity",
                "view_url", "image_urls")
# Detail fetches run a few at a time: each listing is its own GetItem round
# trip, so a 300-item store takes minutes when they run one after another —
# long enough for the browser to give up on the request. Small pool, because
# eBay rate-limits per-account calls.
_FETCH_WORKERS = int(os.getenv("EBAY_SYNC_WORKERS", "6") or "6")


def record_id(item_id: str) -> str:
    return f"ebay-{item_id}"


def is_imported(listing: Listing | dict) -> bool:
    source = (listing.get("source") if isinstance(listing, dict)
              else getattr(listing, "source", ""))
    return (source or "").lower() == "ebay"


def _is_blank(value) -> bool:
    """True for a field the seller has never filled in.

    Deliberately NOT a plain falsiness test: `0`, `0.0` and `False` compare
    equal to each other in Python, so a blanket "falsy means empty" check would
    treat a real zero (a free item, an unchecked flag) as missing and let a
    sync overwrite it."""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    return len(value) == 0 if hasattr(value, "__len__") else False


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
        if _is_blank(merged.get(key)) and not _is_blank(value):
            merged[key] = value
    merged["source"] = "ebay"
    merged["ebay_listing_id"] = fresh.get("ebay_listing_id") or merged.get("ebay_listing_id", "")
    return merged


# How many ended/sold listings to mirror alongside the active ones. eBay only
# retains ~90 days of these, so a modest cap covers the real backlog.
_INACTIVE_LIMIT = int(os.getenv("EBAY_SYNC_INACTIVE_LIMIT", "100") or "100")

# How many ACTIVE listings to mirror. This was 300, which silently truncated
# any store bigger than that — a 616-listing account simply never saw half its
# inventory, and it read as "the sync is missing auctions". The ceiling that
# matters is eBay's own paging (_MAX_PAGES * _PAGE_SIZE).
_ACTIVE_LIMIT = int(os.getenv("EBAY_SYNC_ACTIVE_LIMIT", "2500") or "2500")


def _started_at(data: dict) -> Optional[datetime]:
    """An imported listing's eBay start time, as an aware datetime.

    This is what the row's updated_at becomes, so "most recent first" means
    most recently listed rather than most recently touched by a sync."""
    raw = str(data.get("ebay_start_time") or "").strip()
    if not raw:
        return None
    try:  # eBay sends "2026-07-30T18:04:11.000Z"
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def import_active(token: str, user_id: str, limit: int = _ACTIVE_LIMIT) -> dict:
    """Mirror the seller's eBay store into the app: every ACTIVE listing (up
    to `limit`), plus recently ENDED (unsold → status 'ended', the Inactive
    tab) and SOLD listings (status 'sold'), each capped at
    EBAY_SYNC_INACTIVE_LIMIT. Returns {"found", "imported", "updated",
    "failed"}."""
    jobs: list[tuple[str, str]] = []  # (item_id, status) — first entry wins
    seen: set[str] = set()

    def _add(item_ids: list[str], status: str) -> None:
        for i in item_ids:
            if i not in seen:
                seen.add(i)
                jobs.append((i, status))

    _add(ebay_trading.active_listing_ids(token, limit=limit), "published")
    # Inactive/sold mirrors are additive — a failure there must not sink the
    # main active-store sync.
    for fetch, status in ((ebay_trading.sold_listing_ids, "sold"),
                          (ebay_trading.unsold_listing_ids, "ended")):
        try:
            _add(fetch(token, limit=_INACTIVE_LIMIT), status)
        except Exception as exc:  # noqa: BLE001
            log.info("sync: couldn't list %s items: %s", status, exc)

    # Has to cover everything we're about to write, or listings past the cut
    # look new every sync and get re-imported instead of updated.
    known = {r["id"]: r
             for r in db.list_listings(limit=max(1000, len(jobs) * 2),
                                       user_id=user_id)}
    imported = updated = failed = 0

    def _fetch(job: tuple[str, str]):
        """(item_id, status, detail) — None detail when that listing failed."""
        item_id, status = job
        try:
            return item_id, status, ebay_trading.get_listing(token, item_id)
        except Exception as exc:  # noqa: BLE001 - skip one bad listing
            log.info("sync: couldn't import eBay item %s: %s", item_id, exc)
            return item_id, status, None

    # Fetch in parallel, but write to the DB from this thread only, in eBay's
    # original order — so the import stays deterministic and needs no locking.
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, max(1, len(jobs)))) as pool:
        fetched = list(pool.map(_fetch, jobs)) if jobs else []

    for item_id, status, fresh in fetched:
        if fresh is None:
            failed += 1
            continue
        rid = record_id(item_id)
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
        db.upsert_listing(rid, data, status=status, user_id=user_id,
                          when=_started_at(data))
        if prior:
            updated += 1
        else:
            imported += 1
    log.info("sync: user=%s found=%d imported=%d updated=%d failed=%d",
             user_id, len(jobs), imported, updated, failed)
    return {"found": len(jobs), "imported": imported, "updated": updated,
            "failed": failed}


def refresh_statuses(token: str, user_id: str, records: list[dict]) -> int:
    """Re-check imported listings that are still marked live: sold/ended items
    get their status corrected, and watch/sold counters refreshed. Returns how
    many records changed. A None status (API blip) changes nothing.

    Status calls run in parallel (each is its own eBay round-trip; serially
    a 60-listing sweep pinned a request thread for a minute); DB writes stay
    on this thread, in order."""
    def _probe(rec):
        item_id = str((rec.get("listing") or {}).get("ebay_listing_id") or "")
        if not item_id:
            return rec, None
        try:
            return rec, ebay_trading.listing_status(token, item_id)
        except Exception as exc:  # noqa: BLE001 - one blip skips one record
            log.info("sync: status check failed for %s: %s", rec.get("id"), exc)
            return rec, None

    probed = []
    if records:
        with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(records))) as pool:
            probed = list(pool.map(_probe, records))
    changed = 0
    for rec, result in probed:
        if result is None:
            continue
        status, sold, watch = result
        if status is None:
            continue
        data = rec.get("listing") or {}
        updates = dict(data)
        updates["sold_quantity"] = sold
        updates["watch_count"] = watch
        if status != rec.get("status") or updates != data:
            db.upsert_listing(rec["id"], updates, status=status, user_id=user_id)
            if status == "sold":
                # Archived — reclaim the volume space its working copies held,
                # matching what the app-listing sync path already does.
                storage.purge_session(rec["id"])
            changed += 1
    return changed


def create_on_ebay(token: str, listing: Listing, image_urls: list[str],
                   creds: Optional[dict] = None) -> dict:
    """Publish a NEW listing through the Trading API and mark it as one.

    Listings published via the Sell Inventory API are "inventory-based": eBay
    locks them out of Seller Hub's own editors ("Inventory-based listing
    management is not currently supported by this tool"). Publishing through
    Trading instead gives the seller an ordinary listing they can edit
    anywhere — here, Seller Hub, or the eBay app — and edits from this app
    keep flowing back through revise_listing.
    """
    c = creds or {}
    # Make every specific legal for its aspect before it goes near eBay —
    # canonical names, plain numbers where numbers are demanded, fixed choices
    # matched to eBay's wording. One chatty value ("Fabric Weight: Midweight")
    # otherwise rejects the whole listing.
    taxonomy.sanitize_specifics(listing)
    # eBay rejects a Trading listing that doesn't say where it ships from. Use
    # the saved ZIP, and when there isn't one, read it off the seller's eBay
    # location and remember it so the next publish doesn't pay for the lookup.
    postal = (c.get("ship_from_postal") or "").strip()
    if not postal:
        postal = ebay_auth.ship_from_postal(
            token, c.get("merchant_location_key") or "")
        if postal and c.get("_uid"):
            try:
                db.save_ebay_account(c["_uid"], ship_from_postal=postal)
            except Exception as exc:  # noqa: BLE001 - caching is optional
                log.info("sync: couldn't save the resolved ship-from ZIP: %s", exc)
    res = ebay_trading.create_listing(
        token, listing, image_urls,
        # A per-listing shipping choice (the editor's / bulk card's Shipping
        # service dropdown) beats the account default; payment/returns stay
        # account-level.
        policies={"fulfillment_policy_id": (listing.fulfillment_policy_id
                                            or c.get("fulfillment_policy_id")),
                  "payment_policy_id": c.get("payment_policy_id"),
                  "return_policy_id": c.get("return_policy_id")},
        postal_code=postal)
    # source="ebay" is what routes later edits down the Trading path, exactly
    # like a listing imported from the seller's store.
    listing.source = "ebay"
    listing.ebay_listing_id = res["listing_id"]
    listing.view_url = res.get("view_url", "")
    return res


def push_edit(token: str, listing: Listing,
              image_urls: Optional[list[str]] = None) -> dict:
    """Send an edited imported listing back to eBay. Raises TradingError with
    eBay's own reason on failure.

    `image_urls` overrides which photo URLs eBay gets: the caller passes our
    own /media URLs when the local working copies changed (eBay ingests fresh
    EPS derivatives), and the existing ebayimg URLs when nothing changed (no
    re-upload churn). Default: the listing's current eBay-hosted URLs."""
    taxonomy.sanitize_specifics(listing)  # same guard as create_on_ebay
    return ebay_trading.revise_listing(
        token, listing.ebay_listing_id, listing,
        image_urls=image_urls or listing.image_urls or None)


def end(token: str, listing: Listing) -> dict:
    """End an imported listing on eBay."""
    return ebay_trading.end_listing(token, listing.ebay_listing_id)

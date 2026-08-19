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
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from .. import db, ebay_auth, storage
from ..config import log
from ..models import Listing
from . import ebay_trading, notifications, taxonomy
from .ebay_trading import AlreadyListedError, TradingError

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


def _merge(existing: Optional[dict], fresh: dict, *, own_source: bool = False) -> dict:
    """Fresh eBay data merged over an existing record.

    A first import takes everything. A re-sync only refreshes the fields eBay
    owns (price, quantity, counters, photos) plus anything still blank locally
    — otherwise a sync would wipe the seller's in-app edits.

    `own_source` keeps the record's own source, for a listing this app
    published rather than imported. Those published through the Inventory API
    carry no source, and stamping "ebay" on them would route later edits to
    Trading's ReviseItem — which eBay refuses for inventory-based listings.
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
        # An app record's blank source is meaningful (Inventory-API path), so
        # it's the seller's, not a gap for eBay's "ebay" to fill.
        if own_source and key == "source":
            continue
        if _is_blank(merged.get(key)) and not _is_blank(value):
            merged[key] = value
    if not own_source:
        merged["source"] = "ebay"
    merged["ebay_listing_id"] = fresh.get("ebay_listing_id") or merged.get("ebay_listing_id", "")
    return merged


# "https://www.ebay.com/itm/1234567890" — the item id a publish saved on the
# record even in the rare case the id field itself didn't make it.
_ITEM_URL_RE = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,})")


def _item_id_of(listing: dict) -> str:
    """The eBay item id a record already carries, if any."""
    item = str(listing.get("ebay_listing_id") or "").strip()
    if item:
        return item
    found = _ITEM_URL_RE.search(str(listing.get("view_url") or ""))
    return found.group(1) if found else ""


def _index_by_item(records) -> dict[str, dict]:
    """eBay item id -> the record that already represents it.

    An item can be in the app under two different ids: the "ebay-<item>"
    mirror a sync writes, and the session id of a listing this app published
    (publishing stamps ebay_listing_id onto that record). The app-created one
    wins — it owns the photos on disk and everything the AI wrote — so a sync
    updates it in place instead of importing the same item a second time.
    """
    out: dict[str, dict] = {}
    for rec in records:
        item = _item_id_of(rec.get("listing") or {})
        if not item:
            continue
        current = out.get(item)
        if current is None or (_is_mirror(current) and not _is_mirror(rec)):
            out[item] = rec
    return out


def _is_mirror(record: dict) -> bool:
    """True for a record this sync created (id "ebay-<item>")."""
    return str(record.get("id") or "").startswith("ebay-")


def _drop_stale_mirrors(known: dict, owned: dict, user_id: str) -> int:
    """Delete mirror rows for items the app already has its own record of.

    Every sync before the id match above left one of these behind, so the
    cleanup covers the whole store rather than only the items this run
    happened to fetch — an ended duplicate never comes back in the active
    list, and would otherwise sit in Inactive forever. Returns how many went.
    """
    dropped = 0
    for rid, rec in list(known.items()):
        if not _is_mirror(rec):
            continue
        item = _item_id_of(rec.get("listing") or {}) or rid[len("ebay-"):]
        keeper = owned.get(item)
        if not keeper or keeper["id"] == rid:
            continue
        if db.delete_listing(rid, user_id=user_id):
            known.pop(rid, None)
            dropped += 1
            log.info("sync: dropped duplicate %s — item %s already lives on %s",
                     rid, item, keeper["id"])
    return dropped


# How many ended/sold listings to mirror alongside the active ones. eBay only
# retains ~90 days of these, so a modest cap covers the real backlog.
_INACTIVE_LIMIT = int(os.getenv("EBAY_SYNC_INACTIVE_LIMIT", "100") or "100")

# How many ACTIVE listings to mirror. This was 300, which silently truncated
# any store bigger than that — a 616-listing account simply never saw half its
# inventory, and it read as "the sync is missing auctions". The ceiling that
# matters is eBay's own paging (_MAX_PAGES * _PAGE_SIZE).
ACTIVE_LIMIT = int(os.getenv("EBAY_SYNC_ACTIVE_LIMIT", "2500") or "2500")

# How many existing records a sync reads before deciding what's new. This is
# NOT a store-size limit — it's the dedupe's field of view, and it has to be
# wider than the store: an active listing, an ended mirror, and a sold record
# can all exist for one item at once, so the row count runs well ahead of the
# active-listing count. Sized to stay ahead of ACTIVE_LIMIT + the inactive
# mirrors + whatever duplicates are still waiting to be cleaned up.
_KNOWN_LIMIT = int(os.getenv("EBAY_SYNC_KNOWN_LIMIT", "10000") or "10000")


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


def import_active(token: str, user_id: str, limit: int = ACTIVE_LIMIT) -> dict:
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

    # Has to cover EVERY record the seller has, not a guess scaled off this
    # run's job count. A record the read misses is a record the dedupe below
    # can't match, and the visible result is the duplicate pair it exists to
    # remove — so the ceiling is deliberately far above any real store rather
    # than a multiple of what eBay happened to return.
    known = {r["id"]: r for r in db.list_listings(limit=_KNOWN_LIMIT,
                                                  user_id=user_id)}
    if len(known) >= _KNOWN_LIMIT:
        # Never silently: past this the dedupe is working from a partial view.
        log.warning("sync: user=%s has at least %d records — the dedupe read is "
                    "capped, duplicates may survive this run",
                    user_id, _KNOWN_LIMIT)
    # Match on the eBay item id, not just on the mirror id: a listing this app
    # published is already here under its session id, and keying the sync on
    # "ebay-<item>" alone imported it again as a separate card on every sync —
    # the duplicate pairs (one Thryft, one eBay) sellers were seeing.
    owned = _index_by_item(known.values())
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
        mirror_id = record_id(item_id)
        prior = owned.get(item_id) or known.get(mirror_id)
        rid = prior["id"] if prior else mirror_id
        data = _merge(prior.get("listing") if prior else None, fresh,
                      own_source=bool(prior) and not _is_mirror(prior))
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
        # A listing we already knew flipping to sold IS the sale event. A
        # first-time import of an old sold listing stays silent — backfilling
        # a store must not fire a notification per historical sale.
        if prior and status == "sold" and prior.get("status") != "sold":
            notifications.notify_sold(user_id, rid, data,
                                      sold_quantity=data.get("sold_quantity") or 0)
        if prior:
            updated += 1
        else:
            imported += 1
    deduped = _drop_stale_mirrors(known, owned, user_id)
    log.info("sync: user=%s found=%d imported=%d updated=%d deduped=%d failed=%d",
             user_id, len(jobs), imported, updated, deduped, failed)
    return {"found": len(jobs), "imported": imported, "updated": updated,
            "deduped": deduped, "failed": failed}


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
                if rec.get("status") != "sold":
                    notifications.notify_sold(user_id, rec["id"], updates,
                                              sold_quantity=sold)
                # Archived — reclaim the volume space its working copies held,
                # matching what the app-listing sync path already does.
                storage.purge_session(rec["id"])
            changed += 1
    return changed


def reconcile_recent(token: str, user_id: str,
                     records: list[dict]) -> tuple[int, set[str]]:
    """Correct still-marked-live records whose items eBay says recently
    finished. Returns (records changed, record ids this pass covered).

    The per-record sweeps above are capped (each check is its own eBay round
    trip), so on a big store a listing that ended ON eBay could stay under
    Active for many syncs before its turn came up. This pass stays cheap at
    any store size: one paged GetMyeBaySelling call per list names every item
    that sold or ended in eBay's ~90-day window, and only the records matching
    those ids get the per-item probe — which is what rules out the false
    positive (a multi-quantity listing appears in the sold list while still
    live) and files each record as sold vs ended with fresh counters.
    A list that can't be fetched contributes nothing rather than failing."""
    def _ids(fetch, what: str) -> set[str]:
        try:
            return set(fetch(token, limit=_INACTIVE_LIMIT))
        except Exception as exc:  # noqa: BLE001 - reconcile is best-effort
            log.info("sync: couldn't list %s items: %s", what, exc)
            return set()

    finished = (_ids(ebay_trading.sold_listing_ids, "sold")
                | _ids(ebay_trading.unsold_listing_ids, "ended"))
    candidates = [r for r in records
                  if _item_id_of(r.get("listing") or {}) in finished]
    if not candidates:
        return 0, set()
    changed = refresh_statuses(token, user_id, candidates)
    return changed, {r["id"] for r in candidates}


def create_on_ebay(token: str, listing: Listing, image_urls: list[str],
                   creds: Optional[dict] = None,
                   idempotency_key: str = "") -> dict:
    """Publish a NEW listing through the Trading API and mark it as one.

    Listings published via the Sell Inventory API are "inventory-based": eBay
    locks them out of Seller Hub's own editors ("Inventory-based listing
    management is not currently supported by this tool"). Publishing through
    Trading instead gives the seller an ordinary listing they can edit
    anywhere — here, Seller Hub, or the eBay app — and edits from this app
    keep flowing back through revise_listing.

    `idempotency_key` (see services/publish_guard) makes the create safe to
    repeat: eBay rejects a second attempt under the same key rather than
    minting a duplicate listing, and this resolves that rejection back to the
    listing that already exists so the caller can adopt it.
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
    try:
        res = ebay_trading.create_listing(
            token, listing, image_urls,
            # A per-listing shipping choice (the editor's / bulk card's Shipping
            # service dropdown) beats the account default; payment/returns stay
            # account-level.
            policies={"fulfillment_policy_id": (listing.fulfillment_policy_id
                                                or c.get("fulfillment_policy_id")),
                      "payment_policy_id": c.get("payment_policy_id"),
                      "return_policy_id": c.get("return_policy_id")},
            postal_code=postal, idempotency_key=idempotency_key)
    except AlreadyListedError as exc:
        # This publish already produced a listing — a retry, or a second
        # request that raced this one. Adopt what's there instead of creating a
        # twin: eBay names the item sometimes, and the tracking number finds it
        # the rest of the time.
        item_id = exc.item_id or ebay_trading.item_id_for_tracking_number(
            token, idempotency_key)
        if not item_id:
            # Nothing to adopt and nothing created. Re-raising as an ordinary
            # failure is the honest outcome — but never retry the create here,
            # which is how a duplicate would slip through after all.
            raise TradingError(
                "eBay says this listing was already submitted, but wouldn't say "
                "which listing it became. Check your eBay listings before "
                "publishing again — publishing now could create a duplicate."
            ) from exc
        log.info("trading publish: adopted already-created item %s (key=%s)",
                 item_id, idempotency_key)
        res = {"published": True, "listing_id": item_id, "already_listed": True,
               "view_url": f"https://www.ebay.com/itm/{item_id}"}
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
    """End an imported listing on eBay.

    EndItem refuses a listing that already finished — which is exactly the
    state a seller hits when the listing ended (or sold) on eBay first and
    they click End here before a sync catches up. Failing then would strand
    the record under Active with no way to move it. So on a refusal, ask eBay
    what actually became of the listing: a definitive "it already finished"
    comes back as not_live (with how it finished, so the caller can file it
    under Inactive or Sold); anything less definitive re-raises the refusal.
    """
    try:
        return ebay_trading.end_listing(token, listing.ebay_listing_id)
    except TradingError:
        status = None
        if listing.ebay_listing_id:
            status, _sold, _watch = ebay_trading.listing_status(
                token, listing.ebay_listing_id)
        if status in ("ended", "sold"):
            return {"ended": False, "not_live": True, "status": status,
                    "message": ("This one already sold on eBay — filing it "
                                "under Sold." if status == "sold" else
                                "This listing had already ended on eBay — "
                                "moving it to Inactive.")}
        raise

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, Optional

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


# Stamped on a record when the account it belonged to could not be named. A
# connection made before the identity scope was granted 403s on the identity
# call, so a switch away from it is visible (the saved policy ids stop
# existing) while the account behind it never was. It is deliberately not a
# username: no account can ever match it, which is the point for reads — a
# sweep must not re-confirm those listings under whoever is connected now.
#
# It is NOT evidence that the seller is on a different account, only that we
# cannot prove they are on the same one, so nothing that refuses a write the
# seller explicitly asked for may act on it. `named_account_of` is the reader
# for those callers; `account_of` keeps the raw value for scoping.
UNKNOWN_ACCOUNT = "previous account"


def account_of(listing: Listing | dict) -> str:
    """The eBay account a record's item id lives on ("" = not recorded)."""
    value = (listing.get("ebay_account") if isinstance(listing, dict)
             else getattr(listing, "ebay_account", ""))
    return (value or "").strip()


def named_account_of(listing: Listing | dict) -> str:
    """The owning account when it has a real name, "" otherwise.

    Callers that block an action on "this belongs to someone else" need a name
    they can show and compare. UNKNOWN_ACCOUNT is neither: it reads as a
    username in a sentence ("belongs to @previous account") and it can never
    equal the connected one, so treating it as a rival account refuses every
    publish of an imported listing, forever, with no way back.
    """
    owner = account_of(listing)
    return "" if owner == UNKNOWN_ACCOUNT else owner


def belongs_to(listing: Listing | dict, account: str) -> bool:
    """True when this record may be read or written on `account`'s behalf.

    A record with no account recorded is legacy — it predates the field, and
    the only account it can plausibly belong to is the one connected when it
    was made, so it passes. A record stamped with a DIFFERENT account never
    does: its item id belongs to another seller, and eBay's GetItem will
    happily answer for it anyway (item detail is public), which is precisely
    how a second connected account kept re-confirming the first account's
    listings as live.
    """
    owner = account_of(listing)
    return not owner or not account or owner == (account or "").strip()


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


def recent_sales(token: str) -> dict[str, dict]:
    """{eBay item id: sale} for everything that sold in eBay's recent (~90 day)
    window — see ebay_trading.sold_sales. Best-effort: an unreadable list
    contributes nothing rather than sinking the sync that asked."""
    try:
        return ebay_trading.sold_sales(token, limit=_INACTIVE_LIMIT)
    except Exception as exc:  # noqa: BLE001 - sale amounts are additive
        log.info("sync: couldn't read sold transactions: %s", exc)
        return {}


def stamp_sale(data: dict, sale: Optional[dict] = None,
               mark_now: bool = False) -> dict:
    """Record what a SOLD listing actually earned, returning a new dict.

    `sale` is one entry from recent_sales (None when eBay didn't report the
    sale at all — an item that sold outside the 90-day window, or a list that
    couldn't be read). Only fields eBay actually gave us are written, so a
    sale price the seller typed in by hand survives a sync that learns
    nothing new.

    `mark_now` marks a record that is transitioning to sold RIGHT NOW, which
    settles two things nothing else can:

      - Any sale numbers already on the record belong to a PREVIOUS sale (the
        listing sold, was relisted, and sold again), so they're cleared rather
        than left to stand in for this one. Keeping them was the worse half:
        the new sale would carry the old date and sit outside the dashboard's
        window entirely.
      - Failing anything from eBay, sold_at becomes now — the sync just
        watched it happen.

    So pass it ONLY for a real transition: on a first-time import of an old
    sale it would date a historical sale to today and inflate "sold this week".
    """
    out = dict(data)
    if mark_now:
        out.pop("sold_price", None)
        out.pop("sold_at", None)
    if sale:
        price = sale.get("price")
        if price is not None:
            try:
                out["sold_price"] = round(float(price), 2)
            except (TypeError, ValueError):
                pass
        if sale.get("sold_at"):
            out["sold_at"] = str(sale["sold_at"])
        if sale.get("currency"):
            out["currency"] = sale["currency"]
        qty = int(sale.get("quantity") or 0)
        if qty:
            out["sold_quantity"] = max(int(out.get("sold_quantity") or 0), qty)
    if mark_now and not out.get("sold_at"):
        out["sold_at"] = datetime.now(timezone.utc).isoformat()
    return out


def import_active(token: str, user_id: str, limit: int = ACTIVE_LIMIT,
                  on_progress: Optional[Callable[[str, int, int], None]] = None,
                  account: str = "") -> dict:
    """Mirror the seller's eBay store into the app: every ACTIVE listing (up
    to `limit`), plus recently ENDED (unsold → status 'ended', the Inactive
    tab) and SOLD listings (status 'sold'), each capped at
    EBAY_SYNC_INACTIVE_LIMIT. Returns {"found", "imported", "updated",
    "failed"}.

    `on_progress(phase, done, total)` is called as the run advances — one
    GetItem per listing means a real store takes minutes, and the caller runs
    this as a background job so the seller watches a count instead of a
    request that never answers.

    `account` is the connected eBay username. Everything this run writes is
    stamped with it, and records belonging to a DIFFERENT account are left
    strictly alone — they're another seller's listings, and merging this
    account's store into them (or matching item ids across the two) is how a
    seller who connected a second account ended up with the first one's items.
    """
    def _tick(phase: str, done: int, total: int) -> None:
        if not on_progress:
            return
        try:
            on_progress(phase, done, total)
        except Exception as exc:  # noqa: BLE001 - progress must never sink a sync
            log.debug("sync: progress callback failed: %s", exc)

    jobs: list[tuple[str, str]] = []  # (item_id, status) — first entry wins
    seen: set[str] = set()

    def _add(item_ids: list[str], status: str) -> None:
        for i in item_ids:
            if i not in seen:
                seen.add(i)
                jobs.append((i, status))

    _tick("listing", 0, 0)
    _add(ebay_trading.active_listing_ids(token, limit=limit), "published")
    # Inactive/sold mirrors are additive — a failure there must not sink the
    # main active-store sync. The sold list is read through recent_sales
    # (same one paged call) because it carries the transaction amounts as
    # well as the ids, and the amount is the only place a discounted sale —
    # an accepted offer — is visible at all.
    sales = recent_sales(token)
    _add(list(sales), "sold")
    try:
        _add(ebay_trading.unsold_listing_ids(token, limit=_INACTIVE_LIMIT), "ended")
    except Exception as exc:  # noqa: BLE001
        log.info("sync: couldn't list ended items: %s", exc)

    # Has to cover EVERY record the seller has, not a guess scaled off this
    # run's job count. A record the read misses is a record the dedupe below
    # can't match, and the visible result is the duplicate pair it exists to
    # remove — so the ceiling is deliberately far above any real store rather
    # than a multiple of what eBay happened to return.
    all_known = db.list_listings(limit=_KNOWN_LIMIT, user_id=user_id)
    # Only this account's records take part: matching, merging, and the stale-
    # mirror cleanup all key off eBay item ids, and ids from another account
    # are meaningless here (worse than meaningless — a collision would merge
    # two different sellers' listings into one row).
    known = {r["id"]: r for r in all_known
             if belongs_to(r.get("listing") or {}, account)}
    foreign = len(all_known) - len(known)
    if foreign:
        log.info("sync: user=%s skipping %d record(s) from another eBay "
                 "account (connected=%s)", user_id, foreign, account or "?")
    if len(all_known) >= _KNOWN_LIMIT:
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
    _tick("fetching", 0, len(jobs))
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, max(1, len(jobs)))) as pool:
        # submit + as_completed rather than pool.map: the results are still read
        # in eBay's order below (futures keep their slots), but the count can
        # tick as each GetItem lands instead of only when the last one does.
        futures = [pool.submit(_fetch, job) for job in jobs]
        for done_n, _ in enumerate(as_completed(futures), 1):
            _tick("fetching", done_n, len(jobs))
        fetched = [f.result() for f in futures]

    _tick("saving", 0, len(fetched))
    for done_n, (item_id, status, fresh) in enumerate(fetched, 1):
        _tick("saving", done_n, len(fetched))
        if fresh is None:
            failed += 1
            continue
        mirror_id = record_id(item_id)
        prior = owned.get(item_id) or known.get(mirror_id)
        rid = prior["id"] if prior else mirror_id
        data = _merge(prior.get("listing") if prior else None, fresh,
                      own_source=bool(prior) and not _is_mirror(prior))
        # Whose store this item is in. Written on every sync, so a record made
        # before the field existed is labelled the first time it's seen again.
        data["ebay_account"] = account
        if status == "sold":
            # What it went for, not what it was listed at. mark_now only for a
            # record we watched flip — backfilling an old sale must not date it
            # to today.
            data = stamp_sale(data, sales.get(item_id),
                              mark_now=bool(prior) and prior.get("status") != "sold")
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


def refresh_statuses(token: str, user_id: str, records: list[dict],
                     sales: Optional[dict] = None, account: str = "") -> int:
    """Re-check imported listings that are still marked live: sold/ended items
    get their status corrected, and watch/sold counters refreshed. Returns how
    many records changed. A None status (API blip) changes nothing.

    `account` is the connected eBay username; records belonging to a different
    one are skipped. GetItem answers for ANY seller's item, so without this a
    freshly connected account went on reporting the previous account's
    listings as live and healthy forever.

    `sales` is recent_sales()'s map, so a record that just sold records what it
    actually went for. Callers that already fetched it pass it in; anything
    else gets it fetched lazily — once, and only if something did sell, so a
    sweep that finds nothing new costs no extra eBay call.

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

    records = [r for r in records
               if belongs_to(r.get("listing") or {}, account)]
    probed = []
    if records:
        with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(records))) as pool:
            probed = list(pool.map(_probe, records))
    changed = 0
    known_sales = sales

    def _sale_for(item_id: str) -> Optional[dict]:
        nonlocal known_sales
        if known_sales is None:
            known_sales = recent_sales(token)
        return known_sales.get(item_id)

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
        if status == "sold":
            updates = stamp_sale(
                updates, _sale_for(str(data.get("ebay_listing_id") or "")),
                mark_now=rec.get("status") != "sold")
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


def reconcile_recent(token: str, user_id: str, records: list[dict],
                     account: str = "") -> tuple[int, set[str]]:
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

    sales = recent_sales(token)
    finished = set(sales) | _ids(ebay_trading.unsold_listing_ids, "ended")
    candidates = [r for r in records
                  if belongs_to(r.get("listing") or {}, account)
                  and _item_id_of(r.get("listing") or {}) in finished]
    if not candidates:
        return 0, set()
    changed = refresh_statuses(token, user_id, candidates, sales=sales,
                               account=account)
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
    # Which account it landed on, so a later account switch can tell this
    # listing apart from one made on the next account.
    listing.ebay_account = (c.get("ebay_username") or "").strip()
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

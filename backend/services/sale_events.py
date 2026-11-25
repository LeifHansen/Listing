"""Sale events: the app's own, simpler view of eBay's markdown sales.

A sale here is one thing: a percentage off, a start, an end, and a list of
listings. No coupons, no codes, no tiers — the Etsy "Run a sale" shape, which
is what a reseller means by a sale. It is run BY eBay (a markdown promotion,
see ebay_markdowns), so buyers see the old price struck through and it ends on
its own; this module keeps the app's mirror of those (db.SaleEvent) and
decides what the seller may do from here.

Both ways:

  * A sale made on the Manage tab (Apply discount…, Add to sale…, New sale) is
    created on eBay first and recorded here from eBay's answer — never the
    other way round, so the app never shows a sale eBay does not have.
  * A sale made in Seller Hub is pulled in by `refresh`, on the Sales screen's
    open and on the store sync, at most every REFRESH_MAX_AGE unless forced.
    One the app's model cannot express (tiers, an amount off, listings picked
    by rule) is shown READ-ONLY with the reason and a link to Seller Hub
    (ebay_markdowns.classify) — never flattened by an edit made here.

Rules every write keeps:

  * ONE SALE PER LISTING. eBay will not put a listing in two markdowns, and a
    seller cannot reason about which of two discounts a buyer sees. A listing
    already in a sale is skipped, with the sale's name — unless the seller
    asked to MOVE it, in which case it is taken out of the old sale first.
    A read-only sale's listings are never moved: that sale is Seller Hub's.
  * RE-READ BEFORE EVERY PUT. A PUT replaces the whole promotion, so each write
    fetches the sale from eBay a moment before it, and changes only what was
    asked (ebay_markdowns.patch_body).
  * ONE WRITE PER ACCOUNT AT A TIME. Two tabs adding listings to the same sale
    at once would each PUT a list missing the other's; the second is told to
    wait (Busy) rather than allowed to race.
  * eBay ELIGIBILITY IS eBAY'S. A listing eBay refuses is dropped from the
    request and reported as a skip with eBay's own sentence, and the rest go
    ahead (`_with_drops`).

Listing writes are NOT made: a sale never touches a listing row (its price,
its updated_at), which is why `membership` is worked out from this table and
the browser joins it to the cards itself.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

import httpx

from .. import db
from ..config import log
from . import ebay_markdowns
from .bulk_actions import BulkResult

# How long a pull from eBay is trusted before the Sales screen asks again.
REFRESH_MAX_AGE = 600
# How long an ended sale stays on the Sales screen.
KEEP_ENDED = timedelta(days=30)
# How many times one write drops the listings eBay refused and tries again.
DROP_RETRIES = 3

_FRESH: dict[str, tuple[float, str]] = {}      # uid -> (when, eBay account)
_FRESH_LOCK = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()

AUCTION_FORMATS = ("AUCTION", "AUCTION_BIN")


class Busy(Exception):
    """Another change to this seller's sales is in flight."""


class Refused(Exception):
    """A request the seller can fix, in their words."""


# ------------------------------------------------------------- reading rows

def _utc(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def effective_state(row: dict, now: Optional[datetime] = None) -> str:
    """draft | scheduled | running | paused | ended — from the DATES as much
    as from eBay's status word, so a row read a week after its last pull still
    says the right thing: a sale whose end has passed has ended, whatever the
    stored status says."""
    now = now or datetime.now(timezone.utc)
    status = str(row.get("status") or "").upper()
    if status in ebay_markdowns.FINISHED_STATUSES:
        return "ended"
    end = _utc(row.get("end_at"))
    if end is not None and end <= now:
        return "ended"
    if status == "DRAFT":
        return "draft"
    if status == "PAUSED":
        return "paused"
    start = _utc(row.get("start_at"))
    if start is not None and start > now:
        return "scheduled"
    return "running"


def editable(row: dict, now: Optional[datetime] = None) -> bool:
    return (not row.get("read_only_reason")
            and effective_state(row, now) in ("scheduled", "running", "draft"))


def auto_name(percent: float, days: Optional[float], now: Optional[datetime] = None) -> str:
    """The name a quick "Apply discount…" sale gets: "20% off · 7 days · Nov 24"."""
    now = now or datetime.now(timezone.utc)
    when = f"{now:%b} {now.day}"
    if days:
        d = int(days) if float(days) == int(days) else days
        return f"{int(percent)}% off · {d} day{'' if d == 1 else 's'} · {when}"
    return f"{int(percent)}% off · {when}"


def public(row: dict, now: Optional[datetime] = None) -> dict:
    """One sale as the API returns it."""
    ids = row.get("item_ids")
    return {
        "id": row["id"],
        "ebay_promotion_id": row.get("ebay_promotion_id") or "",
        "name": row.get("name") or "",
        "percent_off": row.get("percent_off"),
        "start_at": row.get("start_at") or "",
        "end_at": row.get("end_at") or "",
        "status": row.get("status") or "",
        "state": effective_state(row, now),
        "source": row.get("source") or "ebay",
        "editable": editable(row, now),
        "read_only_reason": row.get("read_only_reason") or "",
        "item_ids": ids,
        "count": len(ids) if isinstance(ids, list) else None,
        "seller_hub_url": ebay_markdowns.seller_hub_url(row.get("ebay_promotion_id") or ""),
        "synced_at": row.get("synced_at") or "",
    }


def _account(creds: Optional[dict]) -> str:
    return str((creds or {}).get("ebay_username") or "")


def rows_for(uid: str, creds: Optional[dict], strict: bool = False) -> list[dict]:
    """The seller's recorded sales for the eBay account they are connected
    as. A sale recorded under another account is not theirs to see here — it
    belongs to a store that is not connected."""
    acct = _account(creds)
    return [r for r in db.list_sale_events(uid, strict=strict)
            if not acct or (r.get("ebay_account") or "") in ("", acct)]


def membership(rows: Iterable[dict], now: Optional[datetime] = None) -> dict[str, dict]:
    """eBay item id -> the sale it is in, over the sales still to come or
    running. An ended sale holds nothing."""
    out: dict[str, dict] = {}
    for row in rows:
        if effective_state(row, now) == "ended":
            continue
        for item in row.get("item_ids") or []:
            out.setdefault(str(item), row)
    return out


def membership_best_effort(uid: str, creds: Optional[dict] = None) -> dict[str, dict]:
    """membership, for a caller that must not fail over it (Lower prices
    consults it to stay off listings in a sale). Never raises."""
    try:
        return membership(rows_for(uid, creds))
    except Exception as exc:  # noqa: BLE001 - a guard, not the work
        log.info("sale membership unavailable for %s: %s", uid, exc)
        return {}


# --------------------------------------------------------------- the pull

def _fresh(uid: str, acct: str, max_age: float) -> bool:
    import time
    with _FRESH_LOCK:
        hit = _FRESH.get(uid)
    return bool(hit and hit[1] == acct and time.time() - hit[0] < max_age)


def _mark_fresh(uid: str, acct: str) -> None:
    import time
    with _FRESH_LOCK:
        if len(_FRESH) > 2000:
            _FRESH.clear()
        _FRESH[uid] = (time.time(), acct)


def forget(uid: str) -> None:
    """Drop the freshness mark, so the next read pulls from eBay."""
    with _FRESH_LOCK:
        _FRESH.pop(uid, None)


def _record(uid: str, creds: dict, pid: str, detail: dict,
            *, source: Optional[str] = None, http: Optional[httpx.Client] = None,
            now: Optional[datetime] = None) -> Optional[dict]:
    """Write eBay's view of one promotion into this seller's row for it."""
    now = now or datetime.now(timezone.utc)
    verdict = ebay_markdowns.classify(detail)
    status = str(detail.get("promotionStatus") or "").upper()
    ids = verdict.item_ids
    if ids is None and status in ebay_markdowns.LIVE_STATUSES:
        # A rule picked the listings; eBay is the only one who knows which.
        try:
            ids = ebay_markdowns.listing_set(creds, pid, client=http)
        except Exception as exc:  # noqa: BLE001 - shown as "unknown", not wrong
            log.info("sale %s: couldn't read its listings: %s", pid, exc)
    fields = dict(
        ebay_account=_account(creds), name=str(detail.get("name") or "")[:255],
        percent_off=verdict.percent, start_at=_utc(detail.get("startDate")),
        end_at=_utc(detail.get("endDate")), status=status[:16],
        read_only_reason=verdict.reason[:160], item_ids=ids, synced_at=now)
    if source:
        fields["source"] = source
    return db.upsert_sale_event(uid, pid, **fields)


def refresh(uid: str, creds: Optional[dict], *, force: bool = False,
            max_age: float = REFRESH_MAX_AGE,
            now: Optional[datetime] = None) -> dict:
    """Pull the seller's sales from eBay into the mirror.

    {"checked": bool, "needs_reconnect": bool, "refreshed": bool}. A pull
    that could not reach eBay leaves the rows as they were and says so
    (`checked` false) — the screen shows what it last knew, marked as such,
    rather than an empty list that reads as "no sales".
    """
    acct = _account(creds)
    if not creds or not creds.get("access_token"):
        return {"checked": False, "needs_reconnect": False, "refreshed": False}
    if not force and _fresh(uid, acct, max_age):
        return {"checked": True, "needs_reconnect": False, "refreshed": False}
    now = now or datetime.now(timezone.utc)
    http = httpx.Client(timeout=30)
    try:
        try:
            summaries = ebay_markdowns.list_markdowns(creds, client=http)
        except ebay_markdowns.ScopeError:
            return {"checked": False, "needs_reconnect": True, "refreshed": False}
        except Exception as exc:  # noqa: BLE001 - the screen says "couldn't check"
            log.info("sales pull failed for %s: %s", uid, exc)
            return {"checked": False, "needs_reconnect": False, "refreshed": False}
        known = {r["ebay_promotion_id"]: r for r in rows_for(uid, creds, strict=True)}
        seen: list[str] = []
        for summary in summaries:
            ptype = str(summary.get("promotionType") or ebay_markdowns.PROMOTION_TYPE)
            if ptype.upper() != ebay_markdowns.PROMOTION_TYPE:
                continue
            pid = str(summary.get("promotionId") or "").strip()
            if not pid:
                continue
            status = str(summary.get("promotionStatus") or "").upper()
            prior = known.get(pid)
            seen.append(pid)
            # A sale that had already ended last time we looked has nothing new
            # to say; one GET saved per finished sale on every pull.
            if (prior and status in ebay_markdowns.FINISHED_STATUSES
                    and str(prior.get("status") or "").upper() == status):
                continue
            try:
                detail = ebay_markdowns.get_markdown(creds, pid, client=http)
            except ebay_markdowns.NotFound:
                seen.pop()
                continue
            except Exception as exc:  # noqa: BLE001 - keep what we had
                log.info("sale %s: detail unavailable: %s", pid, exc)
                continue
            detail.setdefault("promotionStatus", status)
            _record(uid, creds, pid, detail, http=http, now=now)
        db.delete_sale_events_except(uid, acct, seen, ended_before=now - KEEP_ENDED)
        _mark_fresh(uid, acct)
        return {"checked": True, "needs_reconnect": False, "refreshed": True}
    finally:
        http.close()


# --------------------------------------------------------------- the writes

@contextmanager
def _one_at_a_time(uid: str):
    with _LOCKS_LOCK:
        lock = _LOCKS.setdefault(uid, threading.Lock())
    if not lock.acquire(blocking=False):
        raise Busy("Another change to your sales is still going — try again "
                   "in a moment.")
    try:
        yield
    finally:
        lock.release()


def _title(rec: dict) -> str:
    return ((rec.get("listing") or {}).get("title") or rec.get("title")
            or "this listing")


def _row(rec: dict, message: str, needs_you: bool = True) -> dict:
    return {"listing_id": rec.get("id") or "", "title": _title(rec),
            "message": message, "needs_you": needs_you}


def _preflight(records: list[dict], members: dict[str, dict], *,
               target_pid: str = "", move: bool = False,
               result: BulkResult) -> tuple[list[dict], dict[str, list[str]]]:
    """The records that can go into a sale, and — for a move — which of them
    come out of which sale first. Everything else is reported as a skip."""
    ok: list[dict] = []
    moving: dict[str, list[str]] = {}
    for rec in records:
        if rec.get("status") == "missing":
            result.skipped.append(_row(rec, "Listing not found.", False))
            continue
        if rec.get("status") not in ("published", "live"):
            result.skipped.append(_row(rec, "No longer live on eBay.", False))
            continue
        data = rec.get("listing") or {}
        item = str(data.get("ebay_listing_id") or "").strip()
        if not item:
            result.skipped.append(_row(rec, "This listing isn't on eBay."))
            continue
        if str(data.get("listing_format") or "").upper() in AUCTION_FORMATS:
            result.skipped.append(_row(
                rec, "Auctions can't go in a sale — eBay's sales are for Buy It "
                     "Now listings.", False))
            continue
        current = members.get(item)
        if current and current.get("ebay_promotion_id") == target_pid:
            result.skipped.append(_row(rec, "Already in this sale.", False))
            continue
        if current and current.get("read_only_reason"):
            result.skipped.append(_row(
                rec, f"It's in “{current.get('name') or 'a sale'}”, which is "
                     "managed in Seller Hub."))
            continue
        if current and not move:
            result.skipped.append(_row(
                rec, f"It's already in “{current.get('name') or 'another sale'}”. "
                     "A listing can only be in one sale."))
            continue
        if current:
            moving.setdefault(current["ebay_promotion_id"], []).append(item)
        ok.append(rec)
    return ok, moving


def _item(rec: dict) -> str:
    return str((rec.get("listing") or {}).get("ebay_listing_id") or "").strip()


def _with_drops(call: Callable[[list[str]], None], records: list[dict],
                result: BulkResult) -> list[dict]:
    """Run `call(item_ids)`, dropping the listings eBay names as refused and
    trying again with the rest. Returns the records that went in. Raises the
    last refusal when eBay refused without naming anyone, or refused them
    all."""
    left = list(records)
    for _attempt in range(DROP_RETRIES + 1):
        ids = [_item(r) for r in left]
        try:
            call(ids)
            return left
        except ebay_markdowns.MarkdownRefused as exc:
            named = set(exc.listing_ids)
            if not named or named >= set(ids):
                if named:
                    for rec in left:
                        result.skipped.append(_row(rec, str(exc)))
                    return []
                raise
            for rec in left:
                if _item(rec) in named:
                    result.skipped.append(_row(rec, str(exc)))
            left = [r for r in left if _item(r) not in named]
            if not left:
                return []
    raise ebay_markdowns.MarkdownRefused(
        "eBay kept refusing listings in this sale — try fewer at a time.")


def _image_for(records: list[dict]) -> str:
    """An eBay-hosted photo from one of the sale's listings, for the sale's
    promo image (VERIFY whether eBay requires one)."""
    for rec in records:
        for url in (rec.get("listing") or {}).get("image_urls") or []:
            if isinstance(url, str) and url.startswith("https://"):
                return url
    return ""


def _take_out(uid: str, creds: dict, pid: str, item_ids: list[str],
              http: httpx.Client, now: datetime) -> None:
    """Remove `item_ids` from sale `pid` on eBay (re-read first), ending the
    sale if nothing would be left in it, and record what eBay holds after."""
    detail = ebay_markdowns.get_markdown(creds, pid, client=http)
    verdict = ebay_markdowns.classify(detail)
    if not verdict.editable or verdict.item_ids is None:
        raise Refused(f"“{detail.get('name') or 'That sale'}” is managed in "
                      "Seller Hub — change it there.")
    gone = {str(i) for i in item_ids}
    keep = [i for i in verdict.item_ids if i not in gone]
    if keep:
        ebay_markdowns.update(creds, pid, ebay_markdowns.patch_body(detail, item_ids=keep),
                              client=http)
        _resync(uid, creds, pid, http, now)
    else:
        _end_on_ebay(uid, creds, pid, detail, http, now)


def _resync(uid: str, creds: dict, pid: str, http: httpx.Client,
            now: datetime, source: Optional[str] = None) -> Optional[dict]:
    try:
        detail = ebay_markdowns.get_markdown(creds, pid, client=http)
    except ebay_markdowns.NotFound:
        row = next((r for r in rows_for(uid, creds)
                    if r["ebay_promotion_id"] == pid), None)
        if row:
            db.delete_sale_event(uid, row["id"])
        return None
    return _record(uid, creds, pid, detail, source=source, http=http, now=now)


def _end_on_ebay(uid: str, creds: dict, pid: str, detail: dict,
                 http: httpx.Client, now: datetime) -> None:
    """End a sale: delete one that has not started, pull a running one's end
    date in to now (falling back to delete if eBay will not move it)."""
    start = _utc(detail.get("startDate"))
    status = str(detail.get("promotionStatus") or "").upper()
    if status == "DRAFT" or (start is not None and start > now):
        ebay_markdowns.delete(creds, pid, client=http)
        _resync(uid, creds, pid, http, now)
        return
    try:
        ebay_markdowns.update(
            creds, pid,
            ebay_markdowns.patch_body(detail, end=now + ebay_markdowns.END_BUFFER),
            client=http)
    except ebay_markdowns.MarkdownRefused as exc:
        log.info("sale %s: end date refused (%s); deleting instead", pid, exc)
        ebay_markdowns.delete(creds, pid, client=http)
    _resync(uid, creds, pid, http, now)


def _apply_moves(uid: str, creds: dict, moving: dict[str, list[str]],
                 records: list[dict], result: BulkResult, http: httpx.Client,
                 now: datetime) -> list[dict]:
    """Take moving listings out of their old sales. A sale that will not let
    them go keeps them, and they are reported rather than put in twice."""
    stuck: set[str] = set()
    for pid, items in moving.items():
        try:
            _take_out(uid, creds, pid, items, http, now)
        except (ebay_markdowns.MarkdownRefused, Refused, ebay_markdowns.NotFound) as exc:
            stuck.update(items)
            msg = str(exc) or "eBay wouldn't take it out of its old sale."
            for rec in records:
                if _item(rec) in items:
                    result.failed.append(_row(rec, msg))
    return [r for r in records if _item(r) not in stuck]


def _put_back(uid: str, creds: dict, moving: dict[str, list[str]],
              http: httpx.Client, now: datetime) -> None:
    """Best effort: return moved listings to the sales they came out of."""
    for pid, items in moving.items():
        try:
            detail = ebay_markdowns.get_markdown(creds, pid, client=http)
            have = ebay_markdowns.classify(detail).item_ids or []
            ebay_markdowns.update(creds, pid, ebay_markdowns.patch_body(
                detail, item_ids=list(dict.fromkeys(have + items))), client=http)
            _resync(uid, creds, pid, http, now)
        except Exception as exc:  # noqa: BLE001 - reported by the caller's failure
            log.warning("sale %s: couldn't put %d moved listings back: %s",
                        pid, len(items), exc)


def _unplaced(moving: dict[str, list[str]], moved: list[dict],
              went: list[dict]) -> dict[str, list[str]]:
    """{old sale: item ids} for the listings that left their old sale (still
    in `moved`, the records _apply_moves let go) but did not go into the new
    one (`went`) -- eBay refused them there. _put_back's argument."""
    left = {_item(r) for r in moved}
    placed = {_item(r) for r in went}
    out = {pid: [i for i in items if i in left and i not in placed]
           for pid, items in moving.items()}
    return {pid: items for pid, items in out.items() if items}


def _changed(result: BulkResult, records: list[dict], moved_from: dict[str, str]) -> None:
    for rec in records:
        row = {"listing_id": rec.get("id") or "", "title": _title(rec)}
        if moved_from.get(_item(rec)):
            row["moved_from"] = moved_from[_item(rec)]
        result.changed.append(row)


def create(uid: str, creds: dict, records: list[dict], *, percent: float,
           start=None, end=None, days=None, name: str = "", move: bool = False,
           now: Optional[datetime] = None) -> tuple[Optional[dict], BulkResult]:
    """Start a new sale on eBay over `records`, and record it.

    Returns (the new sale's row or None when nothing could go in, the
    per-listing report)."""
    now = now or datetime.now(timezone.utc)
    pct = ebay_markdowns.validate_percent(percent)
    begin, finish = ebay_markdowns.validate_window(start, end, days, now)
    span = (days if days is not None and end in (None, "")
            else round((finish - begin).total_seconds() / 86400))
    label = (name or "").strip() or auto_name(pct, span, now)
    result = BulkResult()
    with _one_at_a_time(uid):
        refresh(uid, creds, max_age=120, now=now)
        rows = rows_for(uid, creds, strict=True)
        members = membership(rows, now)
        ok, moving = _preflight(records, members, move=move, result=result)
        if len(ok) > ebay_markdowns.MAX_LISTINGS:
            raise Refused(f"A sale holds at most {ebay_markdowns.MAX_LISTINGS} "
                          "listings — pick fewer, or start a second sale.")
        if not ok:
            return None, result
        names = {r["ebay_promotion_id"]: r.get("name") or "" for r in rows}
        moved_from = {i: names.get(pid, "") for pid, items in moving.items() for i in items}
        http = httpx.Client(timeout=30)
        try:
            ok = _apply_moves(uid, creds, moving, ok, result, http, now)
            if not ok:
                return None, result
            made: dict[str, str] = {}

            def _make(ids: list[str]) -> None:
                made["pid"] = ebay_markdowns.create(creds, ebay_markdowns.build_body(
                    name=label, percent=pct, start=begin, end=finish, item_ids=ids,
                    image_url=_image_for(ok)), client=http)

            try:
                went = _with_drops(_make, ok, result)
            except Exception:
                # The moved listings already left their old sales; a sale that
                # then could not be made would leave them in none. Put them
                # back where they were before saying it failed.
                _put_back(uid, creds, moving, http, now)
                raise
            if not made.get("pid"):
                _put_back(uid, creds, moving, http, now)
                return None, result
            # Moved, then dropped by eBay from the new sale: back, not nowhere.
            _put_back(uid, creds, _unplaced(moving, ok, went), http, now)
            row = _resync(uid, creds, made["pid"], http, now, source="app")
            _changed(result, went, moved_from)
            return row, result
        finally:
            http.close()


def add(uid: str, creds: dict, event_id: str, records: list[dict], *,
        move: bool = False, now: Optional[datetime] = None) -> tuple[dict, BulkResult]:
    """Put `records` into an existing sale (re-read from eBay first, so
    nothing added in Seller Hub since is dropped)."""
    now = now or datetime.now(timezone.utc)
    result = BulkResult()
    with _one_at_a_time(uid):
        row = _own_row(uid, creds, event_id)
        if not editable(row, now):
            raise Refused(_not_editable(row, now))
        pid = row["ebay_promotion_id"]
        refresh(uid, creds, max_age=120, now=now)
        rows = rows_for(uid, creds, strict=True)
        ok, moving = _preflight(records, membership(rows, now), target_pid=pid,
                                move=move, result=result)
        if not ok:
            return public(row, now), result
        names = {r["ebay_promotion_id"]: r.get("name") or "" for r in rows}
        moved_from = {i: names.get(p, "") for p, items in moving.items() for i in items}
        http = httpx.Client(timeout=30)
        try:
            # Read and checked BEFORE anything leaves its old sale. A refusal
            # here used to come after the moves, stranding the listings in no
            # sale and ending an old sale the move had emptied. The moves touch
            # only the OLD sales, so this is still the copy the PUT starts from.
            detail = ebay_markdowns.get_markdown(creds, pid, client=http)
            verdict = ebay_markdowns.classify(detail)
            if not verdict.editable or verdict.item_ids is None:
                raise Refused("That sale is managed in Seller Hub now — add to it there.")
            if len(verdict.item_ids) + len(ok) > ebay_markdowns.MAX_LISTINGS:
                raise Refused(f"A sale holds at most {ebay_markdowns.MAX_LISTINGS} "
                              "listings — start a second sale for the rest.")
            ok = _apply_moves(uid, creds, moving, ok, result, http, now)
            if not ok:
                return public(row, now), result

            def _put(ids: list[str]) -> None:
                ebay_markdowns.update(creds, pid, ebay_markdowns.patch_body(
                    detail, item_ids=list(dict.fromkeys(verdict.item_ids + ids))),
                    client=http)

            try:
                went = _with_drops(_put, ok, result)
            except Exception:
                _put_back(uid, creds, _unplaced(moving, ok, []), http, now)
                raise
            _put_back(uid, creds, _unplaced(moving, ok, went), http, now)
            fresh = _resync(uid, creds, pid, http, now) or row
            _changed(result, went, moved_from)
            return public(fresh, now), result
        finally:
            http.close()


def remove(uid: str, creds: dict, records: list[dict], *,
           now: Optional[datetime] = None) -> BulkResult:
    """Take `records` out of whichever sales they are in — one re-read and one
    PUT per sale, and a sale left with nothing in it is ended."""
    now = now or datetime.now(timezone.utc)
    result = BulkResult()
    with _one_at_a_time(uid):
        refresh(uid, creds, max_age=120, now=now)
        members = membership(rows_for(uid, creds, strict=True), now)
        by_sale: dict[str, list[dict]] = {}
        for rec in records:
            if rec.get("status") == "missing":
                result.skipped.append(_row(rec, "Listing not found.", False))
                continue
            sale = members.get(_item(rec))
            if not sale:
                result.skipped.append(_row(rec, "It isn't in a sale.", False))
                continue
            if sale.get("read_only_reason"):
                result.skipped.append(_row(
                    rec, f"“{sale.get('name') or 'Its sale'}” is managed in Seller "
                         "Hub — take it out there."))
                continue
            by_sale.setdefault(sale["ebay_promotion_id"], []).append(rec)
        http = httpx.Client(timeout=30)
        try:
            for pid, recs in by_sale.items():
                try:
                    _take_out(uid, creds, pid, [_item(r) for r in recs], http, now)
                except (ebay_markdowns.MarkdownRefused, Refused,
                        ebay_markdowns.NotFound) as exc:
                    msg = str(exc) or "eBay wouldn't take it out of the sale."
                    result.failed.extend(_row(r, msg) for r in recs)
                    continue
                _changed(result, recs, {})
        finally:
            http.close()
    return result


def edit(uid: str, creds: dict, event_id: str, *, name: Optional[str] = None,
         start=None, end=None, percent=None,
         now: Optional[datetime] = None) -> dict:
    """Rename a sale, move its dates, or (before it starts) change its
    discount."""
    now = now or datetime.now(timezone.utc)
    with _one_at_a_time(uid):
        row = _own_row(uid, creds, event_id)
        if not editable(row, now):
            raise Refused(_not_editable(row, now))
        state = effective_state(row, now)
        pid = row["ebay_promotion_id"]
        new_pct = None
        if percent not in (None, ""):
            new_pct = ebay_markdowns.validate_percent(percent)
            if state == "running" and new_pct != row.get("percent_off"):
                raise Refused("eBay doesn't let a sale change its discount once it "
                              "has started — end it and start a new one.")
        new_start = _utc(start) if start not in (None, "") else None
        if new_start is not None and state == "running":
            raise Refused("This sale has already started — only its end date "
                          "and name can change now.")
        if new_start is not None and new_start < now:
            new_start = now + ebay_markdowns.START_BUFFER
        new_end = _utc(end) if end not in (None, "") else None
        begin = new_start or _utc(row.get("start_at")) or now
        if new_end is not None:
            if new_end <= max(now, begin):
                raise Refused("The sale has to end after it starts, and in the future.")
            if new_end - begin > timedelta(days=ebay_markdowns.MAX_DAYS, minutes=5):
                raise Refused(f"eBay runs a sale for at most {ebay_markdowns.MAX_DAYS} days.")
        http = httpx.Client(timeout=30)
        try:
            detail = ebay_markdowns.get_markdown(creds, pid, client=http)
            ebay_markdowns.update(creds, pid, ebay_markdowns.patch_body(
                detail, name=(name.strip() or None) if isinstance(name, str) else None,
                start=new_start, end=new_end, percent=new_pct), client=http)
            fresh = _resync(uid, creds, pid, http, now) or row
            return public(fresh, now)
        finally:
            http.close()


def end(uid: str, creds: dict, event_id: str, *,
        now: Optional[datetime] = None) -> Optional[dict]:
    """End a sale now (or cancel one that has not started)."""
    now = now or datetime.now(timezone.utc)
    with _one_at_a_time(uid):
        row = _own_row(uid, creds, event_id)
        if row.get("read_only_reason"):
            raise Refused("This sale is managed in Seller Hub — end it there.")
        if effective_state(row, now) == "ended":
            return public(row, now)
        pid = row["ebay_promotion_id"]
        http = httpx.Client(timeout=30)
        try:
            try:
                detail = ebay_markdowns.get_markdown(creds, pid, client=http)
            except ebay_markdowns.NotFound:
                db.delete_sale_event(uid, row["id"])
                return None
            _end_on_ebay(uid, creds, pid, detail, http, now)
            fresh = next((r for r in rows_for(uid, creds)
                          if r["ebay_promotion_id"] == pid), None)
            return public(fresh, now) if fresh else None
        finally:
            http.close()


def _own_row(uid: str, creds: dict, event_id: str) -> dict:
    row = db.get_sale_event(uid, event_id)
    acct = _account(creds)
    if not row or (acct and (row.get("ebay_account") or "") not in ("", acct)):
        raise LookupError("Sale not found.")
    return row


def _not_editable(row: dict, now: datetime) -> str:
    if row.get("read_only_reason"):
        return (f"This sale is managed in Seller Hub ({row['read_only_reason'].lower()}) "
                "— change it there.")
    return "This sale has ended."

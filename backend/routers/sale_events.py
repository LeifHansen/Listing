"""The Sales screen's routes: eBay sale events (markdowns), both ways.

Every route here answers with the seller's whole list of sales (`events`), the
way the saved-views routes answer with the whole strip: the browser adopts
the server's answer instead of patching a local copy, so what is on screen is
what is recorded. A write also carries `report` — the bulk report shape
(services/bulk_actions.BulkResult), per listing — so the Manage tab says what
happened to each ticked listing in the same words as every other bulk action.

The work is services/sale_events; this file is who is asking, which listings
they mean, and the status code a refusal gets. It lives under routers/ by the
rules in tests/test_a_patch_on_main_never_silently_misses.py: it never imports
main, and it binds none of the names the tests patch there.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from .. import db, errors
from ..config import log
from ..services import ebay_markdowns, sale_events
from . import deps

router = APIRouter()

# How many of the seller's listings "everything in this store category" or
# "every active listing" reads to find the ones to put in a sale. Above any
# sale eBay will take (ebay_markdowns.MAX_LISTINGS), so the refusal that comes
# back for too many is eBay's limit, not a page that was cut.
SALE_SCAN_CAP = 3000


def _creds(request: Request) -> dict:
    creds = deps.ebay_creds_for(request)
    if not creds:
        raise HTTPException(400, "Connect eBay first.")
    return creds


def _listing(uid: str, creds: dict, *, refreshed: Optional[dict] = None,
             now: Optional[datetime] = None) -> dict:
    """The answer every route shares."""
    now = now or datetime.now(timezone.utc)
    rows = sale_events.rows_for(uid, creds, strict=True)
    out = {
        "events": [sale_events.public(r, now) for r in rows],
        "limits": ebay_markdowns.limits(),
        "connected": True,
    }
    if refreshed is not None:
        out.update(checked=refreshed.get("checked", False),
                   needs_reconnect=refreshed.get("needs_reconnect", False),
                   refreshed=refreshed.get("refreshed", False))
    synced = [r.get("synced_at") for r in rows if r.get("synced_at")]
    out["synced_at"] = max(synced) if synced else ""
    return out


def _guarded(fn):
    """The status code each refusal gets."""
    try:
        return fn()
    except ebay_markdowns.ScopeError as exc:
        raise HTTPException(400, "Reconnect your eBay account to run sales — it "
                                 "was connected before sales were possible here."
                            ) from exc
    except ebay_markdowns.MarkdownRefused as exc:
        raise HTTPException(400, str(exc) or "eBay refused that sale.") from exc
    except ebay_markdowns.NotFound as exc:
        raise HTTPException(404, "That sale isn't on eBay any more — refresh "
                                 "Sales.") from exc
    except sale_events.Busy as exc:
        raise HTTPException(409, str(exc)) from exc
    except (sale_events.Refused, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, "Sale not found.") from exc
    except errors.StorageUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - a 502, with a reference
        reference = deps.support_reference()
        log.warning("sale events: eBay call failed [%s]: %s", reference, exc)
        raise HTTPException(502, "We couldn't reach eBay just now — try again in "
                                 f"a moment. (Ref {reference})") from exc


def _ids(payload: dict, none_picked: str) -> list[str]:
    ids = [str(i).strip() for i in (payload.get("listing_ids") or []) if str(i).strip()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise HTTPException(400, none_picked)
    if len(ids) > ebay_markdowns.MAX_LISTINGS:
        raise HTTPException(400, f"A sale holds at most {ebay_markdowns.MAX_LISTINGS} "
                                 "listings — pick fewer.")
    return ids


def _records(ids: list[str], uid: str) -> list[dict]:
    """The seller's records for `ids` (ownership enforced in the read), then a
    "missing" row for each id that is not theirs or is gone."""
    mine = db.get_listings(ids, uid)
    found = {r["id"] for r in mine}
    by_id = {r["id"]: r for r in mine}
    return [by_id[i] if i in found else {"id": i, "status": "missing", "listing": {}}
            for i in ids]


def _chosen(payload: dict, uid: str) -> list[dict]:
    """Which listings a new sale is over: the ticks, a store shelf, or every
    active listing — exactly one of them."""
    sources = [k for k in ("listing_ids", "store_category_id", "all_active")
               if payload.get(k)]
    if len(sources) != 1:
        raise HTTPException(400, "Say which listings go in the sale: the ones you "
                                 "ticked, a store category, or all active ones.")
    if sources[0] == "listing_ids":
        return _records(_ids(payload, "Pick at least one listing."), uid)
    live = db.list_listings(limit=SALE_SCAN_CAP, user_id=uid,
                            statuses=("published", "live"))
    if sources[0] == "store_category_id":
        shelf = str(payload.get("store_category_id")).strip()
        live = [r for r in live
                if str((r.get("listing") or {}).get("store_category_id") or "") == shelf]
    if not live:
        raise HTTPException(400, "There are no active listings there to put on sale.")
    return live


@router.get("/api/sale-events")
def list_sale_events(request: Request, refresh: bool = True) -> dict:
    """The seller's sales — pulled from eBay first when the last pull is more
    than sale_events.REFRESH_MAX_AGE old (`refresh=false` skips that)."""
    uid = deps.signed_in_uid(request)
    creds = deps.ebay_creds_for(request)
    if not creds:
        return {"events": [], "limits": ebay_markdowns.limits(), "connected": False,
                "checked": True, "needs_reconnect": False, "synced_at": ""}
    pulled = (sale_events.refresh(uid, creds) if refresh
              else {"checked": True, "needs_reconnect": False, "refreshed": False})
    return _listing(uid, creds, refreshed=pulled)


@router.post("/api/sale-events/refresh")
def refresh_sale_events(request: Request, payload: Optional[dict] = None) -> dict:
    """Pull the seller's sales from eBay now (`force` ignores the window)."""
    uid = deps.signed_in_uid(request)
    creds = _creds(request)
    pulled = sale_events.refresh(uid, creds, force=bool((payload or {}).get("force")))
    return _listing(uid, creds, refreshed=pulled)


@router.post("/api/sale-events")
def create_sale_event(payload: dict, request: Request) -> dict:
    """Start a sale on eBay: `percent`, `start_at` (or now), `end_at` or
    `days`, an optional `name` (a quick "Apply discount…" leaves it out and
    the sale is named for what it is), `move` to take listings out of the sale
    they are in, and exactly one of `listing_ids` / `store_category_id` /
    `all_active`."""
    uid = deps.signed_in_uid(request)
    creds = _creds(request)
    records = _chosen(payload, uid)
    row, report = _guarded(lambda: sale_events.create(
        uid, creds, records, percent=payload.get("percent"),
        start=payload.get("start_at"), end=payload.get("end_at"),
        days=payload.get("days"), name=str(payload.get("name") or "")[:255],
        move=bool(payload.get("move"))))
    now = datetime.now(timezone.utc)
    log.info("sale-events: user=%s created=%s listings=%d skipped=%d failed=%d",
             uid, row["ebay_promotion_id"] if row else "-", len(report.changed),
             len(report.skipped), len(report.failed))
    return {**_listing(uid, creds, now=now),
            "event": sale_events.public(row, now) if row else None,
            "report": report.as_dict()}


@router.patch("/api/sale-events/{event_id}")
def edit_sale_event(event_id: str, payload: dict, request: Request) -> dict:
    """Rename a sale or move its dates; change its discount before it starts."""
    uid = deps.signed_in_uid(request)
    creds = _creds(request)
    event = _guarded(lambda: sale_events.edit(
        uid, creds, event_id, name=payload.get("name"),
        start=payload.get("start_at"), end=payload.get("end_at"),
        percent=payload.get("percent")))
    return {**_listing(uid, creds), "event": event}


@router.post("/api/sale-events/{event_id}/listings")
def add_to_sale_event(event_id: str, payload: dict, request: Request) -> dict:
    """Put the ticked listings in an existing sale."""
    uid = deps.signed_in_uid(request)
    creds = _creds(request)
    records = _records(_ids(payload, "Pick at least one listing to add."), uid)
    event, report = _guarded(lambda: sale_events.add(
        uid, creds, event_id, records, move=bool(payload.get("move"))))
    return {**_listing(uid, creds), "event": event, "report": report.as_dict()}


@router.post("/api/sale-events/remove-listings")
def remove_from_sale_events(payload: dict, request: Request) -> dict:
    """Take the ticked listings out of whichever sales they are in."""
    uid = deps.signed_in_uid(request)
    creds = _creds(request)
    records = _records(_ids(payload, "Pick at least one listing to take out."), uid)
    report = _guarded(lambda: sale_events.remove(uid, creds, records))
    return {**_listing(uid, creds), "report": report.as_dict()}


@router.post("/api/sale-events/{event_id}/end")
def end_sale_event(event_id: str, request: Request) -> dict:
    """End a sale now — or cancel one that has not started."""
    uid = deps.signed_in_uid(request)
    creds = _creds(request)
    event = _guarded(lambda: sale_events.end(uid, creds, event_id))
    return {**_listing(uid, creds), "event": event}

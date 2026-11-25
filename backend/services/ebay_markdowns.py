"""eBay Marketing API — sale events (item price markdown promotions).

A sale event is eBay's own "sale": a percentage off a set of listings for a
set of days, shown to every buyer with the old price struck through. Seller
Hub calls it a sale event (in Discounts Manager, once Markdown Manager); the
API calls it an item price markdown. This module is the HTTP half and the
pure helpers around it; services/sale_events is the half that keeps the
app's mirror of them (db.SaleEvent) and decides what a seller may do.

The calls, all under /sell/marketing/v1 on the `sell.marketing` scope (which
config.EBAY_SCOPES has requested for a while — a seller who connected before
it was added reconnects once, and ScopeError is how they are told):

  GET    /promotion?promotion_type=MARKDOWN_SALE   every sale on the account
  GET    /item_price_markdown/{id}                 one sale, in full
  GET    /promotion/{id}/get_listing_set           the listings a sale covers
  POST   /item_price_markdown                      create one (id in Location)
  PUT    /item_price_markdown/{id}                 replace one, WHOLE
  DELETE /item_price_markdown/{id}                 delete one

Three facts about eBay's contract shape everything here:

  * A PUT REPLACES THE PROMOTION. There is no "add these listings" call; an
    update sends the whole thing back. Sent from a stale copy, it silently
    drops whatever the seller changed in Seller Hub since — so the caller
    re-reads the sale from eBay immediately before every write, and
    `patch_body` keeps every field it does not understand.
  * A SALE THIS APP CANNOT EXPRESS IS NOT REWRITTEN BY IT. Seller Hub can
    give different listings different discounts, take an amount off rather
    than a percentage, or pick listings by rule ("everything in this category
    over $20"). The app's own sales are one percentage over a list of
    listings, and `classify` marks anything else read-only rather than
    flattening it on the next edit.
  * ELIGIBILITY IS EBAY'S. A listing can be refused for being in another sale,
    for a recent price change, for its format, for a minimum advertised price.
    The refusal names the listings in its parameters, and MarkdownRefused
    carries them so the caller can drop them and try the rest.

Every limit below is a constant, marked VERIFY: eBay's developer pages were
not reachable when this was written, and the numbers come from Seller Hub's
public help and eBay's API reference as remembered. Each one is reported to
the browser by the server (/api/sale-events `limits`), so correcting one is a
change here and nowhere else.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import quote

import httpx

from .. import config
from .ebay import is_scope_error, rest_headers

log = logging.getLogger("thryft.markdowns")

_MARKETING = "/sell/marketing/v1"
PROMOTION_TYPE = "MARKDOWN_SALE"

# VERIFY — Seller Hub's sale events take 5% to 80% off.
MIN_PERCENT = 5
MAX_PERCENT = 80
# VERIFY — a sale event runs for at most 45 days.
MAX_DAYS = 45
# VERIFY — eBay takes at most 500 listing ids in one markdown request.
MAX_LISTINGS = 500
# VERIFY — the promotion name's length limit.
NAME_MAX = 90
# A sale "starting now" is scheduled this far ahead: eBay refuses a start
# date in the past, and the request takes a moment to arrive.
START_BUFFER = timedelta(minutes=2)
# How far ahead of eBay's end an early "End sale" sets the new end.
END_BUFFER = timedelta(minutes=2)
# Paging for the account-wide list and for one sale's listings.
PAGE = 200
LIST_MAX_PAGES = 5
LISTING_SET_MAX_PAGES = 10

# The statuses a sale can be in. An ENDED or INVALID one is history: shown,
# never edited.
LIVE_STATUSES = ("DRAFT", "SCHEDULED", "RUNNING", "PAUSED")
FINISHED_STATUSES = ("ENDED", "INVALID")

# eBay's refusals worth saying in the seller's words. The ones not here
# arrive as eBay's own message.
_REASONS: dict[int, str] = {}


class ScopeError(Exception):
    """The token can't use the Marketing API: the seller reconnects eBay."""


class NotFound(Exception):
    """eBay has no such promotion (deleted in Seller Hub, or another
    account's)."""


class MarkdownRefused(Exception):
    """eBay refused the request. `.listing_ids` names the listings eBay said
    were the problem, when it said, so the caller can drop them and retry."""

    def __init__(self, message: str, code: int = 0,
                 listing_ids: Optional[list[str]] = None):
        super().__init__(message)
        self.code = code
        self.listing_ids = list(listing_ids or [])


# ---------------------------------------------------------------- validation

def validate_percent(value) -> float:
    """A whole percentage within eBay's bounds, or ValueError a seller can act
    on. Checked on what was typed, never quietly rounded into range."""
    try:
        pct = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter how much off, as a percentage.") from exc
    if pct < MIN_PERCENT:
        raise ValueError(f"eBay's smallest sale is {MIN_PERCENT}% off.")
    if pct > MAX_PERCENT:
        raise ValueError(f"eBay's biggest sale is {MAX_PERCENT}% off.")
    if pct != int(pct):
        raise ValueError("Sales take a whole percentage — 15%, not 15.5%.")
    return float(int(pct))


def _utc(value) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("That date isn't one we can read.") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def validate_window(start=None, end=None, days=None,
                    now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """(start, end) for a new sale, in UTC — or ValueError.

    No start means "now" (a moment ahead, see START_BUFFER). The end is
    either given or `days` after the start. A start in the past is moved up
    to now rather than refused: a seller who opened the dialog five minutes
    ago and pressed Start meant now.
    """
    now = now or datetime.now(timezone.utc)
    begin = _utc(start)
    soonest = now + START_BUFFER
    if begin is None or begin < soonest:
        begin = soonest
    finish = _utc(end)
    if finish is None:
        try:
            n = float(days)
        except (TypeError, ValueError) as exc:
            raise ValueError("Say how long the sale runs.") from exc
        if n <= 0:
            raise ValueError("A sale has to run for at least a day.")
        finish = begin + timedelta(days=n)
    if finish <= begin:
        raise ValueError("The sale has to end after it starts.")
    if finish - begin > timedelta(days=MAX_DAYS, minutes=5):
        raise ValueError(f"eBay runs a sale for at most {MAX_DAYS} days.")
    return begin, finish


def _ebay_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ------------------------------------------------------------------ bodies

def build_body(*, name: str, percent: float, start: datetime, end: datetime,
               item_ids: Iterable[str], image_url: str = "",
               marketplace: Optional[str] = None) -> dict:
    """The createItemPriceMarkdownPromotion body for one of THIS app's sales:
    one percentage off one explicit list of listings, scheduled to run."""
    body = {
        "name": str(name)[:NAME_MAX],
        "marketplaceId": marketplace or config.EBAY_MARKETPLACE_ID,
        "startDate": _ebay_date(start),
        "endDate": _ebay_date(end),
        "promotionStatus": "SCHEDULED",
        # Free shipping is a separate decision with a cost the seller has not
        # been asked about.
        "applyFreeShipping": False,
        # A sale of THESE listings, not a rule that sweeps in the next ones.
        "autoSelectFutureInventory": False,
        "blockPriceIncreaseInItemRevision": False,
        "selectedInventoryDiscounts": [{
            "discountBenefit": {"percentageOffItem": f"{int(percent)}"},
            "inventoryCriterion": {
                "inventoryCriterionType": "INVENTORY_BY_VALUE",
                "listingIds": [str(i) for i in item_ids],
            },
            "ruleOrder": 1,
        }],
    }
    if image_url:
        # VERIFY — eBay shows this on the sale's promo; it must be an image
        # eBay can fetch (a listing's own eBay-hosted photo is).
        body["promotionImageUrl"] = image_url
    return body


# Fields eBay returns that a PUT must not send back.
_RESPONSE_ONLY = ("promotionId", "promotionHref", "priority", "warnings")


def patch_body(detail: dict, *, name: Optional[str] = None,
               start: Optional[datetime] = None, end: Optional[datetime] = None,
               percent: Optional[float] = None,
               item_ids: Optional[Iterable[str]] = None) -> dict:
    """The PUT body for a sale eBay currently holds as `detail`, with only the
    named changes made. Every field this does not know about is sent back as
    eBay gave it — a PUT replaces the whole promotion."""
    body = {k: v for k, v in (detail or {}).items() if k not in _RESPONSE_ONLY}
    if name is not None:
        body["name"] = str(name)[:NAME_MAX]
    if start is not None:
        body["startDate"] = _ebay_date(start)
    if end is not None:
        body["endDate"] = _ebay_date(end)
    tiers = [dict(t) for t in (body.get("selectedInventoryDiscounts") or [])]
    if (percent is not None or item_ids is not None) and len(tiers) != 1:
        raise ValueError("This sale has more than one discount level — edit it "
                         "in Seller Hub.")
    if tiers and percent is not None:
        tiers[0]["discountBenefit"] = {"percentageOffItem": f"{int(percent)}"}
    if tiers and item_ids is not None:
        crit = dict(tiers[0].get("inventoryCriterion") or {})
        crit["inventoryCriterionType"] = "INVENTORY_BY_VALUE"
        crit["listingIds"] = [str(i) for i in item_ids]
        crit.pop("inventoryItems", None)
        crit.pop("ruleCriteria", None)
        tiers[0]["inventoryCriterion"] = crit
    if tiers:
        body["selectedInventoryDiscounts"] = tiers
    return body


# --------------------------------------------------------------- classifying

@dataclass
class Classification:
    """What the app may do with one of eBay's sales."""
    editable: bool
    reason: str                      # "" when editable
    percent: Optional[float]         # the one percentage, when there is one
    item_ids: Optional[list[str]]    # explicit listings, None when by rule


def _percent_of(tier: dict) -> Optional[float]:
    benefit = tier.get("discountBenefit") or {}
    raw = benefit.get("percentageOffItem")
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def classify(detail: dict) -> Classification:
    """Is this a sale the app can edit, and if not, why not?

    Editable: ONE discount, a percentage off, over an explicit list of
    listings. Everything else is shown read-only with the reason — the app's
    edit path sends one percentage and one list, and applying it to a sale
    built any other way would silently flatten what the seller set up."""
    tiers = [t for t in (detail.get("selectedInventoryDiscounts") or [])
             if isinstance(t, dict)]
    status = str(detail.get("promotionStatus") or "").upper()
    percent = _percent_of(tiers[0]) if len(tiers) == 1 else None
    # Which listings it covers, when every tier names them outright — a
    # tiered sale is read-only here but still holds its listings, and the
    # one-sale-per-listing rule has to see them.
    item_ids: Optional[list[str]] = None
    crits = [t.get("inventoryCriterion") or {} for t in tiers]
    if crits and all(c.get("inventoryCriterionType") == "INVENTORY_BY_VALUE"
                     and c.get("listingIds") for c in crits):
        item_ids = list(dict.fromkeys(
            str(i) for c in crits for i in c.get("listingIds") or []))

    def ro(reason: str) -> Classification:
        return Classification(False, reason, percent, item_ids)

    if not tiers:
        return ro("Its discount isn't one we can read")
    if len(tiers) > 1:
        return ro("Different discounts for different listings")
    benefit = tiers[0].get("discountBenefit") or {}
    if percent is None:
        return ro("An amount off, not a percentage" if benefit.get("amountOffItem")
                  else "Its discount isn't one we can read")
    crit = tiers[0].get("inventoryCriterion") or {}
    kind = crit.get("inventoryCriterionType")
    if kind in ("INVENTORY_BY_RULE", "INVENTORY_ANY") or detail.get("autoSelectFutureInventory"):
        return ro("Picks its listings by rule")
    if kind != "INVENTORY_BY_VALUE" or not crit.get("listingIds"):
        return ro("Picks its listings by SKU" if crit.get("inventoryItems")
                  else "Its listings aren't ones we can read")
    if status in FINISHED_STATUSES:
        return ro("This sale has ended")
    return Classification(True, "", percent, item_ids)


# ------------------------------------------------------------------- HTTP

def _headers(token: str) -> dict:
    return {**rest_headers(token),
            "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID}


def _token(creds: Optional[dict]) -> str:
    token = (creds or {}).get("access_token")
    if not token:
        raise ScopeError()
    return token


_LISTING_ID = re.compile(r"\b\d{9,15}\b")


def _refusal(resp: httpx.Response, sent_ids: Iterable[str] = ()) -> MarkdownRefused:
    """MarkdownRefused from eBay's error envelope, naming the listings eBay
    named — in a parameter, or in the sentence — that were in the request."""
    sent = {str(i) for i in sent_ids}
    try:
        errs = (resp.json() or {}).get("errors") or []
    except (ValueError, json.JSONDecodeError):
        errs = []
    code, message, named = 0, "", []
    for err in errs:
        if not isinstance(err, dict):
            continue
        if not message:
            try:
                code = int(err.get("errorId") or 0)
            except (TypeError, ValueError):
                code = 0
            message = str(err.get("longMessage") or err.get("message") or "")
        texts = [str(err.get("message") or ""), str(err.get("longMessage") or "")]
        for p in err.get("parameters") or []:
            if isinstance(p, dict):
                texts.append(str(p.get("value") or ""))
        for t in texts:
            for found in _LISTING_ID.findall(t):
                if found in sent and found not in named:
                    named.append(found)
    return MarkdownRefused(
        _REASONS.get(code) or message or f"eBay refused the sale ({resp.status_code}).",
        code, named)


def _check(resp: httpx.Response, sent_ids: Iterable[str] = ()) -> None:
    if resp.status_code < 300:
        return
    if resp.status_code == 404:
        raise NotFound()
    if is_scope_error(resp):
        raise ScopeError()
    raise _refusal(resp, sent_ids)


def _client(client: Optional[httpx.Client]) -> tuple[httpx.Client, bool]:
    return (client, False) if client is not None else (httpx.Client(timeout=30), True)


def list_markdowns(creds: Optional[dict], client: Optional[httpx.Client] = None
                   ) -> list[dict]:
    """Every markdown sale on the account (eBay's summaries), all statuses,
    up to LIST_MAX_PAGES pages."""
    token = _token(creds)
    http, owned = _client(client)
    out: list[dict] = []
    try:
        for page in range(LIST_MAX_PAGES):
            r = http.get(f"{config.EBAY_API_BASE}{_MARKETING}/promotion",
                         headers=_headers(token),
                         params={"marketplace_id": config.EBAY_MARKETPLACE_ID,
                                 "promotion_type": PROMOTION_TYPE,
                                 "limit": str(PAGE), "offset": str(page * PAGE)})
            if r.status_code == 204:
                break
            _check(r)
            body = r.json() or {}
            batch = [p for p in body.get("promotions") or [] if isinstance(p, dict)]
            out.extend(batch)
            if len(batch) < PAGE or not body.get("next"):
                break
        return out
    finally:
        if owned:
            http.close()


def get_markdown(creds: Optional[dict], promotion_id: str,
                 client: Optional[httpx.Client] = None) -> dict:
    """One sale, in full — the body a PUT has to send back."""
    token = _token(creds)
    http, owned = _client(client)
    try:
        r = http.get(f"{config.EBAY_API_BASE}{_MARKETING}/item_price_markdown/"
                     f"{quote(str(promotion_id), safe='')}",
                     headers=_headers(token))
        _check(r)
        return r.json() or {}
    finally:
        if owned:
            http.close()


def listing_set(creds: Optional[dict], promotion_id: str,
                client: Optional[httpx.Client] = None) -> list[str]:
    """The listing (item) ids a sale covers, as eBay resolves them — the only
    way to know which listings a rule-based Seller Hub sale reached."""
    token = _token(creds)
    http, owned = _client(client)
    ids: list[str] = []
    try:
        for page in range(LISTING_SET_MAX_PAGES):
            r = http.get(f"{config.EBAY_API_BASE}{_MARKETING}/promotion/"
                         f"{quote(str(promotion_id), safe='')}/get_listing_set",
                         headers=_headers(token),
                         params={"limit": str(PAGE), "offset": str(page * PAGE)})
            if r.status_code == 204:
                break
            _check(r)
            body = r.json() or {}
            batch = [str(x.get("listingId")) for x in body.get("listings") or []
                     if isinstance(x, dict) and x.get("listingId")]
            ids.extend(batch)
            if len(batch) < PAGE or not body.get("next"):
                break
        return list(dict.fromkeys(ids))
    finally:
        if owned:
            http.close()


def _id_from(resp: httpx.Response) -> str:
    loc = resp.headers.get("Location") or resp.headers.get("location") or ""
    if loc:
        return loc.rstrip("/").rsplit("/", 1)[-1]
    try:
        return str((resp.json() or {}).get("promotionId") or "")
    except (ValueError, json.JSONDecodeError):
        return ""


def create(creds: Optional[dict], body: dict,
           client: Optional[httpx.Client] = None) -> str:
    """Create a sale; returns eBay's promotion id."""
    token = _token(creds)
    sent = _ids_in(body)
    http, owned = _client(client)
    try:
        r = http.post(f"{config.EBAY_API_BASE}{_MARKETING}/item_price_markdown",
                      headers=_headers(token), json=body)
        _check(r, sent)
        pid = _id_from(r)
        if not pid:
            raise MarkdownRefused("eBay made the sale but didn't say which it is — "
                                  "refresh Sales to find it.")
        return pid
    finally:
        if owned:
            http.close()


def update(creds: Optional[dict], promotion_id: str, body: dict,
           client: Optional[httpx.Client] = None) -> None:
    """Replace a sale with `body` (see patch_body)."""
    token = _token(creds)
    sent = _ids_in(body)
    http, owned = _client(client)
    try:
        r = http.put(f"{config.EBAY_API_BASE}{_MARKETING}/item_price_markdown/"
                     f"{quote(str(promotion_id), safe='')}",
                     headers=_headers(token), json=body)
        _check(r, sent)
    finally:
        if owned:
            http.close()


def delete(creds: Optional[dict], promotion_id: str,
           client: Optional[httpx.Client] = None) -> None:
    """Delete a sale. VERIFY — eBay may only allow this before it starts; a
    running sale is ended by moving its end date instead (sale_events.end)."""
    token = _token(creds)
    http, owned = _client(client)
    try:
        r = http.delete(f"{config.EBAY_API_BASE}{_MARKETING}/item_price_markdown/"
                        f"{quote(str(promotion_id), safe='')}",
                        headers=_headers(token))
        _check(r)
    finally:
        if owned:
            http.close()


def _ids_in(body: dict) -> list[str]:
    out: list[str] = []
    for tier in body.get("selectedInventoryDiscounts") or []:
        crit = (tier or {}).get("inventoryCriterion") or {}
        out.extend(str(i) for i in crit.get("listingIds") or [])
    return out


def seller_hub_url(promotion_id: str = "") -> str:
    """Where the seller manages a sale in Seller Hub. VERIFY the deep link;
    the Discounts page is the safe landing either way."""
    host = "www.ebay.com" if config.EBAY_ENV == "production" else "www.sandbox.ebay.com"
    return f"https://{host}/sh/mkt/promotions"


def limits() -> dict:
    """The bounds the browser shows and checks before it asks — reported by
    the server so these constants are the only copy."""
    return {"min_percent": MIN_PERCENT, "max_percent": MAX_PERCENT,
            "max_days": MAX_DAYS, "max_listings": MAX_LISTINGS,
            "name_max": NAME_MAX}

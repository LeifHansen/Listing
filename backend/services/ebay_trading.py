"""eBay Trading API (XML) — the only API that sees a seller's WHOLE store.

The Sell Inventory API used everywhere else only knows about listings created
through it (i.e. ones this app published). Listings a seller made on eBay
directly, in Seller Hub, or with another tool are invisible to it. The Trading
API is eBay's legacy-but-fully-supported route that does see them, so it backs
bi-directional sync:

  - GetMyeBaySelling  -> enumerate every active listing on the account
  - GetItem           -> the full detail of one listing (specifics, description,
                         photos, shipping) so it can be edited in this app
  - ReviseItem        -> push an edit back to a listing we didn't create
  - EndItem           -> end one of those listings

It authenticates with the SAME user OAuth token as the REST APIs (passed in the
X-EBAY-API-IAF-TOKEN header), so a connected seller needs no extra setup and no
legacy Auth'n'Auth token or DevName/AppName/CertName header trio is involved.

Everything here returns plain dicts and raises TradingError with a user-facing
message on failure — no XML leaks past this module.
"""
from __future__ import annotations

import hashlib
import html
import math
import re
from typing import Any, Optional
from xml.etree import ElementTree as ET

import httpx

from .. import config
from ..config import log
from ..models import (SUBTITLE_MAX_CHARS, TITLE_MAX_CHARS, ItemSpecific,
                      Listing)
from . import taxonomy

# Trading API's XML namespace — every element in a response carries it.
_NS = "urn:ebay:apis:eBLBaseComponents"
_TAG = re.compile(r"^\{.*\}")  # strips the namespace from a tag name
# Compatibility level this client's request/response shapes were written for.
_COMPAT_LEVEL = "1227"
_TIMEOUT = 45


class TradingError(ValueError):
    """A Trading API call failed — carries a user-facing reason.

    `code` is eBay's own ErrorCode when the failure came back as a rejection
    rather than a transport problem, so callers can branch on a specific
    condition (see AlreadyListedError) instead of matching on message text.

    `detail` is everything else eBay said about the failure: the response's
    own <Message> element plus any errors after the first and any warnings.
    It rides along separately from `message` so callers can show the seller a
    clean headline and still have the specifics to act on.

    `said` is the response's <Message> element ON ITS OWN — where eBay puts
    the ACTUAL reason behind a catch-all rejection (see `_failure`). It is
    kept apart from `detail` because only <Message> is authoritative about
    WHY: `detail` also carries warnings and trailing errors, which are
    context. A caller that reads `detail` as eBay's reason will quote a
    warning as the cause of a rejection it had nothing to do with.

    `codes` is EVERY ErrorCode in the response, not just the first. eBay
    returns them as a list and the order is its own business: a request that
    is refused for two reasons can put either first. Asking "was this a 240?"
    of `code` alone therefore answers "no" for a response that plainly
    contains one — which is how a diagnosis that turns on that question ends
    up abandoning a listing eBay had already explained.
    """

    def __init__(self, message: str, code: str = "", detail: str = "",
                 said: str = "", codes: Optional[list[str]] = None):
        super().__init__(message)
        self.code = code
        self.detail = detail
        self.said = said
        self.codes = list(codes) if codes else ([code] if code else [])

    def has_code(self, wanted: str) -> bool:
        """Did eBay return this ErrorCode anywhere in the response?"""
        return wanted in self.codes


# eBay's seller-level call-limit error. Documented (developer.ebay.com KB
# 2137): returned in the response body with Ack=Failure, NOT as an HTTP
# status, when a seller's requests in the window reach the limit. The limits
# are per SELLER and windowed -- 5000 Add-listing calls / 30s, 1200 Revise /
# 30s -- so one busy store reaches them, not just a busy application.
_RATE_LIMIT_CODES = {"21919144"}

# The application-level daily quota is a separate refusal, and this repository
# cannot cite its numeric code with confidence -- so it is matched on eBay's
# published wording instead of a code invented to look authoritative. Narrow
# on purpose: it must not swallow a listing eBay refused on its merits, which
# is a seller-fixable problem and not something waiting will cure.
_RATE_LIMIT_PHRASES = (
    "exceeded usage limit",
    "exceeded your maximum call limit",
    "call limit exceeded",
)

# "…Try again after 5 seconds." — eBay answering the only question that
# matters here, in the body rather than a header.
_TRY_AGAIN_RE = re.compile(r"try again (?:in|after)\s+(\d+)\s*second",
                           re.IGNORECASE)


class RateLimited(TradingError):
    """eBay is refusing because of a call limit, not because of the request.

    Kept apart from every other TradingError because the right response is the
    opposite one. An ordinary rejection means this listing needs fixing and
    the next listing is fine; a rate limit means nothing was wrong with the
    listing and the NEXT call is the problem. Counting these as per-listing
    failures told sellers eBay had rejected listings it never looked at, and
    carrying on fired hundreds more calls into a windowed limit -- which does
    not merely fail, it holds the window open and lengthens the wait.

    `retry_after` is eBay's own answer in seconds, from the Retry-After header
    or from its message, and it is None when neither said. None rather than a
    default: a number here is a promise about when eBay will answer, and this
    client is not in a position to invent one.
    """

    def __init__(self, message: str, code: str = "", detail: str = "",
                 retry_after: Optional[int] = None):
        super().__init__(message, code=code, detail=detail)
        self.retry_after = retry_after


def _retry_after_seconds(value: str) -> Optional[int]:
    """Seconds from a Retry-After header, or None.

    HTTP allows an HTTP-date as well as a delta in seconds. A date is not
    parsed into a wait here: it would need clock-skew handling to be worth
    anything, and "we do not know" is an honest answer that the caller
    already has to handle.
    """
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _rate_limit_wait(text: str) -> Optional[int]:
    match = _TRY_AGAIN_RE.search(text or "")
    return int(match.group(1)) if match else None


def _is_rate_limit(codes: list[str], text: str) -> bool:
    if any(code in _RATE_LIMIT_CODES for code in codes):
        return True
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in _RATE_LIMIT_PHRASES)


class AlreadyListedError(TradingError):
    """eBay refused a create because THIS publish already produced a listing.

    Raised when the idempotency key on AddItem/AddFixedPriceItem (see
    `create_listing`) collides with one eBay has already seen — the signal that
    a retried or racing publish is about to mint a duplicate. `item_id` is the
    listing that already exists when eBay names it, so the caller can adopt it
    instead of creating a second one.
    """

    def __init__(self, message: str, code: str = "", item_id: str = "",
                 detail: str = ""):
        super().__init__(message, code, detail)
        self.item_id = item_id


def _endpoint() -> str:
    return ("https://api.sandbox.ebay.com/ws/api.dll" if config.EBAY_ENV != "production"
            else "https://api.ebay.com/ws/api.dll")


def _site_id() -> str:
    # 0 = eBay US. The marketplace id is the modern equivalent; map the common
    # ones and default to US.
    return {"EBAY_US": "0", "EBAY_GB": "3", "EBAY_DE": "77",
            "EBAY_AU": "15", "EBAY_CA": "2"}.get(config.EBAY_MARKETPLACE_ID, "0")


def _headers(call: str, token: str) -> dict[str, str]:
    return {
        "X-EBAY-API-COMPATIBILITY-LEVEL": _COMPAT_LEVEL,
        "X-EBAY-API-CALL-NAME": call,
        "X-EBAY-API-SITEID": _site_id(),
        # OAuth user token — the Trading API accepts the same token the REST
        # APIs use through this header (no legacy Auth'n'Auth token needed).
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _cdata(value: str) -> str:
    """Wrap text in CDATA so listing HTML survives intact.

    A literal "]]>" inside the text would otherwise close the section early and
    make the rest of the description parse as markup — eBay rejects the call at
    best, and at worst the description injects elements into the request. The
    standard fix is to split the sequence across two CDATA sections."""
    return "<![CDATA[" + str(value or "").replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _call(call: str, token: str, body: str) -> ET.Element:
    """POST one Trading API call and return the parsed response root."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<{call}Request xmlns="{_NS}">'
        # No RequesterCredentials element: the IAF header carries the token, and
        # sending an empty credentials block makes eBay reject the call.
        "<ErrorLanguage>en_US</ErrorLanguage><WarningLevel>High</WarningLevel>"
        f"{body}"
        f"</{call}Request>"
    )
    try:
        resp = httpx.post(_endpoint(), headers=_headers(call, token),
                          content=xml.encode("utf-8"), timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - network/timeout
        raise TradingError(f"Couldn't reach eBay: {exc}") from exc
    if resp.status_code == 429:
        # The transport refusing before eBay's XML is ever produced. Same
        # condition, different layer.
        raise RateLimited(
            "eBay is limiting how often we can talk to your account right "
            "now. Nothing was changed — this will pick up again shortly.",
            retry_after=_retry_after_seconds(
                resp.headers.get("Retry-After", "")))
    if resp.status_code != 200:
        raise TradingError(f"eBay returned {resp.status_code} for {call}.")
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise TradingError(f"eBay sent an unreadable response for {call}.") from exc
    graded = [((_text(e, "SeverityCode") or "").lower(), e)
              for e in _findall(root, "Errors")]
    errors = [e for sev, e in graded if sev == "error"]
    # Warnings used to be dropped here without even a log line, on failures
    # AND on successes. On a rejection they are sometimes the only place the
    # cause is named; on an acceptance they are how eBay says it changed
    # something — above all that it REMAPPED the category (eBay retires
    # categories and moves the listing itself when CategoryMappingAllowed is
    # true, which every publish here sets). Silently, the app then held an id
    # eBay had already replaced.
    warnings = [e for sev, e in graded if sev != "error"]
    ack = (_text(root, "Ack") or "").lower()
    if ack in ("failure", "partialfailure") and errors:
        raise _failure(call, root, errors, warnings)
    if warnings:
        log.info("trading: %s ok with %d warning(s): %s", call, len(warnings),
                 " | ".join(_error_line(w) for w in warnings)[:400])
    return root


def _error_line(err: ET.Element) -> str:
    """One <Errors> entry as a single readable line, code included."""
    text = (_text(err, "LongMessage") or _text(err, "ShortMessage") or "").strip()
    code = _text(err, "ErrorCode")
    return f"{text} (eBay error {code})" if code and text else text or f"eBay error {code}"


# eBay's catch-all rejections: the LongMessage lists every reason the code can
# mean and names none of them, so on its own it sends the seller looking at
# fields that were never the problem. eBay puts the real one in the response's
# <Message> element (Trading's AddItemResponse.Message, "returned when the
# item is not listed"), which this client used to drop on the floor.
_CATCH_ALL_CODES = {"240"}


def _failure(call: str, root: ET.Element, errors: list[ET.Element],
             warnings: Optional[list[ET.Element]] = None) -> TradingError:
    """Build the TradingError for a failed call, keeping everything eBay said.

    Only the first error became the message before this, and the response-level
    <Message> was never read at all — which is exactly the detail eBay attaches
    to error 240 ("The item cannot be listed or modified..."). A seller then saw
    a sentence listing four possible causes and no way to tell which was theirs.
    """
    first = errors[0]
    code = _text(first, "ErrorCode") or ""
    headline = (_text(first, "LongMessage") or _text(first, "ShortMessage")
                or "eBay rejected the request.").strip()
    # eBay's own detail for this rejection: the response-level <Message>, kept
    # apart from everything else because it is the ONLY part authoritative
    # enough to speak for a catch-all code (see the promotion below).
    said = _text(root, "Message").strip()
    extras = [said] + [_error_line(e) for e in errors[1:]]
    extras += [_error_line(e) for e in (warnings or [])]
    detail = " ".join(x for x in extras if x)[:600]
    # Always logged in full: a rejection the app can't explain is the one thing
    # a seller can't debug from the UI, and the fly logs are where it has to be.
    log.warning("trading: %s rejected — code=%s ack-errors=%d warnings=%d "
                "msg=%s detail=%s", call, code or "?", len(errors),
                len(warnings or []), headline[:200], detail[:300] or "(none)")
    all_codes = [c for c in (_text(e, "ErrorCode") or "" for e in errors) if c]
    # Checked before the branches below, because a call limit is not a
    # rejection of the request: nothing about the listing needs fixing, and
    # the next call is the problem. Telling the seller to reconnect eBay or to
    # correct a field would send them after something that is not there.
    if _is_rate_limit(all_codes, f"{headline} {detail}"):
        return RateLimited(
            "eBay is limiting how often we can talk to your account right "
            "now. Nothing was changed — this will pick up again shortly.",
            code=code, detail=detail,
            retry_after=_rate_limit_wait(f"{headline} {detail}"))
    if code in ("931", "932", "16110", "21917053"):  # auth/token codes
        return TradingError(
            "eBay didn't accept the account connection — reconnect eBay "
            "in Settings and try again.", code=code, detail=detail, said=said,
            codes=all_codes)
    if code in _CATCH_ALL_CODES and said and len(said) <= 300:
        # eBay named the real reason — lead with it instead of the catch-all.
        # Only <Message> earns this: a trailing warning or a second error is
        # context, and promoting one of those to the headline would put words
        # in eBay's mouth about why the listing was refused.
        headline = said
    return TradingError(headline[:300], code=code, detail=detail, said=said,
                        codes=all_codes)


# --- tiny XML helpers (namespace-agnostic) ----------------------------------

def _name(el: ET.Element) -> str:
    return _TAG.sub("", el.tag)


def _find(parent: ET.Element, path: str) -> Optional[ET.Element]:
    """Follow a '/'-separated path of local (namespace-stripped) tag names,
    one child level per segment, and return the element it lands on."""
    node: Optional[ET.Element] = parent
    for part in path.split("/"):
        if node is None:
            return None
        node = next((c for c in node if _name(c) == part), None)
    return node


def _findall(parent: ET.Element, path: str) -> list[ET.Element]:
    """Like _find, but returns EVERY child matching the last path segment."""
    *head, last = path.split("/")
    node: Optional[ET.Element] = parent
    for part in head:
        if node is None:
            return []
        node = next((c for c in node if _name(c) == part), None)
    if node is None:
        return []
    return [c for c in node if _name(c) == last]


def _text(parent: Optional[ET.Element], path: str, default: str = "") -> str:
    if parent is None:
        return default
    el = _find(parent, path)
    return (el.text or "").strip() if el is not None and el.text else default


def _float(parent: Optional[ET.Element], path: str) -> Optional[float]:
    raw = _text(parent, path)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _int(parent: Optional[ET.Element], path: str, default: int = 0) -> int:
    raw = _text(parent, path)
    try:
        return int(float(raw)) if raw else default
    except ValueError:
        return default


# --- condition mapping ------------------------------------------------------
# Trading's numeric ConditionID <-> the Inventory API's condition enum this app
# stores on Listing.condition.
# id -> enum comes from taxonomy, which is the one place that direction is
# defined. A local copy here disagreed on 2750: import read it as
# USED_EXCELLENT while _CONDITION_TO_ID sends LIKE_NEW back as 2750, so every
# revise of an imported "Like New" listing silently downgraded it to 3000.
_CONDITION_BY_ID = taxonomy.CONDITION_ID_TO_ENUM
# enum -> id is NOT the inverse: several ids share an enum (2000/2010,
# 2020/2030/2500), so this names the one id to publish for each.
_CONDITION_TO_ID = {
    "NEW": "1000", "NEW_OTHER": "1500", "NEW_WITH_DEFECTS": "1750",
    "CERTIFIED_REFURBISHED": "2000", "SELLER_REFURBISHED": "2500",
    # 2750 (Like New) exists in some categories (media, trading cards): the
    # taxonomy layer can offer LIKE_NEW, so the publish map must speak it too
    # — a missing key silently dropped <ConditionID>, and eBay then rejected
    # with "condition is required" (architect finding #5).
    "LIKE_NEW": "2750",
    "USED_EXCELLENT": "3000", "USED_VERY_GOOD": "4000", "USED_GOOD": "5000",
    "USED_ACCEPTABLE": "6000", "FOR_PARTS_OR_NOT_WORKING": "7000",
}


def _listing_format(listing_type: str, has_bin: bool) -> str:
    lt = (listing_type or "").lower()
    if lt.startswith("chinese") or lt == "auction":
        return "AUCTION_BIN" if has_bin else "AUCTION"
    return "FIXED_PRICE"


def _quantity_sold(selling: Optional[ET.Element]) -> int:
    """Units already sold on this listing (0 when eBay didn't say)."""
    return _int(selling, "QuantitySold") if selling is not None else 0


def _item_to_listing(item: ET.Element) -> dict:
    """Map one Trading API <Item> to this app's Listing shape (as a dict)."""
    selling = _find(item, "SellingStatus")
    listing_type = _text(item, "ListingType")
    bin_price = _float(item, "BuyItNowPrice")
    start_price = _float(item, "StartPrice")
    current = _float(selling, "CurrentPrice") if selling is not None else None
    fmt = _listing_format(listing_type, bool(bin_price))

    if fmt == "FIXED_PRICE":
        price = start_price if start_price is not None else current
        auction_start = None
    else:
        price = bin_price
        auction_start = start_price

    specifics: list[dict] = []
    for nvl in _findall(item, "ItemSpecifics/NameValueList"):
        name = _text(nvl, "Name")
        values = [(v.text or "").strip() for v in nvl
                  if _name(v) == "Value" and v.text]
        for value in values:
            if name and value:
                specifics.append({"name": name[:40], "value": value[:65]})

    pictures = [(u.text or "").strip() for u in
                _findall(item, "PictureDetails/PictureURL") if u.text]

    dims = _find(item, "ShippingPackageDetails")
    weight_major = _int(dims, "WeightMajor") if dims is not None else 0
    weight_minor = _float(dims, "WeightMinor") if dims is not None else None

    return {
        "title": _text(item, "Title")[:TITLE_MAX_CHARS],
        "subtitle": _text(item, "SubTitle"),
        "brand": next((s["value"] for s in specifics
                       if s["name"].strip().lower() == "brand"), ""),
        "condition": _CONDITION_BY_ID.get(_text(item, "ConditionID"), "USED_EXCELLENT"),
        "condition_description": _text(item, "ConditionDescription"),
        "category_id": _text(item, "PrimaryCategory/CategoryID"),
        "category_suggestion": _text(item, "PrimaryCategory/CategoryName"),
        "description": _text(item, "Description"),
        "price": round(price, 2) if price is not None else None,
        "currency": _text(item, "Currency") or config.EBAY_CURRENCY,
        # What is still BUYABLE, which is not what eBay puts in Item.Quantity.
        #
        # GetItem reports Item.Quantity as the quantity the listing was
        # created with and SellingStatus.QuantitySold as how many of those
        # have gone; the remainder is the difference. Importing Item.Quantity
        # directly overstates stock by exactly the number already sold — and
        # because ReviseFixedPriceItem reads the Quantity it is sent as the
        # new available stock, the next edit would put the sold units back on
        # sale. max(1, ...) compounded it by making a sold-out listing import
        # as "1 available", so the one listing with nothing left to sell was
        # the one offering a unit that does not exist.
        #
        # Zero is a real state here (eBay's out-of-stock control), so the
        # floor is 0, not 1. Clamped because eBay does report sold greater
        # than quantity on some variation/out-of-stock listings.
        # https://developer.ebay.com/devzone/xml/docs/reference/ebay/getitem.html
        "quantity": max(0, _int(item, "Quantity", 0) - _quantity_sold(selling)),
        "listing_format": fmt,
        "auction_start_price": round(auction_start, 2) if auction_start else None,
        "package_weight_lb": float(weight_major or 0),
        "package_weight_oz": round(weight_minor or 0.0, 1),
        # `_float`, not `float(_int(...))`. These are eBay MeasureType
        # (decimal) fields, and `_int` runs them through int() first -- so a
        # package eBay reported as 10.5 inches was read back as 10 and, on the
        # seller's next edit, sent back to eBay a shrunken box. Read what eBay
        # said; the ROUNDING belongs at the emit, where eBay's "whole number of
        # inches" rule applies (see _whole_inches).
        "package_length_in": _float(dims, "PackageLength") or 0.0,
        "package_width_in": _float(dims, "PackageWidth") or 0.0,
        "package_height_in": _float(dims, "PackageDepth") or 0.0,
        "item_specifics": specifics,
        "images": [],           # no local files — this listing came from eBay
        "image_urls": pictures,  # eBay-hosted photos, shown as-is
        "ebay_listing_id": _text(item, "ItemID"),
        # eBay's Variations container. Nothing here ever looked for it, so a
        # multi-variation listing imported as ONE flat record -- a single
        # price (eBay reports the lowest variation's), a single item-level
        # quantity, and no sign the other sizes exist. Knowing is the whole
        # point: it is what stops the revise below rewriting a structure this
        # app cannot see.
        "has_variations": _find(item, "Variations") is not None,
        "sku": _text(item, "SKU"),
        "source": "ebay",
        "watch_count": _int(item, "WatchCount"),
        "sold_quantity": _quantity_sold(selling),
        "view_url": _text(item, "ListingDetails/ViewItemURL"),
        # When the listing actually went live on eBay. The only true recency
        # signal an imported listing has — without it every listing looks as
        # new as the sync that pulled it in.
        "ebay_start_time": _text(item, "ListingDetails/StartTime"),
    }


# --- public API -------------------------------------------------------------

# GetMyeBaySelling accepts up to 200 entries per page; 100 keeps each response
# small enough to parse quickly. _MAX_PAGES bounds the walk so a pathological
# account can never spin here forever.
_PAGE_SIZE = 100
_MAX_PAGES = 25


def _my_ebay_pages(token: str, container: str, sort: str, max_pages: int):
    """Yield one GetMyeBaySelling container element per page, following eBay's
    own PaginationResult. Callers `break` out early when they have enough."""
    page = 1
    while page <= max_pages:
        body = (
            f"<{container}><Include>true</Include>"
            f"<Pagination><EntriesPerPage>{_PAGE_SIZE}</EntriesPerPage>"
            f"<PageNumber>{page}</PageNumber></Pagination>"
            + (f"<Sort>{sort}</Sort>" if sort else "")
            + f"</{container}>"
            "<DetailLevel>ReturnAll</DetailLevel>"
        )
        root = _call("GetMyeBaySelling", token, body)
        cont = _find(root, container)
        if cont is None:
            return
        yield cont
        total_pages = _int(cont, "PaginationResult/TotalNumberOfPages", 1)
        if page >= max(1, total_pages):
            return
        page += 1


def _my_ebay_ids(token: str, container: str, sort: str,
                 limit: Optional[int], max_pages: int) -> list[str]:
    """Item ids from one GetMyeBaySelling container (ActiveList / UnsoldList /
    SoldList), page by page. SoldList nests items inside order transactions
    rather than a flat ItemArray, so ids are swept from every ItemID element
    under the container (deduped, order kept)."""
    ids: list[str] = []
    seen: set[str] = set()
    for cont in _my_ebay_pages(token, container, sort, max_pages):
        # EXACT tag match: a suffix test also catches <OrderLineItemID>, whose
        # value is "<itemId>-<transactionId>". Those aren't item ids, and eBay
        # rejected every one of them ("Input data for tag <ItemID> is invalid")
        # — 50 bogus GetItem calls per sold-list sync.
        page_ids = [el.text.strip() for el in cont.iter()
                    if _name(el) == "ItemID" and el.text and el.text.strip()]
        for i in page_ids:  # dedupe as we go — a multi-qty sale repeats its id
            if i not in seen:
                seen.add(i)
                ids.append(i)
        if not page_ids or (limit is not None and len(ids) >= limit):
            break
    return ids[:limit] if limit is not None else ids


def active_listing_ids(token: str, limit: Optional[int] = None,
                       max_pages: int = _MAX_PAGES) -> list[str]:
    """Every ACTIVE listing id on the connected account, oldest page first.

    GetMyeBaySelling's ActiveList is the authoritative "what's live in my
    store" view — it includes listings created anywhere, not just by us.
    `limit` stops the walk as soon as that many ids are in hand, so a caller
    that only wants the first N doesn't pay for pages it will discard."""
    return _my_ebay_ids(token, "ActiveList", "TimeLeft", limit, max_pages)


def unsold_listing_ids(token: str, limit: Optional[int] = None,
                       max_pages: int = _MAX_PAGES) -> list[str]:
    """Listings that ENDED WITHOUT SELLING (eBay keeps ~90 days of these) —
    the store's inactive pile, importable so it mirrors here too."""
    return _my_ebay_ids(token, "UnsoldList", "", limit, max_pages)


def sold_sales(token: str, limit: Optional[int] = None,
               max_pages: int = _MAX_PAGES) -> dict[str, dict]:
    """What the recently-sold listings ACTUALLY brought in, keyed by item id:
    {item_id: {"price", "currency", "quantity", "sold_at"}}.

    The transaction is the only place the real money appears. An accepted Best
    Offer settles below the asking price and eBay never moves the item's own
    price to match, so a sold record built from GetItem alone reports what the
    seller ASKED — which is how a $76.50 sale showed up in the app as $89.99.
    SoldList nests one <Transaction> per sale under OrderTransactionArray, and
    TransactionPrice is the per-unit amount the buyer actually paid.

    Several transactions can share an item id (a multi-quantity listing sold
    in separate orders): quantities add up, and the most recent transaction
    owns the headline price and date.
    """
    out: dict[str, dict] = {}
    for cont in _my_ebay_pages(token, "SoldList", "", max_pages):
        found = 0
        for tx in cont.iter():
            if _name(tx) != "Transaction":
                continue
            item_id = _text(tx, "Item/ItemID")
            if not item_id:
                continue
            found += 1
            price_el = _find(tx, "TransactionPrice")
            price = _float(tx, "TransactionPrice")
            currency = (price_el.get("currencyID") or "") if price_el is not None else ""
            qty = max(1, _int(tx, "QuantityPurchased", 1))
            # CreatedDate is when the sale happened; PaidTime only exists once
            # the buyer has paid, so it can't be the primary signal.
            sold_at = _text(tx, "CreatedDate") or _text(tx, "PaidTime")
            prior = out.get(item_id)
            if prior is None:
                out[item_id] = {"price": price, "currency": currency,
                                "quantity": qty, "sold_at": sold_at}
                continue
            prior["quantity"] += qty
            # eBay's timestamps are ISO-8601 UTC ("2026-08-12T18:04:11.000Z"),
            # so a plain string compare orders them correctly.
            if sold_at and sold_at > (prior.get("sold_at") or ""):
                prior["sold_at"] = sold_at
                if price is not None:
                    prior["price"] = price
                    prior["currency"] = currency
        if not found or (limit is not None and len(out) >= limit):
            break
    return out


def watch_counts(token: str, max_pages: int = _MAX_PAGES) -> dict[str, int]:
    """{item_id: watch count} for every active listing on the account. Backs
    the metrics overlay — the Sell APIs don't expose watchers, and routing the
    call through here keeps the endpoint env-aware (sandbox vs production)
    with the shared error handling."""
    out: dict[str, int] = {}
    page = 1
    while page <= max_pages:
        body = (
            "<ActiveList><Include>true</Include>"
            f"<Pagination><EntriesPerPage>{_PAGE_SIZE}</EntriesPerPage>"
            f"<PageNumber>{page}</PageNumber></Pagination></ActiveList>"
            "<DetailLevel>ReturnAll</DetailLevel>"
        )
        root = _call("GetMyeBaySelling", token, body)
        cont = _find(root, "ActiveList")
        if cont is None:
            break
        items = _findall(cont, "ItemArray/Item")
        for item in items:
            iid = _text(item, "ItemID")
            if iid:
                out[iid] = _int(item, "WatchCount")
        total_pages = _int(cont, "PaginationResult/TotalNumberOfPages", 1)
        if page >= max(1, total_pages) or not items:
            break
        page += 1
    return out


def get_listing(token: str, item_id: str) -> dict:
    """Full detail for one listing, mapped to this app's Listing shape."""
    body = (
        f"<ItemID>{_esc(item_id)}</ItemID>"
        "<DetailLevel>ReturnAll</DetailLevel>"
        "<IncludeItemSpecifics>true</IncludeItemSpecifics>"
    )
    root = _call("GetItem", token, body)
    item = _find(root, "Item")
    if item is None:
        raise TradingError(f"eBay returned no details for listing {item_id}.")
    return _item_to_listing(item)


def item_id_for_sku(token: str, sku: str) -> str:
    """The item id of the listing carrying this SKU, or "".

    After a publish whose response never arrived, this answers "did that
    listing actually go up, and what is it?" without guessing from titles.

    GetItem accepts SKU in place of ItemID only for listings created with
    InventoryTrackingMethod=SKU, which is why build_add_item sets it on every
    fixed-price create. The previous version queried by
    InventoryTrackingNumber — not a GetItem input, and not an ItemType
    element either — so this recovery arm could never succeed and was in
    practice dead code. Never raises: a lookup failure just means "unknown".
    """
    if not sku:
        return ""
    try:
        root = _call("GetItem", token, f"<SKU>{_esc(sku)}</SKU>")
    except TradingError as exc:
        log.info("trading: no item for sku %s (%s)", sku, exc)
        return ""
    item = _find(root, "Item")
    return _text(item, "ItemID") if item is not None else ""


def _item_fields(listing: Listing, image_urls: Optional[list[str]] = None,
                 only: Optional[set[str]] = None) -> list[str]:
    """The <Item> children shared by create and revise: the listing's content.

    `only` restricts the output to the named Listing fields, and is how a
    revise stays minimal. A create passes None and sends everything, because
    it has no remote state to overwrite.

    Every field this omits on a revise is a field eBay keeps as it is. Every
    field it INCLUDES is one eBay overwrites — with a value this app may have
    read weeks ago. That is the difference between "update the price" and
    "replace the listing with my copy of it".
    """
    def wanted(name: str) -> bool:
        return only is None or name in only

    parts: list[str] = []
    if listing.title and wanted("title"):
        parts.append(f"<Title>{_esc(listing.title[:TITLE_MAX_CHARS])}</Title>")
    # The editor has had a Subtitle field all along and this never emitted one,
    # so a seller who typed a subtitle got no subtitle and no explanation. It
    # is a paid eBay listing upgrade (SubtitleFee), which is why it is sent
    # only when the seller actually filled the field in -- and why the editor
    # now says so where they type it.
    #
    # Only when non-empty: an empty <SubTitle/> is not "no subtitle". On a
    # revise it is a request to REMOVE one, which is a different thing to say.
    if listing.subtitle and wanted("subtitle"):
        parts.append(f"<SubTitle>{_esc(listing.subtitle[:SUBTITLE_MAX_CHARS])}"
                     "</SubTitle>")
    if listing.description and wanted("description"):
        parts.append(f"<Description>{_cdata(listing.description)}</Description>")
    if listing.category_id and wanted("category_id"):
        parts.append("<PrimaryCategory><CategoryID>"
                     f"{_esc(listing.category_id)}</CategoryID></PrimaryCategory>")
    cond_id = _CONDITION_TO_ID.get((listing.condition or "").upper())
    if cond_id and wanted("condition"):
        parts.append(f"<ConditionID>{cond_id}</ConditionID>")
    if listing.condition_description and wanted("condition_description"):
        parts.append("<ConditionDescription>"
                     f"{_esc(listing.condition_description[:1000])}</ConditionDescription>")
    if not wanted("item_specifics") and not wanted("brand"):
        specifics = []
    else:
        specifics = [s for s in listing.item_specifics
                     if s.name.strip() and s.value.strip()]
    # CRITICAL: identify, the maker double-check, and the editor's Brand field
    # all write the brand to listing.brand — not to a specifics row. The old
    # Inventory path seeded the Brand aspect from it (services/ebay.py); this
    # Trading path must too, or every publish goes out brand-less: invisible
    # to brand filters and rejected outright in Brand-required categories.
    # First value only — Brand is a SINGLE-value aspect on eBay.
    if listing.brand and not any(s.name.strip().lower() == "brand" for s in specifics):
        specifics.insert(0, ItemSpecific(
            name="Brand", value=listing.brand.split(",")[0].strip()[:65]))
    if specifics:
        # One NameValueList PER NAME, with a <Value> per value. A multi-select
        # aspect (eBay's item-specifics checkboxes: Features, Style, Season...)
        # holds several values and reaches us as several rows — emitting those
        # as separate NameValueLists is a duplicate-name error that rejects the
        # whole publish, so group them here instead.
        grouped: dict[str, list[str]] = {}
        for s in specifics:
            name = _esc(s.name.strip()[:40])
            value = _esc(s.value.strip()[:65])
            values = grouped.setdefault(name, [])
            # eBay caps an aspect at 30 values; exact repeats never earn a slot.
            if value not in values and len(values) < 30:
                values.append(value)
        rows = "".join(
            f"<NameValueList><Name>{name}</Name>"
            + "".join(f"<Value>{v}</Value>" for v in values)
            + "</NameValueList>"
            for name, values in list(grouped.items())[:60])
        parts.append(f"<ItemSpecifics>{rows}</ItemSpecifics>")
    # PictureDetails REPLACES the listing's whole photo set, so sending it on
    # an unrelated edit silently discards anything the seller added on eBay
    # since the last sync.
    if image_urls and (wanted("image_urls") or wanted("images")):
        urls = "".join(f"<PictureURL>{_esc(u)}</PictureURL>" for u in image_urls[:24])
        parts.append(f"<PictureDetails>{urls}</PictureDetails>")
    return parts


# eBay's ListingDuration tokens for a Chinese auction. The model stores the
# editor's own spelling (DAYS_10); eBay wants Days_10, and its own spelling is
# the only one it accepts.
_AUCTION_DURATIONS = {
    "DAYS_1": "Days_1", "DAYS_3": "Days_3", "DAYS_5": "Days_5",
    "DAYS_7": "Days_7", "DAYS_10": "Days_10",
}
_DEFAULT_AUCTION_DURATION = "Days_7"


def _auction_duration(listing: Listing) -> str:
    """The duration the seller chose, in eBay's spelling.

    This was hard-coded to Days_7 while the editor offered 1/3/5/7/10 days —
    so a seller who picked ten days got a seven-day auction, and nothing said
    so. An unrecognised value falls back rather than being passed through: a
    stale client or a hand-edited record must not produce a listing eBay
    rejects outright, and seven days is both eBay's default and what this
    always sent.

    Days_10 carries eBay's AuctionLengthFee, which is why the editor now says
    so next to the choice.
    """
    chosen = str(listing.auction_duration or "").strip().upper()
    return _AUCTION_DURATIONS.get(chosen, _DEFAULT_AUCTION_DURATION)


def _whole_inches(value) -> int:
    """A package dimension as eBay wants it: a whole number of inches.

    Rounded UP, not truncated. `int(10.5)` is 10, and a 10.5-inch item does
    not fit in a 10-inch box — on calculated postage that under-declaration
    is money the seller pays out of the sale, on every sale. Rounding up
    costs the buyer pennies and is the side to be wrong on.

    Floored at 0 so a nonsense value cannot reach eBay: the caller only emits
    the dimensions when all three are truthy, so a negative one becomes 0 and
    takes the whole container out of the request rather than being sent.
    """
    return max(0, int(math.ceil(float(value or 0))))


def _package_details(listing: Listing) -> str:
    """ShippingPackageDetails — eBay needs a weight for calculated shipping."""
    lb = int(listing.package_weight_lb or 0)
    oz = float(listing.package_weight_oz or 0)
    if not (lb or oz):
        return ""
    # Rounded FIRST, then gated on the rounded values. Gating on the raw ones
    # let a nonsense entry through as a zero dimension: -5 is truthy, so the
    # container was emitted and the floor turned it into
    # <PackageLength>0</PackageLength>, which is a claim about the box rather
    # than the absence of one.
    length = _whole_inches(listing.package_length_in)
    width = _whole_inches(listing.package_width_in)
    depth = _whole_inches(listing.package_height_in)
    dims = ""
    if length and width and depth:
        dims = (f"<PackageLength>{length}</PackageLength>"
                f"<PackageWidth>{width}</PackageWidth>"
                f"<PackageDepth>{depth}</PackageDepth>")
    return ("<ShippingPackageDetails>"
            f"<WeightMajor unit=\"lbs\">{lb}</WeightMajor>"
            f"<WeightMinor unit=\"oz\">{oz:g}</WeightMinor>"
            f"{dims}</ShippingPackageDetails>")


def create_listing(token: str, listing: Listing, image_urls: list[str],
                   policies: Optional[dict] = None,
                   postal_code: str = "",
                   idempotency_key: str = "") -> dict:
    """Publish a NEW listing through the Trading API.

    This is what keeps a listing editable everywhere. A listing published via
    the Sell Inventory API becomes "inventory-based", and eBay then refuses to
    edit it anywhere but the tool that created it — Seller Hub says
    "Inventory-based listing management is not currently supported by this
    tool." A Trading-API listing is an ordinary listing the seller can edit in
    Seller Hub, the eBay app, or here.

    `policies` carries the seller's business-policy ids (fulfillment/payment/
    return); with those set eBay takes shipping, payment, and returns from the
    profiles, so they don't have to be spelled out per listing.

    `idempotency_key` makes this call safe to repeat. Creating a listing is the
    one operation here that isn't naturally idempotent — a second call means a
    second live listing, which is both a duplicate on the seller's account and
    an eBay policy problem. The key rides along two ways: as UUID, which eBay
    checks against calls it has already processed (answering error 488 with
    the item id the first attempt produced), and (fixed-price only) as
    Item.SKU alongside InventoryTrackingMethod=SKU, which is what makes the
    listing findable by GetItem afterwards -- so a caller that loses the
    response can still find what it made. A collision raises
    AlreadyListedError instead of duplicating. Pass "" to opt out.
    """
    if not postal_code:
        # eBay's own words for this are "Your item's location was not filled
        # in" — accurate but useless to a seller who never saw a location
        # field. Say what to actually do instead of letting the call fail.
        raise TradingError(
            "eBay needs to know where this ships from. Add your ship-from ZIP "
            "in Settings → Listing settings and publish again.")
    call, body = build_add_item(listing, image_urls, policies, postal_code,
                                idempotency_key)
    try:
        root = _call(call, token, body)
    except TradingError as exc:
        if idempotency_key and _is_duplicate_rejection(exc):
            # eBay has already processed this exact publish. Surfacing its raw
            # wording ("UUID has already been used") would read as a failure to
            # a seller whose listing is in fact live.
            log.info("trading: %s refused as already-listed (key=%s, code=%s)",
                     call, idempotency_key, exc.code)
            raise AlreadyListedError(
                "This listing was already published to eBay.", code=exc.code,
                item_id=_item_id_in_error(str(exc)),
                detail=getattr(exc, "detail", "")) from exc
        raise
    item_id = _text(root, "ItemID")
    if not item_id:
        raise TradingError("eBay accepted the listing but returned no item id.")
    log.info("trading: %s ok item=%s", call, item_id)
    out = {"published": True, "listing_id": item_id,
           "view_url": f"https://www.ebay.com/itm/{item_id}"}
    # eBay retires categories and moves the listing to the current one on its
    # own (CategoryMappingAllowed, which this request always sets). When it
    # says so, follow it: the id in our record is the one every later revise,
    # aspect lookup and condition list is built from, and a stale one sends
    # all of them to a category the listing is no longer in.
    remapped = _text(root, "CategoryID")
    if remapped and remapped != (listing.category_id or "").strip():
        log.warning("trading: eBay remapped category %s -> %s (item %s)",
                    listing.category_id or "?", remapped, item_id)
        out["category_id"] = remapped
    return out


def build_add_item(listing: Listing, image_urls: list[str],
                   policies: Optional[dict] = None,
                   postal_code: str = "",
                   idempotency_key: str = "") -> tuple[str, str]:
    """(call name, <Item> XML) for a NEW listing.

    Exactly the body create_listing sends, built without touching the network
    or validating anything — so the dry-run preview and the real publish can
    never describe two different requests. An empty postal_code simply omits
    the element here; create_listing is what refuses to publish without one.
    """
    fmt = (listing.listing_format or "FIXED_PRICE").upper()
    is_auction = fmt.startswith("AUCTION")
    parts = _item_fields(listing, image_urls)

    if is_auction:
        start = listing.auction_start_price or listing.price or 0
        parts.append(f"<StartPrice>{float(start):.2f}</StartPrice>")
        if fmt == "AUCTION_BIN" and listing.price:
            parts.append(f"<BuyItNowPrice>{float(listing.price):.2f}</BuyItNowPrice>")
        parts.append("<ListingType>Chinese</ListingType>"
                     f"<ListingDuration>{_auction_duration(listing)}"
                     "</ListingDuration>"
                     "<Quantity>1</Quantity>")
    else:
        parts.append(f"<StartPrice>{float(listing.price or 0):.2f}</StartPrice>")
        parts.append("<ListingType>FixedPriceItem</ListingType>"
                     "<ListingDuration>GTC</ListingDuration>"
                     f"<Quantity>{max(1, int(listing.quantity or 1))}</Quantity>")

    parts.append(f"<Country>{_esc(config.EBAY_MARKETPLACE_ID[-2:] or 'US')}</Country>")
    parts.append(f"<Currency>{_esc(listing.currency or config.EBAY_CURRENCY)}</Currency>")
    if postal_code:
        parts.append(f"<PostalCode>{_esc(postal_code)}</PostalCode>")
    parts.append("<CategoryMappingAllowed>true</CategoryMappingAllowed>")
    parts.append(_package_details(listing))

    p = policies or {}
    profiles = ""
    if p.get("fulfillment_policy_id"):
        profiles += ("<SellerShippingProfile><ShippingProfileID>"
                     f"{_esc(p['fulfillment_policy_id'])}</ShippingProfileID>"
                     "</SellerShippingProfile>")
    if p.get("payment_policy_id"):
        profiles += ("<SellerPaymentProfile><PaymentProfileID>"
                     f"{_esc(p['payment_policy_id'])}</PaymentProfileID>"
                     "</SellerPaymentProfile>")
    if p.get("return_policy_id"):
        profiles += ("<SellerReturnProfile><ReturnProfileID>"
                     f"{_esc(p['return_policy_id'])}</ReturnProfileID>"
                     "</SellerReturnProfile>")
    if profiles:
        parts.append(f"<SellerProfiles>{profiles}</SellerProfiles>")

    if idempotency_key:
        # SKU tracking, which is eBay's documented answer to "the response
        # never arrived — did the listing go up, and what is it?"
        #
        # This used to send <InventoryTrackingNumber>, which is not an element
        # of eBay's ItemType at all: AddFixedPriceItem ignores it, so the
        # "unique among the seller's active listings" second guard the code
        # promised never existed, and the GetItem lookup built on it could
        # never succeed. SKU + InventoryTrackingMethod=SKU is the real
        # pairing, and BOTH must be set on the create — ReviseFixedPriceItem
        # drops InventoryTrackingMethod, so it cannot be retrofitted later.
        # Fixed-price only; auctions keep UUID alone.
        # https://developer.ebay.com/support/kb-article?KBid=1462
        if not is_auction:
            parts.append(f"<SKU>{_esc(idempotency_key[:50])}</SKU>")
            parts.append("<InventoryTrackingMethod>SKU"
                         "</InventoryTrackingMethod>")
        parts.append(f"<UUID>{_esc(_uuid_form(idempotency_key))}</UUID>")

    call = "AddItem" if is_auction else "AddFixedPriceItem"
    return call, f"<Item>{''.join(parts)}</Item>"


# The dry-run twin of each create call. eBay validates the item exactly as it
# would on the real call — account holds, selling limits and listing content
# included — and creates nothing.
_VERIFY_CALL = {"AddItem": "VerifyAddItem",
                "AddFixedPriceItem": "VerifyAddFixedPriceItem"}


def verify_listing(token: str, listing: Listing, image_urls: list[str],
                   policies: Optional[dict] = None,
                   postal_code: str = "") -> None:
    """Ask eBay whether it WOULD accept this listing. Nothing is listed.

    Returns None when eBay says it would take it, and raises TradingError —
    with the same ErrorCode the real call would have produced — when it would
    not. That makes it the one way to settle what eBay's error 240 never says:
    whether a rejection belongs to the ACCOUNT or to this listing's wording.
    Ask twice with different wording and eBay answers by contradiction.

    No idempotency key rides along: this call mints nothing, so there is
    nothing to make repeatable, and a key here would only risk colliding with
    the real publish it is diagnosing.
    """
    call, body = build_add_item(listing, image_urls, policies, postal_code,
                                idempotency_key="")
    _call(_VERIFY_CALL[call], token, body)


# eBay's error codes for "you already sent this". Codes are matched first,
# with a text fallback so a code eBay adds later still lands here —
# duplicating a listing is worse than one publish reported as already-live.
#
# 21919188 is NOT one of them: it is "this listing would cause you to exceed
# the amount you can list" — the monthly SELLING LIMIT. Treating it as a
# duplicate submission sent the seller a warning that publishing again "could
# create a duplicate", when nothing had been created and the real fix is to
# ask eBay to raise the limit. (eBay's duplicate-LISTING-policy code is
# 21919067, which is a different thing again and not an idempotency signal.)
# 488 is eBay's actual duplicate-UUID code ("Duplicate UUID used."), and it
# was missing. 21916884/21916885 were in here and are NOT idempotency signals
# — they belong to eBay's item-CONDITION family (21916885 is "Dropped
# condition from Item specifics"; 21916886 is "Item condition definitions
# have changed"). Treating a condition rejection as a duplicate told the
# seller their listing was already live and swallowed the message saying what
# to fix, so it hid a problem they could have fixed in a few seconds.
#
# 21916752 is kept: it has been observed on this path and no evidence
# contradicts it. The text fallback below is the real safety net either way —
# it matches eBay's own duplicate wording whatever code arrives — which is
# also why removing the two condition codes loses no genuine coverage.
_DUPLICATE_CODES = {"488", "21916752"}
_DUPLICATE_TEXT = re.compile(
    r"(uuid|inventory\s*tracking\s*number).{0,60}?"
    r"(already\s+(been\s+)?(used|exists|specified)|not\s+unique|duplicate)"
    r"|duplicate.{0,30}(uuid|inventory\s*tracking)", re.I | re.S)
# eBay names the offending listing inside the message ("...already been used;
# ListedByRequestAppId=1, item ID=110040602158"). Prefer the number eBay
# actually LABELS as the item id: the bare fallback would happily adopt
# ListedByRequestAppId, and pointing the seller's record at another listing is
# worse than not recovering at all.
_ERROR_ITEM_LABELLED_RE = re.compile(r"item\s*ID\s*[=:]\s*(\d{9,})", re.I)
_ERROR_ITEM_RE = re.compile(r"\b(\d{9,})\b")


def _is_duplicate_rejection(exc: TradingError) -> bool:
    return exc.code in _DUPLICATE_CODES or bool(_DUPLICATE_TEXT.search(str(exc)))


def _item_id_in_error(message: str) -> str:
    labelled = _ERROR_ITEM_LABELLED_RE.search(message or "")
    if labelled:
        return labelled.group(1)
    found = _ERROR_ITEM_RE.search(message or "")
    return found.group(1) if found else ""


def _uuid_form(key: str) -> str:
    """`key` as the 32-hex-character UUID eBay's UUID element requires."""
    if re.fullmatch(r"[0-9a-fA-F]{32}", key or ""):
        return (key or "").lower()
    return hashlib.md5(str(key).encode("utf-8")).hexdigest()  # noqa: S324


def _revise_call_name(listing: Listing) -> str:
    """ReviseFixedPriceItem for Buy It Now, ReviseItem for auctions — eBay
    rejects the wrong one for the listing's format."""
    return ("ReviseItem" if (listing.listing_format or "").upper().startswith("AUCTION")
            else "ReviseFixedPriceItem")


def build_revise_item(listing: Listing, item_id: str,
                      image_urls: Optional[list[str]] = None) -> tuple[str, str]:
    """(call name, request body) for revising one listing.

    Split out of revise_listing so the payload can be asserted on without a
    network call — what this request does NOT contain is now a correctness
    rule, not a detail (see tests/test_ebay_quantity_contract.py).
    """
    if not item_id:
        raise TradingError("This listing has no eBay item id to update.")
    if listing.has_variations:
        # eBay's own documentation: ReviseItem does not support revisions of
        # multiple-variation listings, and a variation whose quantity reaches
        # 0 is REMOVED from the listing (error 21916620), with the listing
        # ending once none are left. This used to build an item-level Quantity
        # and StartPrice revise regardless — one "update stock" away from
        # editing a structure it could not see.
        #
        # Refused here rather than at the route, because every path to a
        # revise goes through this builder.
        raise TradingError(
            "This listing has size or colour variations, and Thryft Shop "
            "can't edit those yet — changing it here could remove them. "
            "Edit it on eBay in Seller Hub; everything else about it still "
            "works here.")
    # Only what the seller actually changed. Everything else this app holds is
    # a snapshot of eBay taken at the last sync, and sending a snapshot is not
    # a no-op — it overwrites whatever eBay has now, which may be newer
    # (Seller Hub, the eBay app, a category remap eBay applied itself). A
    # seller who fixed a title on eBay and later changed only the price here
    # had the stale title pushed back over their newer one, and was told the
    # update succeeded.
    dirty = set(listing.dirty_fields)
    parts = [f"<ItemID>{_esc(item_id)}</ItemID>"]
    parts.extend(_item_fields(listing, image_urls, only=dirty))
    is_auction = (listing.listing_format or "").upper().startswith("AUCTION")
    if "price" in dirty and listing.price is not None and listing.price > 0:
        # On an auction the editable price is Buy It Now; the start price can't
        # be revised once bids exist, so it's left alone.
        tag = "BuyItNowPrice" if is_auction else "StartPrice"
        parts.append(f"<{tag}>{listing.price:.2f}</{tag}>")
    # Quantity ONLY when the seller actually changed stock.
    #
    # eBay reads the Quantity on a revise as the new AVAILABLE quantity, not
    # as a restatement of the original listing size. The value this app holds
    # is a snapshot from the last import, so re-sending it on an unrelated
    # edit (a title fix, a price change) tells eBay to make that many units
    # available again — including the ones that already sold. A seller who
    # renamed an item found stock they no longer had back on sale.
    #
    # `> 0` was the second half: it dropped zero, so the single edit that
    # takes a listing out of stock was the one that never reached eBay.
    if listing.is_dirty("quantity") and listing.quantity is not None:
        parts.append(f"<Quantity>{max(0, int(listing.quantity))}</Quantity>")
    if "fulfillment_policy_id" in dirty and listing.fulfillment_policy_id:
        # The seller picked a shipping service for THIS listing — send the
        # matching business-policy profile so the revise actually changes it.
        parts.append("<SellerProfiles><SellerShippingProfile><ShippingProfileID>"
                     f"{_esc(listing.fulfillment_policy_id)}</ShippingProfileID>"
                     "</SellerShippingProfile></SellerProfiles>")
    return _revise_call_name(listing), f"<Item>{''.join(parts)}</Item>"


def revise_listing(token: str, item_id: str, listing: Listing,
                   image_urls: Optional[list[str]] = None) -> dict:
    """Push an edit back to a listing this app didn't create.

    Sends only the fields this app actually edits, so nothing set elsewhere on
    the listing gets clobbered by omission. Returns {"ok": True, "listing_id"}
    or raises TradingError with eBay's own reason."""
    call, body = build_revise_item(listing, item_id, image_urls)
    root = _call(call, token, body)
    returned = _text(root, "ItemID") or item_id
    log.info("trading: %s ok item=%s", call, returned)
    return {"ok": True, "listing_id": returned}


def end_listing(token: str, item_id: str, reason: str = "NotAvailable") -> dict:
    """End a listing this app didn't create."""
    if not item_id:
        raise TradingError("This listing has no eBay item id to end.")
    _call("EndItem", token,
          f"<ItemID>{_esc(item_id)}</ItemID>"
          f"<EndingReason>{_esc(reason)}</EndingReason>")
    return {"ended": True, "listing_id": item_id}


def listing_status(token: str, item_id: str) -> tuple[Optional[str], int, int]:
    """(status, sold_quantity, watch_count) for one listing, where status is
    'published' | 'sold' | 'ended' | None (couldn't tell — change nothing)."""
    try:
        root = _call("GetItem", token,
                     f"<ItemID>{_esc(item_id)}</ItemID><DetailLevel>ReturnAll</DetailLevel>")
    except TradingError as exc:
        log.info("trading: status check failed for %s: %s", item_id, exc)
        return None, 0, 0
    item = _find(root, "Item")
    if item is None:
        return None, 0, 0
    selling = _find(item, "SellingStatus")
    state = (_text(selling, "ListingStatus") or "").upper()
    sold = _int(selling, "QuantitySold") if selling is not None else 0
    watch = _int(item, "WatchCount")
    if state == "ACTIVE":
        return "published", sold, watch
    if state in ("COMPLETED", "ENDED"):
        return ("sold" if sold > 0 else "ended"), sold, watch
    return None, sold, watch

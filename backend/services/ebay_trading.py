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
import re
from typing import Any, Optional
from xml.etree import ElementTree as ET

import httpx

from .. import config
from ..config import log
from ..models import TITLE_MAX_CHARS, ItemSpecific, Listing

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
    """

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class AlreadyListedError(TradingError):
    """eBay refused a create because THIS publish already produced a listing.

    Raised when the idempotency key on AddItem/AddFixedPriceItem (see
    `create_listing`) collides with one eBay has already seen — the signal that
    a retried or racing publish is about to mint a duplicate. `item_id` is the
    listing that already exists when eBay names it, so the caller can adopt it
    instead of creating a second one.
    """

    def __init__(self, message: str, code: str = "", item_id: str = ""):
        super().__init__(message, code)
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
    if resp.status_code != 200:
        raise TradingError(f"eBay returned {resp.status_code} for {call}.")
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise TradingError(f"eBay sent an unreadable response for {call}.") from exc
    ack = (_text(root, "Ack") or "").lower()
    if ack in ("failure", "partialfailure"):
        errors = [e for e in _findall(root, "Errors")
                  if (_text(e, "SeverityCode") or "").lower() == "error"]
        if errors:
            first = errors[0]
            msg = (_text(first, "LongMessage") or _text(first, "ShortMessage")
                   or "eBay rejected the request.")
            code = _text(first, "ErrorCode") or ""
            if code in ("931", "932", "16110", "21917053"):  # auth/token codes
                raise TradingError(
                    "eBay didn't accept the account connection — reconnect eBay "
                    "in Settings and try again.", code=code)
            raise TradingError(msg.strip()[:300], code=code)
    return root


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
_CONDITION_BY_ID = {
    "1000": "NEW", "1500": "NEW_OTHER", "1750": "NEW_WITH_DEFECTS",
    "2000": "CERTIFIED_REFURBISHED", "2010": "CERTIFIED_REFURBISHED",
    "2020": "SELLER_REFURBISHED", "2030": "SELLER_REFURBISHED",
    "2500": "SELLER_REFURBISHED", "2750": "USED_EXCELLENT",
    "3000": "USED_EXCELLENT", "4000": "USED_VERY_GOOD",
    "5000": "USED_GOOD", "6000": "USED_ACCEPTABLE",
    "7000": "FOR_PARTS_OR_NOT_WORKING",
}
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
        "quantity": max(1, _int(item, "Quantity", 1)),
        "listing_format": fmt,
        "auction_start_price": round(auction_start, 2) if auction_start else None,
        "package_weight_lb": float(weight_major or 0),
        "package_weight_oz": round(weight_minor or 0.0, 1),
        "package_length_in": float(_int(dims, "PackageLength") if dims is not None else 0),
        "package_width_in": float(_int(dims, "PackageWidth") if dims is not None else 0),
        "package_height_in": float(_int(dims, "PackageDepth") if dims is not None else 0),
        "item_specifics": specifics,
        "images": [],           # no local files — this listing came from eBay
        "image_urls": pictures,  # eBay-hosted photos, shown as-is
        "ebay_listing_id": _text(item, "ItemID"),
        "sku": _text(item, "SKU"),
        "source": "ebay",
        "watch_count": _int(item, "WatchCount"),
        "sold_quantity": (_int(selling, "QuantitySold") if selling is not None else 0),
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


def item_id_for_tracking_number(token: str, tracking_number: str) -> str:
    """The item id of the listing carrying this InventoryTrackingNumber, or "".

    GetItem accepts an InventoryTrackingNumber in place of an ItemID, which is
    what makes the tracking number a usable idempotency key: after a publish
    whose response never arrived, this answers "did that listing actually go
    up, and what is it?" without guessing from titles. Never raises — a lookup
    failure just means "unknown".
    """
    if not tracking_number:
        return ""
    try:
        root = _call("GetItem", token,
                     f"<InventoryTrackingNumber>{_esc(tracking_number)}</InventoryTrackingNumber>")
    except TradingError as exc:
        log.info("trading: no item for tracking number %s (%s)",
                 tracking_number, exc)
        return ""
    item = _find(root, "Item")
    return _text(item, "ItemID") if item is not None else ""


def _item_fields(listing: Listing, image_urls: Optional[list[str]] = None) -> list[str]:
    """The <Item> children shared by create and revise: the listing's content."""
    parts: list[str] = []
    if listing.title:
        parts.append(f"<Title>{_esc(listing.title[:TITLE_MAX_CHARS])}</Title>")
    if listing.description:
        parts.append(f"<Description>{_cdata(listing.description)}</Description>")
    if listing.category_id:
        parts.append("<PrimaryCategory><CategoryID>"
                     f"{_esc(listing.category_id)}</CategoryID></PrimaryCategory>")
    cond_id = _CONDITION_TO_ID.get((listing.condition or "").upper())
    if cond_id:
        parts.append(f"<ConditionID>{cond_id}</ConditionID>")
    if listing.condition_description:
        parts.append("<ConditionDescription>"
                     f"{_esc(listing.condition_description[:1000])}</ConditionDescription>")
    specifics = [s for s in listing.item_specifics if s.name.strip() and s.value.strip()]
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
    if image_urls:
        urls = "".join(f"<PictureURL>{_esc(u)}</PictureURL>" for u in image_urls[:24])
        parts.append(f"<PictureDetails>{urls}</PictureDetails>")
    return parts


def _package_details(listing: Listing) -> str:
    """ShippingPackageDetails — eBay needs a weight for calculated shipping."""
    lb = int(listing.package_weight_lb or 0)
    oz = float(listing.package_weight_oz or 0)
    if not (lb or oz):
        return ""
    dims = ""
    if listing.package_length_in and listing.package_width_in and listing.package_height_in:
        dims = (f"<PackageLength>{int(listing.package_length_in)}</PackageLength>"
                f"<PackageWidth>{int(listing.package_width_in)}</PackageWidth>"
                f"<PackageDepth>{int(listing.package_height_in)}</PackageDepth>")
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
    checks against calls it has already processed, and (fixed-price only) as
    InventoryTrackingNumber, which must be unique among the seller's active
    listings and is separately queryable, so a caller that loses the response
    can find the listing it made. Either collision raises AlreadyListedError
    instead of duplicating. Pass "" to opt out.
    """
    if not postal_code:
        # eBay's own words for this are "Your item's location was not filled
        # in" — accurate but useless to a seller who never saw a location
        # field. Say what to actually do instead of letting the call fail.
        raise TradingError(
            "eBay needs to know where this ships from. Add your ship-from ZIP "
            "in Settings → Listing settings and publish again.")
    fmt = (listing.listing_format or "FIXED_PRICE").upper()
    is_auction = fmt.startswith("AUCTION")
    parts = _item_fields(listing, image_urls)

    if is_auction:
        start = listing.auction_start_price or listing.price or 0
        parts.append(f"<StartPrice>{float(start):.2f}</StartPrice>")
        if fmt == "AUCTION_BIN" and listing.price:
            parts.append(f"<BuyItNowPrice>{float(listing.price):.2f}</BuyItNowPrice>")
        parts.append("<ListingType>Chinese</ListingType>"
                     "<ListingDuration>Days_7</ListingDuration>"
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
        # InventoryTrackingNumber is fixed-price only, and can't be combined
        # with an item-level SKU (this path sends none).
        if not is_auction:
            parts.append("<InventoryTrackingNumber>"
                         f"{_esc(idempotency_key[:50])}</InventoryTrackingNumber>")
        parts.append(f"<UUID>{_esc(_uuid_form(idempotency_key))}</UUID>")

    call = "AddItem" if is_auction else "AddFixedPriceItem"
    try:
        root = _call(call, token, f"<Item>{''.join(parts)}</Item>")
    except TradingError as exc:
        if idempotency_key and _is_duplicate_rejection(exc):
            # eBay has already processed this exact publish. Surfacing its raw
            # wording ("UUID has already been used") would read as a failure to
            # a seller whose listing is in fact live.
            log.info("trading: %s refused as already-listed (key=%s, code=%s)",
                     call, idempotency_key, exc.code)
            raise AlreadyListedError(
                "This listing was already published to eBay.", code=exc.code,
                item_id=_item_id_in_error(str(exc))) from exc
        raise
    item_id = _text(root, "ItemID")
    if not item_id:
        raise TradingError("eBay accepted the listing but returned no item id.")
    log.info("trading: %s ok item=%s", call, item_id)
    return {"published": True, "listing_id": item_id,
            "view_url": f"https://www.ebay.com/itm/{item_id}"}


# eBay's error codes for "you already sent this": a reused UUID, and an
# InventoryTrackingNumber already on an active listing. Codes are matched
# first, with a text fallback so a code eBay adds later still lands here —
# duplicating a listing is worse than one publish reported as already-live.
_DUPLICATE_CODES = {"21916884", "21916885", "21919188", "21916752"}
_DUPLICATE_TEXT = re.compile(
    r"(uuid|inventory\s*tracking\s*number).{0,60}?"
    r"(already\s+(been\s+)?(used|exists|specified)|not\s+unique|duplicate)"
    r"|duplicate.{0,30}(uuid|inventory\s*tracking)", re.I | re.S)
# eBay names the offending listing inside the message often enough to be worth
# reading ("...already used for item 123456789012").
_ERROR_ITEM_RE = re.compile(r"\b(\d{9,})\b")


def _is_duplicate_rejection(exc: TradingError) -> bool:
    return exc.code in _DUPLICATE_CODES or bool(_DUPLICATE_TEXT.search(str(exc)))


def _item_id_in_error(message: str) -> str:
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


def revise_listing(token: str, item_id: str, listing: Listing,
                   image_urls: Optional[list[str]] = None) -> dict:
    """Push an edit back to a listing this app didn't create.

    Sends only the fields this app actually edits, so nothing set elsewhere on
    the listing gets clobbered by omission. Returns {"ok": True, "listing_id"}
    or raises TradingError with eBay's own reason."""
    if not item_id:
        raise TradingError("This listing has no eBay item id to update.")
    parts = [f"<ItemID>{_esc(item_id)}</ItemID>"]
    parts.extend(_item_fields(listing, image_urls))
    is_auction = (listing.listing_format or "").upper().startswith("AUCTION")
    if listing.price is not None and listing.price > 0:
        # On an auction the editable price is Buy It Now; the start price can't
        # be revised once bids exist, so it's left alone.
        tag = "BuyItNowPrice" if is_auction else "StartPrice"
        parts.append(f"<{tag}>{listing.price:.2f}</{tag}>")
    if listing.quantity and listing.quantity > 0:
        parts.append(f"<Quantity>{int(listing.quantity)}</Quantity>")
    if listing.fulfillment_policy_id:
        # The seller picked a shipping service for THIS listing — send the
        # matching business-policy profile so the revise actually changes it.
        parts.append("<SellerProfiles><SellerShippingProfile><ShippingProfileID>"
                     f"{_esc(listing.fulfillment_policy_id)}</ShippingProfileID>"
                     "</SellerShippingProfile></SellerProfiles>")

    call = _revise_call_name(listing)
    root = _call(call, token, f"<Item>{''.join(parts)}</Item>")
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

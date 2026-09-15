"""Price suggestions from real eBay market data, with pluggable sources.

Layer 1 (implemented): ACTIVE COMPS — what comparable items are listed for
right now, from the Buy Browse API. Official and generally available; needs
only the application token (EBAY_CLIENT_ID/SECRET) already used for taxonomy.
These are asking prices, not sold prices, and the UI labels them as such.

Layer 2 (plug-in point): SOLD COMPS — what comparable items actually sold
for. eBay's official route is the Marketplace Insights API (limited release;
requires eBay approval). `sold_comps()` below is the seam: implement it and
`suggest()` will automatically prefer it over asking prices.
"""
from __future__ import annotations

import statistics
from typing import Optional
from urllib.parse import quote_plus

import httpx

from .. import config
from ..config import log
from ..money import charm_price
from .taxonomy import _app_token

# Inventory-API condition enums that count as "new" for comp matching;
# everything else (LIKE_NEW, USED_*, FOR_PARTS...) shops the USED bucket.
_NEW_CONDITIONS = {"NEW", "NEW_OTHER", "NEW_WITH_DEFECTS"}

MAX_SAMPLE = 8  # comps shown in the UI


def _condition_bucket(condition: Optional[str]) -> Optional[str]:
    if not condition:
        return None
    return "NEW" if condition.strip().upper() in _NEW_CONDITIONS else "USED"


def active_comps(query: str, category_id: Optional[str] = None,
                 condition: Optional[str] = None, limit: int = 50,
                 gtin: Optional[str] = None) -> Optional[dict]:
    """Stats over live fixed-price asking prices for comparable items.

    `gtin` (a check-digit-verified UPC read off the item's barcode) searches
    Browse by PRODUCT rather than by keywords. That is a different and much
    better question: a keyword query matches whatever else happens to share
    the title's words, while a UPC matches the same product and nothing else,
    so the median it returns is the price of THIS item instead of the price
    of items that sound like it. Browse documents the parameter as taking a
    UPC specifically, and it replaces `q` rather than joining it.
    """
    if not config.taxonomy_ready():
        return None
    # eBay's Browse filter spec requires `priceCurrency` to be paired with a
    # `price` filter; sending it alone 400s. We don't range-filter, so omit it
    # — the marketplace header already scopes results to the US/USD site.
    filters = ["buyingOptions:{FIXED_PRICE}"]
    bucket = _condition_bucket(condition)
    if bucket:
        filters.append("conditions:{%s}" % bucket)
    params = {"limit": str(limit), "filter": ",".join(filters)}
    if gtin:
        params["gtin"] = str(gtin)
    else:
        params["q"] = query
    if category_id:
        params["category_ids"] = str(category_id)

    resp = httpx.get(
        f"{config.EBAY_API_BASE}/buy/browse/v1/item_summary/search",
        params=params,
        headers={
            "Authorization": f"Bearer {_app_token()}",
            "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID,
        },
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get("itemSummaries", []) or []

    prices: list[float] = []
    sample: list[dict] = []
    for it in items:
        try:
            price = float((it.get("price") or {}).get("value"))
        except (TypeError, ValueError):
            continue
        prices.append(price)
        if len(sample) < MAX_SAMPLE:
            sample.append({
                "title": it.get("title", ""),
                "price": round(price, 2),
                "condition": it.get("condition", ""),
                "url": it.get("itemWebUrl", ""),
            })
    if not prices:
        return None

    prices.sort()
    if len(prices) >= 4:
        q1, _, q3 = statistics.quantiles(prices, n=4)
    else:
        q1, q3 = prices[0], prices[-1]
    return {
        "source": "active_comps",
        # The label reaches the seller, and the difference matters to them:
        # a UPC match is the same product, a keyword match is items that read
        # like it. A number they are told the basis of is a number they can
        # judge.
        "label": ("Live asking prices for this exact product (barcode match)"
                  if gtin else "Live asking prices on eBay"),
        "sold_data": False,
        "estimate": round(statistics.median(prices), 2),
        "low": round(q1, 2),
        "high": round(q3, 2),
        "count": len(prices),
        "sample": sample,
        "search_url": ("https://www.ebay.com/sch/i.html?_nkw="
                       + quote_plus(gtin or query) + "&LH_BIN=1"),
    }


# Marketplace Insights is a LIMITED RELEASE API: the credentials work, the
# endpoint exists, and it answers 403 until eBay has approved this application
# for the buy.marketplace.insights scope. Approval is per-application and is
# applied for, so the normal state for most installs is "not approved yet".
#
# That distinction has to survive all the way to the seller. `suggest` already
# separates "the market has nothing like this" from "we could not look"
# (`checked`, and test_a_failed_price_lookup_is_not_no_comps), and an
# unapproved 403 is neither of those: it is a source that is not turned on. It
# must not be reported as a market with no sales in it, and it must not make
# every draft look like a failed lookup either.
#
# So a 403 LATCHES. The first one raises, so `suggest` records a failed source
# for that call and `checked` tells the truth about it; after that the source
# short-circuits and stops burning a request per item against an endpoint that
# will refuse all of them. Cleared on process restart, which is when a newly
# granted approval would be picked up anyway.
_INSIGHTS_DENIED = False


class InsightsNotApproved(RuntimeError):
    """eBay has not granted this application the marketplace-insights scope."""


def insights_enabled() -> bool:
    return config.taxonomy_ready() and not _INSIGHTS_DENIED


def reset_insights_latch() -> None:
    """For tests, and for a process that has just been granted the scope."""
    global _INSIGHTS_DENIED
    _INSIGHTS_DENIED = False


def sold_comps(query: str, category_id: Optional[str] = None,
               condition: Optional[str] = None,
               gtin: Optional[str] = None) -> Optional[dict]:
    """What comparable items actually SOLD for — the signal active_comps only
    approximates.

    An asking price is what somebody hopes for; a sold price is what somebody
    paid. For art the gap between them is enormous, because an unsold print
    can sit at an optimistic price for years and every one of those listings
    is a "comp" to a keyword search.

    Returns the same dict shape as active_comps with sold_data=True, so
    suggest() prefers it automatically (it is first in _SOURCES). None means
    the search ran and found nothing comparable. RAISES when the lookup could
    not be made at all, which is what keeps "no sales" and "no answer" apart
    in the seller's price card.
    """
    if not insights_enabled():
        return None
    filters = ["buyingOptions:{FIXED_PRICE|AUCTION}"]
    bucket = _condition_bucket(condition)
    if bucket:
        filters.append("conditions:{%s}" % bucket)
    params = {"limit": "50", "filter": ",".join(filters)}
    if gtin:
        params["gtin"] = str(gtin)
    else:
        params["q"] = query
    if category_id:
        params["category_ids"] = str(category_id)

    resp = httpx.get(
        f"{config.EBAY_API_BASE}/buy/marketplace_insights/v1_beta"
        f"/item_sales/search",
        params=params,
        headers={
            "Authorization": f"Bearer {_app_token()}",
            "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID,
        },
        timeout=30,
    )
    if resp.status_code in (401, 403):
        global _INSIGHTS_DENIED
        _INSIGHTS_DENIED = True
        log.info("pricing: marketplace insights is not approved for this "
                 "application (%s) — sold prices are off until eBay grants "
                 "the buy.marketplace.insights scope", resp.status_code)
        raise InsightsNotApproved(
            "eBay has not approved this application for sold-price data")
    resp.raise_for_status()
    return parse_sold(resp.json(), query, gtin=gtin)


def parse_sold(data, query: str, gtin: Optional[str] = None) -> Optional[dict]:
    """The stats an item_sales payload supports, or None when it holds no
    usable price. Split from the fetch so the shape can be asserted without a
    live approval — the same split services/imagesearch uses."""
    items = (data or {}).get("itemSales") or [] if isinstance(data, dict) else []
    prices: list[float] = []
    sample: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # lastSoldPrice is what this item actually went for. Its absence
        # means the row is not a sale we can price from.
        try:
            price = float((it.get("lastSoldPrice") or {}).get("value"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        prices.append(price)
        if len(sample) < MAX_SAMPLE:
            sample.append({
                "title": str(it.get("title") or "")[:140],
                "price": round(price, 2),
                "condition": str(it.get("condition") or ""),
                "url": str(it.get("itemWebUrl") or ""),
            })
    if not prices:
        return None

    prices.sort()
    if len(prices) >= 4:
        q1, _, q3 = statistics.quantiles(prices, n=4)
    else:
        q1, q3 = prices[0], prices[-1]
    return {
        "source": "sold_comps",
        "label": ("What this exact product actually sold for (barcode match)"
                  if gtin else "What comparable items actually sold for"),
        "sold_data": True,
        "estimate": round(statistics.median(prices), 2),
        "low": round(q1, 2),
        "high": round(q3, 2),
        "count": len(prices),
        "sample": sample,
        "search_url": ("https://www.ebay.com/sch/i.html?_nkw="
                       + quote_plus(gtin or query) + "&LH_Sold=1&LH_Complete=1"),
    }


_SOURCES = (sold_comps, active_comps)  # preferred first


# Account pricing strategy → which end of the comp range the headline
# suggestion lands on. "estimate" is the median; low/high are the quartiles.
_STRATEGY_PICK = {
    "quick_flip": ("low", "Quick Flip — priced at the low end to sell fast"),
    "median": ("estimate", "Median — typical market price"),
    "long_sale": ("high", "Long Sale — priced at the high end, patient sale"),
}


def suggest(query: str, category_id: Optional[str] = None,
            condition: Optional[str] = None, strategy: str = "",
            gtin: Optional[str] = None) -> dict:
    """Run every configured pricing source; never fail the whole call because
    one source errored (log it and keep what we have). `strategy`
    (quick_flip | median | long_sale) picks which end of the comp range the
    headline suggestion uses; the full low/median/high always rides along.
    `gtin` is a verified UPC off the item's own barcode — when there is one,
    every source is asked about that PRODUCT rather than about the words in
    the title."""
    sources, failed = [], []
    for src in _SOURCES:
        try:
            result = src(query, category_id=category_id, condition=condition,
                         gtin=gtin)
            if result:
                sources.append(result)
        except InsightsNotApproved as exc:
            # A source that is not turned on, which is the NORMAL state for
            # most installs (see _INSIGHTS_DENIED above) — not a failure an
            # operator can act on. It still counts as failed, because
            # `checked` below must keep telling the truth about whether
            # anything got to look; it just does not belong at warning level,
            # which is where error capture starts. Left there it filed itself
            # in the production error feed once per process restart, as a bug
            # report against the one condition the latch exists to expect.
            failed.append(src.__name__)
            log.info("pricing: %s is not approved for this application (%s)",
                     src.__name__, exc)
        except Exception as exc:  # noqa: BLE001 - a source is best-effort
            failed.append(src.__name__)
            log.warning("pricing: %s failed for %r: %s", src.__name__, query, exc)
    best = sources[0] if sources else None
    pick, strategy_label = _STRATEGY_PICK.get(strategy, (None, None))
    return {
        "query": query,
        # What was actually searched for, when it was not the query: the
        # editor shows the basis, and "we priced this off its barcode" is a
        # different claim from "we priced this off its title".
        "gtin": gtin or "",
        "sources": sources,
        "strategy": strategy or "median",
        # Did we actually get to look? Without this, a 429 against the shared
        # application quota, an expired app token or a network blip produced
        # the same answer as a search over a market with nothing comparable in
        # it — and both screens reading this state it as fact: the editor says
        # "No comparable listings found" and sends the seller off to rewrite a
        # title that was never the problem, and Shop Mode says "No price
        # estimate yet" to someone deciding whether to spend their own money
        # on the item in their hand. A source that answered is enough to have
        # looked; only a clean sweep of failures is unknown.
        "checked": bool(sources) or not failed,
        "suggestion": None if not best else {
            # The headline is a price to LIST at, so it lands on a .99 like
            # every other price this app chooses (money.charm_price). The
            # range and the comps beside it are measurements of the market and
            # stay exactly as measured — a median reported as $24.99 when the
            # median is $25.00 would be a lie about the data.
            "price": charm_price(best[pick] if pick else best["estimate"]),
            "low": best["low"],
            "high": best["high"],
            "count": best["count"],
            "basis": (f"{best['label']} · {strategy_label}" if strategy_label
                      else best["label"]),
            "sold_data": best["sold_data"],
        },
    }

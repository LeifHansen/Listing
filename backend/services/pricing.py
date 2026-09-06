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
                 condition: Optional[str] = None, limit: int = 50) -> Optional[dict]:
    """Stats over live fixed-price asking prices for comparable items."""
    if not config.taxonomy_ready():
        return None
    # eBay's Browse filter spec requires `priceCurrency` to be paired with a
    # `price` filter; sending it alone 400s. We don't range-filter, so omit it
    # — the marketplace header already scopes results to the US/USD site.
    filters = ["buyingOptions:{FIXED_PRICE}"]
    bucket = _condition_bucket(condition)
    if bucket:
        filters.append("conditions:{%s}" % bucket)
    params = {"q": query, "limit": str(limit), "filter": ",".join(filters)}
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
        "label": "Live asking prices on eBay",
        "sold_data": False,
        "estimate": round(statistics.median(prices), 2),
        "low": round(q1, 2),
        "high": round(q3, 2),
        "count": len(prices),
        "sample": sample,
        "search_url": ("https://www.ebay.com/sch/i.html?_nkw=" + quote_plus(query)
                       + "&LH_BIN=1"),
    }


def sold_comps(query: str, category_id: Optional[str] = None,
               condition: Optional[str] = None) -> Optional[dict]:
    """Plug-in point for real sold-price data (the better signal).

    Implement with the Marketplace Insights API once eBay approves access
    (GET /buy/marketplace_insights/v1_beta/item_sales/search — same shape as
    active_comps above), or an env-gated third-party feed. Return the same
    dict shape with source="sold_comps" and sold_data=True; suggest() will
    then prefer it automatically.
    """
    return None


_SOURCES = (sold_comps, active_comps)  # preferred first


# Account pricing strategy → which end of the comp range the headline
# suggestion lands on. "estimate" is the median; low/high are the quartiles.
_STRATEGY_PICK = {
    "quick_flip": ("low", "Quick Flip — priced at the low end to sell fast"),
    "median": ("estimate", "Median — typical market price"),
    "long_sale": ("high", "Long Sale — priced at the high end, patient sale"),
}


def suggest(query: str, category_id: Optional[str] = None,
            condition: Optional[str] = None, strategy: str = "") -> dict:
    """Run every configured pricing source; never fail the whole call because
    one source errored (log it and keep what we have). `strategy`
    (quick_flip | median | long_sale) picks which end of the comp range the
    headline suggestion uses; the full low/median/high always rides along."""
    sources, failed = [], []
    for src in _SOURCES:
        try:
            result = src(query, category_id=category_id, condition=condition)
            if result:
                sources.append(result)
        except Exception as exc:  # noqa: BLE001 - a source is best-effort
            failed.append(src.__name__)
            log.warning("pricing: %s failed for %r: %s", src.__name__, query, exc)
    best = sources[0] if sources else None
    pick, strategy_label = _STRATEGY_PICK.get(strategy, (None, None))
    return {
        "query": query,
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

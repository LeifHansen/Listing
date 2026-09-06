"""eBay listing metrics — views/impressions (Sell Analytics) + watchers and
pending Best Offers (Trading API).

Best-effort and fail-soft: metrics are a nice-to-have overlay, never allowed to
break a page. Each source is fetched independently, so one failing (e.g. the
seller hasn't granted the analytics scope yet) doesn't sink the other. Results
are cached briefly so the dashboard's insights + the listing grid don't double
up the same eBay calls.

Views/impressions come from Sell Analytics getTrafficReport (needs the
sell.analytics.readonly scope). Watch counts come from the Trading API's
GetMyeBaySelling in a single call (the Sell APIs don't expose watchers), and
that same call names which listings have ever had a Best Offer — the shortlist
GetBestOffers then turns into "how many are waiting on you right now".

Fail-soft is not the same as fail-silent: when the traffic report can't be
read, `listing_metrics` says so through its `status` out-dict, so the UI can
explain the blank numbers instead of showing every listing 0 views.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from .. import config
from . import ebay_trading

log = logging.getLogger("thryft.metrics")

_CACHE: dict[str, tuple[float, dict, dict]] = {}
_TTL = 120  # seconds — enough to dedupe the insights + grid fetches

# The traffic metrics we ask for, in request order, and the field each one
# lands in. eBay echoes the columns back in this order, which is the fallback
# when the response header doesn't name them.
_METRICS = ["LISTING_IMPRESSION_TOTAL", "LISTING_VIEWS_TOTAL"]
_FIELD_BY_METRIC = {
    "LISTING_IMPRESSION_TOTAL": "impressions",
    "LISTING_VIEWS_TOTAL": "views",
}


class TrafficUnavailable(RuntimeError):
    """getTrafficReport couldn't be read. `needs_reconnect` flags the one cause
    the seller can fix: a token granted before sell.analytics.readonly was
    requested (refreshes keep the originally approved scopes, so only
    reconnecting adds it)."""

    def __init__(self, message: str, needs_reconnect: bool = False):
        super().__init__(message)
        self.needs_reconnect = needs_reconnect


def _is_scope_error(resp: httpx.Response) -> bool:
    """A refusal the seller can fix by reconnecting, vs. a transient API blip."""
    if resp.status_code in (401, 403):
        return True
    body = resp.text.lower()
    return ("insufficient" in body and "scope" in body) or "access_denied" in body


def _metric_keys(data: dict) -> list[str]:
    """The metric name of each metricValues column, in order.

    eBay names them in header.metrics[].key (an array of objects carrying
    key/dataType/localizedName). Reading only header.metricKeys — which the
    live API never sends — yielded no columns at all, so every record parsed
    into an empty dict and every listing read as 0 views. Both spellings are
    accepted now, as strings or objects; [] means the header named nothing.
    """
    header = data.get("header") or {}
    for field in ("metrics", "metricKeys"):
        raw = header.get(field) or []
        keys = [k if isinstance(k, str) else (k or {}).get("key") for k in raw]
        keys = [str(k) for k in keys if k]
        if keys:
            return keys
    return []


def _metric_value(cell) -> int:
    """One metricValues entry as an int. Values arrive as {'value': n} (ints,
    floats, or numeric strings depending on the metric); anything unparseable
    counts as 0."""
    raw = cell.get("value") if isinstance(cell, dict) else cell
    try:
        return int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _traffic(token: str, listing_ids: list[str]) -> dict[str, dict]:
    """views + impressions per listing over the last 30 days, via Sell
    Analytics getTrafficReport. {listing_id: {'views': n, 'impressions': n}}."""
    if not listing_ids:
        return {}
    # Yesterday, not today. eBay rejects the whole report — "Neither the start
    # date nor the end date can be in the future" — for any end date it
    # considers future, and it judges that in Pacific time while the app runs
    # in UTC: for the seven hours after UTC midnight, "today" here is still
    # tomorrow there. The report only holds data through yesterday anyway, so
    # stepping back a day costs nothing and is safe in every timezone.
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=30)
    filters = [
        "marketplace_ids:{%s}" % config.EBAY_MARKETPLACE_ID,
        f"date_range:[{start:%Y%m%d}..{end:%Y%m%d}]",
        "listing_ids:{%s}" % "|".join(listing_ids[:200]),
    ]
    r = httpx.get(
        f"{config.EBAY_API_BASE}/sell/analytics/v1/traffic_report",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params={
            "dimension": "LISTING",
            "metric": ",".join(_METRICS),
            "filter": ",".join(filters),
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise TrafficUnavailable(f"traffic_report {r.status_code}: {r.text[:160]}",
                                 needs_reconnect=_is_scope_error(r))
    data = r.json()
    keys = _metric_keys(data)
    out: dict[str, dict] = {}
    for rec in data.get("records", []) or []:
        dims = rec.get("dimensionValues") or []
        lid = str((dims[0] or {}).get("value")) if dims else ""
        if not lid:
            continue
        vals = rec.get("metricValues") or []
        # No named columns: fall back to the order we asked for, but only when
        # the widths match — a mismatch means guessing, and a wrong number is
        # worse than none.
        cols = keys or (_METRICS if len(vals) == len(_METRICS) else [])
        m: dict[str, int] = {}
        for i, k in enumerate(cols):
            field = _FIELD_BY_METRIC.get(k)
            if field and i < len(vals):
                m[field] = _metric_value(vals[i])
        out[lid] = m
    return out


def _active_counts(token: str) -> dict[str, dict]:
    """{item_id: {"watchers": n, "offers_received": n}} for every active
    listing. The Sell APIs expose neither number, so this goes through the
    Trading API — via ebay_trading so the endpoint honors EBAY_ENV (the old
    inline call hardcoded production) and errors surface with the shared
    handling. One GetMyeBaySelling walk carries both; see
    ebay_trading.active_listing_counts for why they travel together."""
    return ebay_trading.active_listing_counts(token)


# How many listings one sweep may ask GetBestOffers about. The ActiveList walk
# hands over which listings have EVER had an offer; each of those costs one
# more Trading call to turn into "and how many are pending right now". Most
# stores have a handful, so the cap is never reached; the store that has
# haggled on hundreds of listings gets the busiest of them answered rather
# than spending its whole Trading allowance on a badge.
_OFFER_LOOKUPS = 25


def _offers(token: str, counts: dict[str, dict],
            ids: list[str]) -> tuple[dict[str, dict], set[str]]:
    """({item_id: pending-offer summary}, ids we can honestly report on).

    Two-step, and the second step is the point. eBay's per-listing
    BestOfferCount (already in hand from the ActiveList sweep) counts offers
    RECEIVED, settled ones included, so it cannot answer "is a buyer waiting".
    It answers the cheap half — a listing at zero has never had an offer at
    all — and GetBestOffers answers the rest exactly, one listing at a time.

    The second return value is which listings the caller may state a number
    for: a zero from the sweep, or a lookup that came back. A listing whose
    lookup failed is left out entirely rather than reported as nought, because
    "no offer" and "we could not ask" are different things to tell a seller
    about money on the table.
    """
    known = {i for i in ids if not (counts.get(i) or {}).get("offers_received")}
    candidates = [i for i in ids if (counts.get(i) or {}).get("offers_received")]
    # Busiest first, so a store past the cap gets the listings with the most
    # offers answered rather than whichever happened to sort first.
    candidates.sort(key=lambda i: -counts[i]["offers_received"])
    out: dict[str, dict] = {}
    for item_id in candidates[:_OFFER_LOOKUPS]:
        try:
            summary = ebay_trading.pending_offers(token, item_id)
        except Exception as exc:  # noqa: BLE001 - one listing, not the sweep
            log.info("pending offers unavailable for %s: %s", item_id, exc)
            continue
        known.add(item_id)
        if summary.get("count"):
            out[item_id] = summary
    return out, known


def listing_metrics(creds: Optional[dict], listing_ids: list[str],
                    status: Optional[dict] = None) -> dict[str, dict]:
    """Combined {listing_id: {views, impressions, watchers}} for the given eBay
    listing ids. Best-effort per source; returns {} if nothing was fetched.
    Cached for a short window keyed by the token + id set.

    Pass a `status` dict to also learn whether the traffic report itself came
    back — it gets {'traffic_ok': bool, 'needs_reconnect': bool}, which is how
    the UI tells "nobody has viewed these yet" apart from "we couldn't ask".
    """
    token = (creds or {}).get("access_token")
    ids = sorted({str(i) for i in listing_ids if i})
    if not token or not ids:
        if status is not None:
            status.update({"traffic_ok": False, "needs_reconnect": False})
        return {}
    cache_key = f"{token[-12:]}:{','.join(ids)}"
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _TTL:
        if status is not None:
            status.update(hit[2])
        return hit[1]

    out: dict[str, dict] = {}
    st = {"traffic_ok": True, "needs_reconnect": False}
    try:
        for lid, m in _traffic(token, ids).items():
            out.setdefault(lid, {}).update(m)
    except Exception as exc:  # noqa: BLE001 - missing scope / API blip
        st = {"traffic_ok": False,
              "needs_reconnect": bool(getattr(exc, "needs_reconnect", False))}
        log.warning("traffic metrics unavailable: %s", exc)
    watchers_ok = True
    offers_known: set = set()
    try:
        counts = _active_counts(token)
        for lid in ids:
            if lid in counts:
                out.setdefault(lid, {})["watchers"] = counts[lid]["watchers"]
        # Same sweep, second question — see _offers. Its own failure is its
        # own: a Best Offer lookup that times out must not blank the watch
        # counts that already came back in the call above.
        try:
            summaries, offers_known = _offers(token, counts, ids)
            for lid, summary in summaries.items():
                out.setdefault(lid, {}).update({
                    "offers": summary["count"],
                    "top_offer": summary["top"],
                    "offer_currency": summary["currency"],
                    "offer_expires_at": summary["expires_at"],
                })
        except Exception as exc:  # noqa: BLE001
            offers_known = set()
            log.info("pending offers unavailable: %s", exc)
    except Exception as exc:  # noqa: BLE001
        watchers_ok = False
        log.info("watch counts unavailable: %s", exc)

    result = {lid: m for lid, m in out.items() if m}
    # A listing nobody has looked at is MISSING from eBay's traffic report —
    # it lists what happened, not what didn't — and the same goes for the
    # watch counts. Left as absent, those listings showed no views row at all
    # while the ones eBay did mention showed "0 views", so a seller's grid
    # read as though the app knew about some listings and not others. It knew
    # about all of them: the report answered, and its answer for these is
    # nought. Filled in only where the call actually SUCCEEDED — a report that
    # could not be read stays blank everywhere rather than turning an outage
    # into a store with no traffic.
    for lid in ids:
        if st["traffic_ok"]:
            result.setdefault(lid, {}).setdefault("views", 0)
        if watchers_ok:
            result.setdefault(lid, {}).setdefault("watchers", 0)
        # Only where the answer is actually known — a listing past the lookup
        # cap, or one whose lookup failed, says nothing rather than "no
        # offers". See _offers.
        if lid in offers_known:
            result.setdefault(lid, {}).setdefault("offers", 0)
    if len(_CACHE) > 200:
        _CACHE.clear()
    _CACHE[cache_key] = (time.time(), result, st)
    if status is not None:
        status.update(st)
    return result

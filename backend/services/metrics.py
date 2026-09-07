"""eBay listing metrics — views/impressions (Sell Analytics) + watchers
(Trading API).

Best-effort and fail-soft: metrics are a nice-to-have overlay, never allowed to
break a page. Each source is fetched independently, so one failing (e.g. the
seller hasn't granted the analytics scope yet) doesn't sink the other. Results
are cached briefly so the dashboard's insights + the listing grid don't double
up the same eBay calls.

Views/impressions come from Sell Analytics getTrafficReport (needs the
sell.analytics.readonly scope), asked for 200 listings at a time because that
is all eBay's listing_ids filter takes. Watch counts come from the Trading
API's GetMyeBaySelling, paged over the active list (the Sell APIs don't expose
watchers).

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

# How far back the report reaches. eBay answers up to 90 days in one request,
# and Seller Hub's own "views" column counts a listing's whole life — so a
# 30-day window read low against both: a listing live since spring showed one
# month of its traffic under a label a seller reads as "how many people have
# looked at this". 90 is the most eBay will answer for.
_WINDOW_DAYS = 90

# eBay's listing_ids filter takes at most 200 ids per request. The report used
# to be asked for once, with `listing_ids[:200]` quietly dropping the rest —
# and then the nought-filling at the bottom of listing_metrics, which cannot
# tell "eBay said nothing about this listing" from "eBay was never asked about
# it", filled every dropped listing in as 0 views. Ids are sorted, and eBay's
# are ascending, so the effect was precise: the oldest 200 listings carried
# real numbers and everything newer read as traffic nobody had.
_ID_CHUNK = 200

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


def _traffic_page(token: str, listing_ids: list[str], start, end) -> dict[str, dict]:
    """One getTrafficReport call, for at most _ID_CHUNK listing ids."""
    filters = [
        "marketplace_ids:{%s}" % config.EBAY_MARKETPLACE_ID,
        f"date_range:[{start:%Y%m%d}..{end:%Y%m%d}]",
        "listing_ids:{%s}" % "|".join(listing_ids),
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


def _traffic(token: str, listing_ids: list[str],
             covered: Optional[set] = None) -> dict[str, dict]:
    """views + impressions per listing over the report window, via Sell
    Analytics getTrafficReport. {listing_id: {'views': n, 'impressions': n}}.

    eBay answers for at most _ID_CHUNK ids at a time, so a store with more live
    listings than that is asked in several passes and the answers merged.

    `covered` (an out-parameter) collects the ids eBay was successfully ASKED
    about — which is not the same set as the ids it answered with, and not the
    same set as the ids passed in. Only a listing eBay was asked about can be
    read as one nobody looked at; a listing in a pass that failed is one
    nothing is known about. Keeping the two apart is the whole point of
    reporting it rather than inferring it from the ids we sent.
    """
    if not listing_ids:
        return {}
    # Yesterday, not today. eBay rejects the whole report — "Neither the start
    # date nor the end date can be in the future" — for any end date it
    # considers future, and it judges that in Pacific time while the app runs
    # in UTC: for the seven hours after UTC midnight, "today" here is still
    # tomorrow there. The report only holds data through yesterday anyway, so
    # stepping back a day costs nothing and is safe in every timezone.
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=_WINDOW_DAYS)
    out: dict[str, dict] = {}
    asked: set[str] = set()
    failure: Optional[TrafficUnavailable] = None
    for i in range(0, len(listing_ids), _ID_CHUNK):
        chunk = listing_ids[i:i + _ID_CHUNK]
        try:
            out.update(_traffic_page(token, chunk, start, end))
        except TrafficUnavailable as exc:
            # A refusal the seller has to fix — a token without the analytics
            # scope — will refuse every remaining pass the same way. Stop,
            # rather than spend a dozen more round trips learning that.
            if exc.needs_reconnect:
                raise
            failure = exc
            log.info("traffic report pass failed (%d ids): %s", len(chunk), exc)
            continue
        asked.update(chunk)
    # Every pass failed: that is a report that could not be read, not a store
    # nobody has visited. Say so, so the zeros never get filled in.
    if failure is not None and not asked:
        raise failure
    if covered is not None:
        covered.update(asked)
    return out


def _watchers(token: str, status: Optional[dict] = None) -> dict[str, int]:
    """watch count per active listing ({item_id: watchers}). The Sell APIs
    don't expose watchers, so this goes through the Trading API — via
    ebay_trading so the endpoint honors EBAY_ENV (the old inline call
    hardcoded production) and errors surface with the shared handling.

    `status` (out) takes {'complete': bool}: whether the walk reached the end
    of the account's active listings. A walk that stopped short says nothing
    about the listings past where it stopped, and "not reached" is not
    "nobody is watching it"."""
    return ebay_trading.watch_counts(token, status=status)


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
    # The ids eBay actually answered a report for. Not `ids`: a store bigger
    # than one request is asked in several passes, and a pass that failed
    # leaves its listings unknown rather than idle.
    covered: set[str] = set()
    try:
        for lid, m in _traffic(token, ids, covered).items():
            out.setdefault(lid, {}).update(m)
    except Exception as exc:  # noqa: BLE001 - missing scope / API blip
        st = {"traffic_ok": False,
              "needs_reconnect": bool(getattr(exc, "needs_reconnect", False))}
        covered = set()
        log.warning("traffic metrics unavailable: %s", exc)
    watchers_ok = True
    wst: dict = {}
    try:
        watch = _watchers(token, wst)
        for lid in ids:
            if lid in watch:
                out.setdefault(lid, {})["watchers"] = watch[lid]
    except Exception as exc:  # noqa: BLE001
        watchers_ok = False
        log.info("watch counts unavailable: %s", exc)
    # The walk over the account's active listings is bounded, so a very large
    # store can run out of pages before it runs out of listings. The ones it
    # never reached are unknown, not unwatched. `complete` missing means the
    # call didn't report — read as complete, which is what it was before
    # anyone asked.
    watchers_complete = watchers_ok and bool(wst.get("complete", True))

    result = {lid: m for lid, m in out.items() if m}
    # A listing nobody has looked at is MISSING from eBay's traffic report —
    # it lists what happened, not what didn't — and the same goes for the
    # watch counts. Left as absent, those listings showed no views row at all
    # while the ones eBay did mention showed "0 views", so a seller's grid
    # read as though the app knew about some listings and not others. It knew
    # about all of them: the report answered, and its answer for these is
    # nought. Filled in only where the call actually SUCCEEDED — a report that
    # could not be read stays blank everywhere rather than turning an outage
    # into a store with no traffic — and, since a big store takes several
    # passes, filled per listing rather than for the store as a whole.
    for lid in ids:
        if lid in covered:
            result.setdefault(lid, {}).setdefault("views", 0)
        if watchers_complete:
            result.setdefault(lid, {}).setdefault("watchers", 0)
    if len(_CACHE) > 200:
        _CACHE.clear()
    _CACHE[cache_key] = (time.time(), result, st)
    if status is not None:
        status.update(st)
    return result

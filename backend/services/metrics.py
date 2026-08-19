"""eBay listing metrics — views/impressions (Sell Analytics) + watchers
(Trading API).

Best-effort and fail-soft: metrics are a nice-to-have overlay, never allowed to
break a page. Each source is fetched independently, so one failing (e.g. the
seller hasn't granted the analytics scope yet) doesn't sink the other. Results
are cached briefly so the dashboard's insights + the listing grid don't double
up the same eBay calls.

Views/impressions come from Sell Analytics getTrafficReport (needs the
sell.analytics.readonly scope). Watch counts come from the Trading API's
GetMyeBaySelling in a single call (the Sell APIs don't expose watchers).

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
    end = datetime.now(timezone.utc).date()
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


def _watchers(token: str) -> dict[str, int]:
    """watch count per active listing ({item_id: watchers}). The Sell APIs
    don't expose watchers, so this goes through the Trading API — via
    ebay_trading so the endpoint honors EBAY_ENV (the old inline call
    hardcoded production) and errors surface with the shared handling."""
    return ebay_trading.watch_counts(token)


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
    try:
        watch = _watchers(token)
        for lid in ids:
            if lid in watch:
                out.setdefault(lid, {})["watchers"] = watch[lid]
    except Exception as exc:  # noqa: BLE001
        log.info("watch counts unavailable: %s", exc)

    result = {lid: m for lid, m in out.items() if m}
    if len(_CACHE) > 200:
        _CACHE.clear()
    _CACHE[cache_key] = (time.time(), result, st)
    if status is not None:
        status.update(st)
    return result

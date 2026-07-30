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
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from .. import config

log = logging.getLogger("thryft.metrics")

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 120  # seconds — enough to dedupe the insights + grid fetches


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
            "metric": "LISTING_IMPRESSION_TOTAL,LISTING_VIEWS_TOTAL",
            "filter": ",".join(filters),
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"traffic_report {r.status_code}: {r.text[:160]}")
    data = r.json()
    keys = [k if isinstance(k, str) else k.get("key")
            for k in (data.get("header", {}) or {}).get("metricKeys", [])]
    out: dict[str, dict] = {}
    for rec in data.get("records", []) or []:
        dims = rec.get("dimensionValues") or []
        lid = str((dims[0] or {}).get("value")) if dims else ""
        if not lid:
            continue
        vals = rec.get("metricValues") or []
        m: dict[str, int] = {}
        for i, k in enumerate(keys):
            raw = vals[i].get("value") if i < len(vals) else None
            try:
                v = int(float(raw)) if raw is not None else 0
            except (TypeError, ValueError):
                v = 0
            if k == "LISTING_VIEWS_TOTAL":
                m["views"] = v
            elif k == "LISTING_IMPRESSION_TOTAL":
                m["impressions"] = v
        out[lid] = m
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # strip the XML namespace


def _watchers(token: str) -> dict[str, int]:
    """watch count per active listing, via Trading GetMyeBaySelling (one call).
    {item_id: watchers}. The Sell APIs don't expose watchers, so this uses the
    legacy Trading API with the OAuth token (IAF header)."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        '<ActiveList><Include>true</Include>'
        '<Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>1</PageNumber></Pagination>'
        '</ActiveList><DetailLevel>ReturnAll</DetailLevel>'
        '</GetMyeBaySellingRequest>'
    )
    r = httpx.post(
        "https://api.ebay.com/ws/api.dll",
        headers={
            "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": "1193",
            "X-EBAY-API-IAF-TOKEN": token,
            "Content-Type": "text/xml",
        },
        content=body.encode("utf-8"),
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GetMyeBaySelling {r.status_code}")
    out: dict[str, int] = {}
    root = ET.fromstring(r.text)
    for item in root.iter():
        if _local(item.tag) != "Item":
            continue
        iid, watch = "", 0
        for child in item:
            lt = _local(child.tag)
            if lt == "ItemID":
                iid = (child.text or "").strip()
            elif lt == "WatchCount":
                try:
                    watch = int(child.text or 0)
                except (TypeError, ValueError):
                    watch = 0
        if iid:
            out[iid] = watch
    return out


def listing_metrics(creds: Optional[dict], listing_ids: list[str]) -> dict[str, dict]:
    """Combined {listing_id: {views, impressions, watchers}} for the given eBay
    listing ids. Best-effort per source; returns {} if nothing was fetched.
    Cached for a short window keyed by the token + id set."""
    token = (creds or {}).get("access_token")
    ids = sorted({str(i) for i in listing_ids if i})
    if not token or not ids:
        return {}
    cache_key = f"{token[-12:]}:{','.join(ids)}"
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]

    out: dict[str, dict] = {}
    try:
        for lid, m in _traffic(token, ids).items():
            out.setdefault(lid, {}).update(m)
    except Exception as exc:  # noqa: BLE001 - missing scope / API blip
        log.info("traffic metrics unavailable: %s", exc)
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
    _CACHE[cache_key] = (time.time(), result)
    return result

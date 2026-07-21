"""eBay Promoted Listings Standard — promote a just-published live listing.

Promoted Listings Standard uses a COST_PER_SALE funding model: the seller picks
an ad rate (a percentage) and eBay only charges that percentage of the sale
price when the item actually sells through the promotion. We keep ONE
app-managed campaign per marketplace and attach each promoted listing to it as
an ad at the chosen rate (updating the bid if the ad already exists, e.g. on a
re-publish/revise).

Everything here is best-effort and fail-soft: a promotion problem must NEVER
fail a publish — the listing is already live by the time we get here. When the
connected token lacks the sell.marketing scope (a seller who linked eBay before
that scope existed), we surface a clear "reconnect" status instead.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .. import config
from ..models import Listing
from . import ebay

log = logging.getLogger("thryft.promotions")

CAMPAIGN_NAME = "Thryft Shop Promoted Listings"
_MARKETING = "/sell/marketing/v1"

# Resolve the campaign id once per (marketplace, token) instead of listing
# campaigns on every publish. Keyed by a short, non-sensitive token suffix.
_CAMPAIGN_CACHE: dict[str, str] = {}


class _ScopeError(Exception):
    """The token can't use the Marketing API — the seller must reconnect."""


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Content-Language": "en-US",
    }


def _is_scope_error(resp: httpx.Response) -> bool:
    if resp.status_code in (401, 403):
        return True
    body = resp.text.lower()
    return ("insufficient" in body and "scope" in body) or "access_denied" in body


def _ensure_campaign(client: httpx.Client, base: str, token: str, marketplace: str) -> str:
    cache_key = f"{marketplace}:{token[-12:]}"
    if cache_key in _CAMPAIGN_CACHE:
        return _CAMPAIGN_CACHE[cache_key]
    # Reuse our campaign if it already exists on this account.
    r = client.get(f"{base}{_MARKETING}/ad_campaign", headers=_headers(token),
                   params={"campaign_name": CAMPAIGN_NAME})
    if r.status_code == 200:
        for c in (r.json().get("campaigns") or []):
            if c.get("campaignName") == CAMPAIGN_NAME and c.get("campaignId"):
                _CAMPAIGN_CACHE[cache_key] = c["campaignId"]
                return c["campaignId"]
    elif _is_scope_error(r):
        raise _ScopeError()
    # Otherwise create it (COST_PER_SALE = pay only when it sells).
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {
        "campaignName": CAMPAIGN_NAME,
        "marketplaceId": marketplace,
        "fundingStrategy": {"fundingModel": "COST_PER_SALE"},
        "startDate": now,
    }
    r = client.post(f"{base}{_MARKETING}/ad_campaign", headers=_headers(token), json=body)
    if r.status_code in (200, 201):
        # Create returns the id in the Location header (or body on some paths).
        loc = r.headers.get("Location", "")
        cid = loc.rstrip("/").split("/")[-1] if loc else (r.json() or {}).get("campaignId", "")
        if cid:
            _CAMPAIGN_CACHE[cache_key] = cid
            return cid
    if _is_scope_error(r):
        raise _ScopeError()
    raise RuntimeError(f"campaign create failed ({r.status_code}): {r.text[:200]}")


def _create_or_update_ad(client: httpx.Client, base: str, token: str,
                         campaign_id: str, sku: str, rate: float) -> dict:
    payload = {
        "bidPercentage": f"{rate:.1f}",
        "inventoryReferenceId": sku,
        "inventoryReferenceType": "INVENTORY_ITEM",
    }
    r = client.post(
        f"{base}{_MARKETING}/ad_campaign/{campaign_id}/create_ads_by_inventory_reference",
        headers=_headers(token), json=payload)
    if r.status_code in (200, 201, 207):
        return {"ok": True}
    if _is_scope_error(r):
        raise _ScopeError()
    # Ad already exists (re-publish) → update its bid to the new rate instead.
    if r.status_code == 409 or "already" in r.text.lower():
        u = client.post(
            f"{base}{_MARKETING}/ad_campaign/{campaign_id}"
            "/bulk_update_ads_bid_by_inventory_reference",
            headers=_headers(token), json={"requests": [payload]})
        if u.status_code in (200, 207):
            return {"ok": True, "updated": True}
        return {"ok": False, "detail": u.text[:300]}
    return {"ok": False, "detail": r.text[:300]}


def promote_listing(session_id: str, listing: Listing, creds: dict | None) -> dict:
    """Best-effort promote a just-published live listing. Returns a status dict:
    {promoted, ad_rate, needs_reconnect, message}. Never raises."""
    rate = round(float(listing.ad_rate_percent or 0), 1)
    if not listing.promote or rate <= 0:
        return {"promoted": False}
    token = (creds or {}).get("access_token")
    if not token:
        return {"promoted": False, "needs_reconnect": True,
                "message": "Connect your eBay account to run promotions."}
    base = config.EBAY_API_BASE
    marketplace = config.EBAY_MARKETPLACE_ID
    sku = ebay._sku(session_id, listing)
    try:
        with httpx.Client(timeout=30) as client:
            campaign_id = _ensure_campaign(client, base, token, marketplace)
            res = _create_or_update_ad(client, base, token, campaign_id, sku, rate)
    except _ScopeError:
        return {"promoted": False, "needs_reconnect": True,
                "message": "Reconnect your eBay account to grant ad permissions, "
                           "then republish to start the promotion."}
    except Exception as exc:  # noqa: BLE001 - promotion must never break publish
        log.warning("promote failed (session=%s): %s", session_id, exc)
        return {"promoted": False, "message": f"Couldn't start the promotion: {exc}"}
    if res.get("ok"):
        log.info("promoted session=%s at %.1f%%", session_id, rate)
        return {"promoted": True, "ad_rate": rate,
                "message": f"Promoted at {rate:.1f}% — you're only charged that if it "
                           "sells through the promotion."}
    return {"promoted": False,
            "message": "eBay couldn't start the promotion: "
                       + (res.get("detail") or "unknown error")}

"""eBay Sell (Inventory) API integration.

Publishing flow:
  1. createOrReplaceInventoryItem  (the product + condition + images)
  2. createOffer                   (price, policies, marketplace) -> the DRAFT
  3. publishOffer                  (only for "live"; turns the offer into a
                                    live listing)

When credentials are not configured (see config.ebay_ready), every call is a
"dry run": the exact payloads are returned and saved to data/exports/ so you
can inspect them and push later once you have a developer account.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

from .. import config, storage
from ..models import Listing


def _sku(session_id: str, listing: Listing) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", (listing.brand or "item")).strip("-")
    return f"{base[:20]}-{session_id}".upper()


def _image_urls(session_id: str, base_url: str) -> list[str]:
    """eBay requires publicly reachable image URLs.

    We expose optimized images via the app; base_url is the public origin the
    UI was loaded from. In dry-run mode these are informational.
    """
    names = storage.list_optimized(session_id)
    return [f"{base_url}/media/{session_id}/optimized/{n}" for n in names]


def build_inventory_item(session_id: str, listing: Listing, base_url: str) -> dict:
    aspects: dict[str, list[str]] = {}
    if listing.brand:
        aspects["Brand"] = [listing.brand]
    for spec in listing.item_specifics:
        if spec.name and spec.value:
            aspects.setdefault(spec.name, [])
            if spec.value not in aspects[spec.name]:
                aspects[spec.name].append(spec.value)

    return {
        "sku": _sku(session_id, listing),
        "availability": {
            "shipToLocationAvailability": {"quantity": max(1, listing.quantity)}
        },
        "condition": listing.condition,
        "conditionDescription": listing.condition_description or None,
        "product": {
            "title": listing.title[:80],
            "description": listing.description or listing.title,
            "aspects": aspects,
            "imageUrls": _image_urls(session_id, base_url),
            "brand": listing.brand or None,
        },
    }


def build_offer(session_id: str, listing: Listing) -> dict:
    price = listing.price if listing.price is not None else 0.0
    return {
        "sku": _sku(session_id, listing),
        "marketplaceId": config.EBAY_MARKETPLACE_ID,
        "format": "FIXED_PRICE",
        "availableQuantity": max(1, listing.quantity),
        "categoryId": listing.category_id or None,
        "listingDescription": listing.description or listing.title,
        "pricingSummary": {
            "price": {
                "value": f"{price:.2f}",
                "currency": listing.currency or config.EBAY_CURRENCY,
            }
        },
        "listingPolicies": {
            "fulfillmentPolicyId": config.EBAY_FULFILLMENT_POLICY_ID or None,
            "paymentPolicyId": config.EBAY_PAYMENT_POLICY_ID or None,
            "returnPolicyId": config.EBAY_RETURN_POLICY_ID or None,
        },
        "merchantLocationKey": config.EBAY_MERCHANT_LOCATION_KEY or None,
    }


# --- live API calls --------------------------------------------------------

def _access_token() -> str:
    if config.EBAY_OAUTH_TOKEN:
        return config.EBAY_OAUTH_TOKEN
    # Refresh-token grant.
    resp = httpx.post(
        f"{config.EBAY_API_BASE}/identity/v1/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": config.EBAY_REFRESH_TOKEN,
            "scope": "https://api.ebay.com/oauth/api_scope/sell.inventory",
        },
        auth=(config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
        "Accept": "application/json",
    }


def _push_live(session_id: str, listing: Listing, mode: str, base_url: str) -> dict:
    token = _access_token()
    sku = _sku(session_id, listing)
    item = build_inventory_item(session_id, listing, base_url)
    offer = build_offer(session_id, listing)
    base = config.EBAY_API_BASE
    steps: list[dict] = []

    with httpx.Client(timeout=60) as client:
        r1 = client.put(
            f"{base}/sell/inventory/v1/inventory_item/{sku}",
            headers=_headers(token),
            json=item,
        )
        steps.append({"step": "createOrReplaceInventoryItem", "status": r1.status_code})
        r1.raise_for_status()

        r2 = client.post(
            f"{base}/sell/inventory/v1/offer",
            headers=_headers(token),
            json=offer,
        )
        steps.append({"step": "createOffer", "status": r2.status_code, "body": r2.json()})
        r2.raise_for_status()
        offer_id = r2.json().get("offerId")

        published = False
        listing_id = None
        if mode == "live":
            r3 = client.post(
                f"{base}/sell/inventory/v1/offer/{offer_id}/publish",
                headers=_headers(token),
            )
            steps.append({"step": "publishOffer", "status": r3.status_code, "body": r3.json()})
            r3.raise_for_status()
            published = True
            listing_id = r3.json().get("listingId")

    return {
        "dry_run": False,
        "mode": mode,
        "sku": sku,
        "offer_id": offer_id,
        "published": published,
        "listing_id": listing_id,
        "steps": steps,
    }


def publish(session_id: str, listing: Listing, mode: str, base_url: str) -> dict:
    """Push to eBay, or dry-run if not configured.

    mode: "draft" (create unpublished offer) or "live" (also publishOffer).
    """
    item = build_inventory_item(session_id, listing, base_url)
    offer = build_offer(session_id, listing)
    payload = {"inventory_item": item, "offer": offer, "mode": mode}
    export_path = storage.write_export(session_id, "ebay_payload", payload)

    if not config.ebay_ready():
        return {
            "dry_run": True,
            "mode": mode,
            "message": (
                "eBay credentials not configured - generated the API payload "
                "instead of publishing. Add credentials to .env to go live."
            ),
            "export_path": str(export_path),
            "payload": payload,
        }

    try:
        result = _push_live(session_id, listing, mode, base_url)
        result["export_path"] = str(export_path)
        return result
    except httpx.HTTPStatusError as exc:
        return {
            "dry_run": False,
            "error": True,
            "mode": mode,
            "message": f"eBay API error: {exc.response.status_code}",
            "detail": exc.response.text,
            "export_path": str(export_path),
        }

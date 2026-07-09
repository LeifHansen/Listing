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

from .. import config, objstore, storage
from ..models import Listing


def _prune(value):
    """Recursively drop None values and empty dicts.

    eBay's API rejects explicit nulls for optional fields (e.g. categoryId,
    conditionDescription), so we omit them rather than send `null`.
    """
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            pruned = _prune(val)
            if pruned is None or (isinstance(pruned, dict) and not pruned):
                continue
            out[key] = pruned
        return out
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def _sku(session_id: str, listing: Listing) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", (listing.brand or "item")).strip("-")
    return f"{base[:20]}-{session_id}".upper()


def _image_urls(session_id: str, names: list[str], base_url: str) -> list[str]:
    """eBay requires publicly reachable image URLs.

    Prefer durable R2 public URLs when object storage is configured; otherwise
    fall back to serving via the app (needs a public deployment).
    """
    names = names or storage.list_optimized(session_id)
    if objstore.enabled():
        return [objstore.public_url(objstore.key_for(session_id, n)) for n in names]
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

    return _prune({
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
            "imageUrls": _image_urls(session_id, listing.images, base_url),
            "brand": listing.brand or None,
        },
    })


def build_offer(session_id: str, listing: Listing, creds: Optional[dict] = None) -> dict:
    price = listing.price if listing.price is not None else 0.0
    c = creds or {}
    return _prune({
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
            "fulfillmentPolicyId": c.get("fulfillment_policy_id") or config.EBAY_FULFILLMENT_POLICY_ID or None,
            "paymentPolicyId": c.get("payment_policy_id") or config.EBAY_PAYMENT_POLICY_ID or None,
            "returnPolicyId": c.get("return_policy_id") or config.EBAY_RETURN_POLICY_ID or None,
        },
        "merchantLocationKey": c.get("merchant_location_key") or config.EBAY_MERCHANT_LOCATION_KEY or None,
    })


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


def _body(resp: httpx.Response) -> dict:
    """eBay error responses aren't always JSON; never let parsing crash us."""
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:500]}


# updateOffer (PUT) rejects these immutable keys that createOffer (POST) needs.
_OFFER_IMMUTABLE = ("sku", "marketplaceId", "format")


def _existing_offer_id(client, base: str, token: str, sku: str) -> Optional[str]:
    """Return the existing offerId for this SKU, or None. Republishing the same
    session reuses the SKU, so we must update that offer rather than create a
    duplicate (eBay error 25002 'Offer entity already exists')."""
    try:
        r = client.get(
            f"{base}/sell/inventory/v1/offer",
            headers=_headers(token),
            params={"sku": sku, "marketplace_id": config.EBAY_MARKETPLACE_ID,
                    "format": "FIXED_PRICE"},
        )
        if r.status_code == 200:
            offers = r.json().get("offers", []) or []
            if offers:
                return offers[0].get("offerId")
    except Exception:  # noqa: BLE001 - treat as "no existing offer"
        pass
    return None


def _push_live(session_id: str, listing: Listing, mode: str, base_url: str,
               creds: Optional[dict] = None) -> dict:
    token = (creds or {}).get("access_token") or _access_token()
    sku = _sku(session_id, listing)
    item = build_inventory_item(session_id, listing, base_url)
    offer = build_offer(session_id, listing, creds)
    base = config.EBAY_API_BASE
    steps: list[dict] = []

    try:
        with httpx.Client(timeout=60) as client:
            r1 = client.put(
                f"{base}/sell/inventory/v1/inventory_item/{sku}",
                headers=_headers(token),
                json=item,
            )
            steps.append({"step": "createOrReplaceInventoryItem", "status": r1.status_code,
                          "body": None if r1.is_success else _body(r1)})
            r1.raise_for_status()

            # Idempotent offer: update the existing one for this SKU, else create.
            offer_id = _existing_offer_id(client, base, token, sku)
            if offer_id:
                update_body = {k: v for k, v in offer.items() if k not in _OFFER_IMMUTABLE}
                r2 = client.put(
                    f"{base}/sell/inventory/v1/offer/{offer_id}",
                    headers=_headers(token),
                    json=update_body,
                )
                # updateOffer returns 204 No Content on success.
                r2_body = _body(r2) if r2.content else {}
                steps.append({"step": "updateOffer", "status": r2.status_code,
                              "offerId": offer_id, "body": r2_body})
                r2.raise_for_status()
            else:
                r2 = client.post(
                    f"{base}/sell/inventory/v1/offer",
                    headers=_headers(token),
                    json=offer,
                )
                r2_body = _body(r2)
                steps.append({"step": "createOffer", "status": r2.status_code, "body": r2_body})
                r2.raise_for_status()
                offer_id = r2_body.get("offerId")
            if not offer_id:
                # No offerId to publish against — stop with a clear error rather
                # than POSTing to /offer/None/publish.
                return {
                    "dry_run": False,
                    "error": True,
                    "mode": mode,
                    "message": "eBay offer create/update returned no offerId.",
                    "detail": str(r2_body),
                    "steps": steps,
                }

            published = False
            listing_id = None
            if mode == "live":
                r3 = client.post(
                    f"{base}/sell/inventory/v1/offer/{offer_id}/publish",
                    headers=_headers(token),
                )
                r3_body = _body(r3)
                steps.append({"step": "publishOffer", "status": r3.status_code, "body": r3_body})
                r3.raise_for_status()
                published = True
                listing_id = r3_body.get("listingId")
    except httpx.HTTPStatusError as exc:
        failed = steps[-1]["step"] if steps else "authentication"
        return {
            "dry_run": False,
            "error": True,
            "mode": mode,
            "message": f"eBay API error {exc.response.status_code} during {failed}",
            "detail": exc.response.text,
            "steps": steps,
        }

    return {
        "dry_run": False,
        "mode": mode,
        "sku": sku,
        "offer_id": offer_id,
        "published": published,
        "listing_id": listing_id,
        "steps": steps,
    }


def publish(session_id: str, listing: Listing, mode: str, base_url: str,
            creds: Optional[dict] = None) -> dict:
    """Push to eBay, or dry-run if not configured.

    creds: a connected user's eBay credentials (access_token + policy ids +
    location). When present we publish with those; otherwise we fall back to
    env config, and dry-run if neither is available.
    mode: "draft" (create unpublished offer) or "live" (also publishOffer).
    """
    item = build_inventory_item(session_id, listing, base_url)
    offer = build_offer(session_id, listing, creds)
    payload = {"inventory_item": item, "offer": offer, "mode": mode}
    export_path = storage.write_export(session_id, "ebay_payload", payload)

    ready = bool((creds or {}).get("access_token")) or config.ebay_ready()
    if not ready:
        return {
            "dry_run": True,
            "mode": mode,
            "message": (
                "eBay not connected - generated the API payload instead of "
                "publishing. Connect eBay (or add credentials) to go live."
            ),
            "export_path": str(export_path),
            "payload": payload,
        }

    # eBay fetches every image URL when the inventory item is created; if the
    # local files are gone (session predates a restart/deploy) it fails with
    # an opaque 25001 'system error'. Fail clearly instead.
    if not objstore.enabled():
        names = listing.images or storage.list_optimized(session_id)
        opt_dir = storage.optimized_dir(session_id)
        if not names or any(not (opt_dir / n).is_file() for n in names):
            return {
                "dry_run": False,
                "error": True,
                "mode": mode,
                "message": (
                    "This listing's photos are no longer on the server, so eBay "
                    "can't fetch them. Please re-upload the photos and try again."
                ),
                "export_path": str(export_path),
            }

    try:
        result = _push_live(session_id, listing, mode, base_url, creds)
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

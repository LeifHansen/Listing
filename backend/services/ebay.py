"""eBay Sell (Inventory) API integration.

Publishing flow (live only — drafts stay in QuickFlip and never touch eBay):
  1. createOrReplaceInventoryItem  (the product + condition + images)
  2. createOffer / updateOffer     (price, policies, marketplace, location)
  3. publishOffer                  (turns the offer into a live listing)

When credentials are not configured (see config.ebay_ready), every call is a
"dry run": the exact payloads are returned and saved to data/exports/ so you
can inspect them and push later once you have a developer account.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from .. import config, ebay_errors, objstore, storage
from ..config import log
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
        return [p for p in (_prune(v) for v in value) if p is not None]
    return value


def _sku(session_id: str, listing: Listing) -> str:
    # Must be STABLE for the life of the listing: republishing the same session
    # reuses the SKU so we update the existing offer instead of creating a
    # duplicate. Deriving it from mutable fields (e.g. brand) would mint a new
    # SKU whenever the user edits that field and silently create a second live
    # listing — so key it on the immutable session id alone.
    return f"THRYFT-{session_id}".upper()


def _image_urls(session_id: str, names: list[str], base_url: str) -> list[str]:
    """eBay requires publicly reachable image URLs.

    Prefer durable R2 public URLs when object storage is configured; otherwise
    fall back to serving via the app (needs a public deployment).
    """
    names = names or storage.list_optimized(session_id)
    if objstore.enabled():
        return [objstore.public_url(objstore.key_for(session_id, n)) for n in names]
    return [f"{base_url}/media/{session_id}/optimized/{n}" for n in names]


def _package_weight_and_size(listing: Listing) -> dict:
    """Build eBay's packageWeightAndSize. Publishing requires a valid weight;
    dimensions are optional and only included when all three are set."""
    total_lb = round((listing.package_weight_lb or 0)
                     + (listing.package_weight_oz or 0) / 16.0, 2)
    pkg: dict = {}
    if total_lb > 0:
        pkg["weight"] = {"value": total_lb, "unit": "POUND"}
    dims = (listing.package_length_in, listing.package_width_in,
            listing.package_height_in)
    if all(d and d > 0 for d in dims):
        pkg["dimensions"] = {
            "length": round(listing.package_length_in, 2),
            "width": round(listing.package_width_in, 2),
            "height": round(listing.package_height_in, 2),
            "unit": "INCH",
        }
    return {"packageWeightAndSize": pkg} if pkg else {}


# Product identifiers live in dedicated product.* fields, not as free aspects.
_IDENTIFIER_KEYS = {"upc": "upc", "ean": "ean", "isbn": "isbn"}


def build_inventory_item(session_id: str, listing: Listing, base_url: str) -> dict:
    aspects: dict[str, list[str]] = {}
    identifiers: dict[str, str] = {}
    if listing.brand:
        aspects["Brand"] = [listing.brand]
    for spec in listing.item_specifics:
        if not (spec.name and spec.value):
            continue
        key = spec.name.strip().lower()
        # Route UPC/EAN/ISBN to the canonical product fields rather than aspects.
        if key in _IDENTIFIER_KEYS:
            identifiers.setdefault(_IDENTIFIER_KEYS[key], spec.value.strip())
            continue
        # eBay allows exactly ONE Brand value (error 25002 "Brand should
        # contain only one value"). The AI often emits Brand as an item
        # specific too — sometimes with different casing/spelling — which used
        # to produce Brand: [x, y] and block the publish. listing.brand wins.
        if key == "brand":
            if not aspects.get("Brand"):
                aspects["Brand"] = [spec.value.strip()]
            continue
        aspects.setdefault(spec.name, [])
        if spec.value not in aspects[spec.name]:
            aspects[spec.name].append(spec.value)

    # eBay validates brand and MPN as a pair ('Input data for tag <BrandMPN>
    # is invalid or missing'): once a brand is present, an MPN must be too.
    # Use the seller's MPN item specific when given, else eBay's official
    # "no part number" sentinel.
    brand = listing.brand or (aspects.get("Brand") or [""])[0]
    mpn = next((s.value for s in listing.item_specifics
                if s.name and s.value and s.name.strip().lower()
                in ("mpn", "manufacturer part number")), "")
    if brand and not mpn:
        mpn = "Does Not Apply"

    product = {
        "title": listing.title[:80],
        "description": listing.description or listing.title,
        "aspects": aspects,
        "imageUrls": _image_urls(session_id, listing.images, base_url),
        "brand": brand or None,
        "mpn": (mpn if brand else None) or None,
    }
    # eBay expects identifiers as arrays; "Does Not Apply" is the accepted
    # sentinel for items without one.
    for field, value in identifiers.items():
        product[field] = [value]

    return _prune({
        **_package_weight_and_size(listing),
        "sku": _sku(session_id, listing),
        "availability": {
            "shipToLocationAvailability": {"quantity": max(1, listing.quantity)}
        },
        "condition": listing.condition,
        "conditionDescription": listing.condition_description or None,
        "product": product,
    })


def build_offer(session_id: str, listing: Listing, creds: Optional[dict] = None) -> dict:
    price = listing.price if listing.price is not None else 0.0
    # A connected seller (creds present) must ONLY use their own policies and
    # location — never fall back to the deployment owner's env-configured IDs,
    # which belong to a different eBay account and would be rejected under the
    # user's token. Env fallbacks apply only to the no-user (env-config) path.
    if creds is not None:
        c = creds
        fulfillment = c.get("fulfillment_policy_id") or None
        payment = c.get("payment_policy_id") or None
        returns = c.get("return_policy_id") or None
        location = c.get("merchant_location_key") or None
    else:
        fulfillment = config.EBAY_FULFILLMENT_POLICY_ID or None
        payment = config.EBAY_PAYMENT_POLICY_ID or None
        returns = config.EBAY_RETURN_POLICY_ID or None
        location = config.EBAY_MERCHANT_LOCATION_KEY or None
    # A shipping service chosen on the listing overrides the account default.
    fulfillment = listing.fulfillment_policy_id or fulfillment
    currency = listing.currency or config.EBAY_CURRENCY
    best_offer = None
    if listing.best_offer_enabled:
        best_offer = {"bestOfferEnabled": True}
        if listing.best_offer_min and listing.best_offer_min > 0:
            best_offer["autoDeclinePrice"] = {
                "value": f"{listing.best_offer_min:.2f}", "currency": currency}
        if listing.best_offer_accept and listing.best_offer_accept > 0:
            best_offer["autoAcceptPrice"] = {
                "value": f"{listing.best_offer_accept:.2f}", "currency": currency}
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
            "fulfillmentPolicyId": fulfillment,
            "paymentPolicyId": payment,
            "returnPolicyId": returns,
            "bestOfferTerms": best_offer,
        },
        "merchantLocationKey": location,
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
    duplicate (eBay error 25002 'Offer entity already exists'). Queried by SKU
    only — extra marketplace/format filters can hide a match."""
    try:
        r = client.get(
            f"{base}/sell/inventory/v1/offer",
            headers=_headers(token),
            params={"sku": sku},
        )
        if r.status_code == 200:
            offers = r.json().get("offers", []) or []
            if offers:
                return offers[0].get("offerId")
        else:
            log.info("getOffers(sku=%s) -> %s %s", sku, r.status_code, _body(r))
    except Exception as exc:  # noqa: BLE001 - treat as "no existing offer"
        log.info("getOffers(sku=%s) failed: %s", sku, exc)
    return None


def _offer_id_from_error(body: dict) -> Optional[str]:
    """eBay's 'Offer entity already exists' (25002) error carries the existing
    offerId in its parameters — pull it out so we can update that offer."""
    for err in (body or {}).get("errors", []) or []:
        for p in err.get("parameters", []) or []:
            if str(p.get("name", "")).lower() == "offerid" and p.get("value"):
                return str(p["value"])
    return None


def create_seller_hub_draft(session_id: str, listing: Listing, base_url: str,
                            access_token: str) -> Optional[dict]:
    """Create a REAL eBay Seller Hub draft (Listing API createItemDraft) —
    the kind that shows up under Selling → Drafts on eBay itself.

    Returns {"item_draft_id", "message"} on success, or None when the token
    lacks the sell.item.draft scope / the API isn't enabled for this keyset,
    so the caller can fall back to staging an unpublished offer instead.
    """
    price = listing.price if listing.price is not None else None
    body = _prune({
        "product": {
            "title": listing.title[:80],
            "description": listing.description or listing.title,
            "imageUrls": _image_urls(session_id, listing.images, base_url),
            "brand": listing.brand or None,
            "aspects": {s.name: [s.value] for s in listing.item_specifics
                        if s.name and s.value} or None,
        },
        "categoryId": listing.category_id or None,
        "condition": listing.condition or None,
        "format": "FIXED_PRICE",
        "pricingSummary": ({"price": {
            "value": f"{price:.2f}",
            "currency": listing.currency or config.EBAY_CURRENCY,
        }} if price else None),
    })
    try:
        resp = httpx.post(
            f"{config.EBAY_API_BASE}/sell/listing/v1_beta/item_draft/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": config.EBAY_MARKETPLACE_ID,
            },
            json=body,
            timeout=30,
        )
    except httpx.RequestError as exc:
        log.warning("item_draft: network error: %s", exc)
        return None
    if resp.status_code in (200, 201):
        data = _body(resp)
        log.info("item_draft created: session=%s id=%s", session_id,
                 data.get("itemDraftId", ""))
        return {
            "item_draft_id": data.get("itemDraftId", ""),
            "message": ("Draft created on eBay! Find it under Selling → Drafts "
                        "in Seller Hub (or the eBay app) to finish or publish "
                        "it there — or Publish Live from here anytime."),
        }
    # 401/403: token lacks the sell.item.draft scope (connections made before
    # the scope was added) or the beta API isn't enabled for this keyset.
    log.info("item_draft unavailable (%s): %s", resp.status_code, resp.text[:300])
    return None


def _push_live(session_id: str, listing: Listing, mode: str, base_url: str,
               creds: Optional[dict] = None, do_publish: bool = True) -> dict:
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

            # Idempotent offer: update the existing one for this SKU, else
            # create. If we couldn't find it but createOffer says it already
            # exists, recover the offerId from that error and update instead.
            def _update_offer(oid: str):
                update_body = {k: v for k, v in offer.items() if k not in _OFFER_IMMUTABLE}
                rr = client.put(
                    f"{base}/sell/inventory/v1/offer/{oid}",
                    headers=_headers(token),
                    json=update_body,
                )
                steps.append({"step": "updateOffer", "status": rr.status_code,
                              "offerId": oid, "body": _body(rr) if rr.content else {}})
                rr.raise_for_status()

            offer_id = _existing_offer_id(client, base, token, sku)
            if offer_id:
                _update_offer(offer_id)
            else:
                r2 = client.post(
                    f"{base}/sell/inventory/v1/offer",
                    headers=_headers(token),
                    json=offer,
                )
                r2_body = _body(r2)
                steps.append({"step": "createOffer", "status": r2.status_code, "body": r2_body})
                if not r2.is_success:
                    recovered = _offer_id_from_error(r2_body)
                    if recovered:
                        log.info("createOffer said offer exists; updating %s (sku=%s)", recovered, sku)
                        offer_id = recovered
                        _update_offer(offer_id)
                    else:
                        r2.raise_for_status()
                else:
                    offer_id = r2_body.get("offerId")
            if not offer_id:
                # No offerId to publish against — stop with a clear error rather
                # than POSTing to /offer/None/publish.
                log.error("publish: offer create/update returned no offerId (sku=%s): %s", sku, r2_body)
                return {
                    "dry_run": False,
                    "error": True,
                    "mode": mode,
                    "message": "eBay didn’t return an offer id — try publishing again.",
                    "issues": [{"target": "generic", "title": "eBay hiccup creating the offer",
                                "fix": "Press Publish Live again."}],
                    "detail": str(r2_body),
                    "steps": steps,
                }

            # Draft mode stops here: the inventory item + unpublished offer now
            # live on eBay (ready to publish), but we don't call publishOffer.
            published = False
            listing_id = None
            if do_publish:
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
        status = exc.response.status_code
        issues = ebay_errors.from_response(exc.response.text)
        for it in issues:
            log.warning("publish %s failed (sku=%s): [%s] %s | fix: %s",
                        failed, sku, it.get("error_id", ""),
                        it.get("ebay_message") or it["title"], it["fix"])
        return {
            "dry_run": False,
            "error": True,
            "mode": mode,
            "step": failed,
            "message": ebay_errors.headline(issues, failed, status),
            "issues": issues,
            "detail": exc.response.text,
            "steps": steps,
        }
    except httpx.RequestError as exc:
        # Timeout / connection / DNS failure — no HTTP status. Don't 500: a
        # publishOffer that timed out may have SUCCEEDED on eBay, so tell the
        # user to check before retrying rather than blindly republishing.
        failed = steps[-1]["step"] if steps else "authentication"
        log.warning("publish %s network error (sku=%s): %s", failed, sku, exc)
        return {
            "dry_run": False,
            "error": True,
            "mode": mode,
            "step": failed,
            "message": "Couldn’t reach eBay just now — the connection timed out.",
            "issues": [{"target": "generic", "title": "eBay didn’t respond in time",
                        "fix": ("Check Selling → Active on eBay in a minute: if the "
                                "listing isn’t there, press Publish Live again.")}],
            "detail": str(exc),
            "steps": steps,
        }

    return {
        "dry_run": False,
        "mode": mode,
        "sku": sku,
        "offer_id": offer_id,
        "published": published,
        "ebay_draft": not published,  # unpublished offer created on eBay
        "message": (None if published else
                    "Draft staged on eBay as an unpublished offer (not visible "
                    "in Seller Hub). To get REAL Seller Hub drafts, disconnect "
                    "and reconnect your eBay account in Settings — that grants "
                    "the extra permission eBay needs."),
        "listing_id": listing_id,
        "steps": steps,
    }


def publish(session_id: str, listing: Listing, mode: str, base_url: str,
            creds: Optional[dict] = None) -> dict:
    """Push to eBay, or dry-run if not configured.

    creds: a connected user's eBay credentials (access_token + policy ids +
    location). When present we publish with those; otherwise we fall back to
    env config, and dry-run if neither is available.
    mode: "draft" saves in QuickFlip only (no eBay call); "live" publishes to eBay.
    """
    item = build_inventory_item(session_id, listing, base_url)
    offer = build_offer(session_id, listing, creds)
    payload = {"inventory_item": item, "offer": offer, "mode": mode}
    export_path = storage.write_export(session_id, "ebay_payload", payload)

    ready = bool((creds or {}).get("access_token")) or config.ebay_ready()
    if not ready:
        # No eBay connection: a draft stays in QuickFlip only (we can't reach
        # eBay), and a live publish returns the dry-run payload to inspect.
        if mode == "draft":
            return {
                "dry_run": False,
                "draft": True,
                "mode": mode,
                "message": ("Saved to your Thryft drafts — find it under Drafts. "
                            "It is NOT on eBay: connect your eBay account and press "
                            "Publish Live when you're ready to list it."),
                "export_path": str(export_path),
            }
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
    # an opaque 25001 'system error'. Applies to drafts too (both create the
    # inventory item). Fail clearly instead.
    if not objstore.enabled():
        names = listing.images or storage.list_optimized(session_id)
        opt_dir = storage.optimized_dir(session_id)
        if not names or any(not (opt_dir / n).is_file() for n in names):
            log.warning("publish blocked: photos missing for session %s", session_id)
            return {
                "dry_run": False,
                "error": True,
                "mode": mode,
                "message": "This listing’s photos aren’t on the server anymore.",
                "issues": [{"target": "photos", "title": "Photos are missing",
                            "fix": "Go back to images and re-upload the photos, then publish again."}],
                "export_path": str(export_path),
            }

    # Package weight and a ship-from location are publish-time requirements, so
    # only gate a LIVE publish on them — an incomplete draft can still be saved
    # to eBay and finished later.
    if mode == "live":
        total_lb = (listing.package_weight_lb or 0) + (listing.package_weight_oz or 0) / 16.0
        if total_lb <= 0:
            log.warning("publish blocked: no package weight for session %s", session_id)
            return {
                "dry_run": False,
                "error": True,
                "mode": mode,
                "message": "eBay needs a package weight to publish.",
                "issues": [{"target": "weight", "title": "Package weight is missing",
                            "fix": "Enter the shipping weight (lb / oz) in the listing, then Publish Live again."}],
                "export_path": str(export_path),
            }
        # For a connected seller, only THEIR location counts (the env key
        # belongs to the app owner's account, unusable under the user's token).
        has_location = (bool(creds.get("merchant_location_key")) if creds is not None
                        else bool(config.EBAY_MERCHANT_LOCATION_KEY))
        if not has_location:
            log.warning("publish blocked: no ship-from location for session %s", session_id)
            return {
                "dry_run": False,
                "error": True,
                "mode": mode,
                "message": "eBay needs a ship-from location before it can publish.",
                "issues": [{"target": "location", "title": "No ship-from location set",
                            "fix": "Open Listing settings, add your ship-from ZIP, and save — then Publish Live again."}],
                "export_path": str(export_path),
            }

    if mode == "draft" and creds and creds.get("access_token"):
        hub = create_seller_hub_draft(session_id, listing, base_url,
                                      creds["access_token"])
        if hub:
            return {
                "dry_run": False,
                "draft": True,
                "ebay_draft": True,
                "seller_hub_draft": True,
                "item_draft_id": hub["item_draft_id"],
                "mode": mode,
                "message": hub["message"],
                "export_path": str(export_path),
            }
        # Fall through to the unpublished-offer staging below.

    try:
        result = _push_live(session_id, listing, mode, base_url, creds,
                            do_publish=(mode == "live"))
        result["export_path"] = str(export_path)
        return result
    except httpx.HTTPStatusError as exc:
        issues = ebay_errors.from_response(exc.response.text)
        for it in issues:
            log.warning("publish failed (session=%s): [%s] %s | fix: %s", session_id,
                        it.get("error_id", ""), it.get("ebay_message") or it["title"], it["fix"])
        return {
            "dry_run": False,
            "error": True,
            "mode": mode,
            "message": ebay_errors.headline(issues, "publishing", exc.response.status_code),
            "issues": issues,
            "detail": exc.response.text,
            "export_path": str(export_path),
        }
    except httpx.RequestError as exc:
        # Token-refresh or other pre-publish network failure.
        log.warning("publish network error (session=%s): %s", session_id, exc)
        return {
            "dry_run": False,
            "error": True,
            "mode": mode,
            "message": "Couldn’t reach eBay just now — please try again in a moment.",
            "issues": [{"target": "generic", "title": "eBay didn’t respond",
                        "fix": "Wait a moment and press Publish Live again."}],
            "detail": str(exc),
            "export_path": str(export_path),
        }


def withdraw(session_id: str, listing: dict, creds: Optional[dict]) -> bool:
    """End a live eBay listing for this session's SKU (best-effort). Used when
    the seller deletes a published listing from QuickFlip so we don't leave an
    orphaned live listing on eBay. Returns True if an offer was withdrawn."""
    token = (creds or {}).get("access_token") or _access_token()
    from ..models import Listing as _Listing
    lst = listing if isinstance(listing, _Listing) else _Listing(**(listing or {}))
    sku = _sku(session_id, lst)
    base = config.EBAY_API_BASE
    try:
        with httpx.Client(timeout=30) as client:
            offer_id = _existing_offer_id(client, base, token, sku)
            if not offer_id:
                return False
            r = client.post(
                f"{base}/sell/inventory/v1/offer/{offer_id}/withdraw",
                headers=_headers(token),
            )
            log.info("withdraw offer %s (sku=%s) -> %s", offer_id, sku, r.status_code)
            return r.is_success
    except Exception as exc:  # noqa: BLE001 - never block a local delete
        log.warning("withdraw(sku=%s) failed: %s", sku, exc)
        return False


# --- Promoted Listings + dashboard data (Marketing / Fulfillment APIs) ------

_PL_CAMPAIGN_NAME = "QuickFlip Promoted"


def _find_or_create_campaign(client, base: str, token: str) -> Optional[str]:
    """A single always-on Promoted Listings Standard campaign to hang ads on."""
    r = client.get(f"{base}/sell/marketing/v1/ad_campaign",
                   headers=_headers(token), params={"limit": 50})
    if r.status_code == 200:
        for c in r.json().get("campaigns", []) or []:
            if c.get("campaignStatus") in ("RUNNING", "PAUSED") or \
               c.get("campaignName") == _PL_CAMPAIGN_NAME:
                return c.get("campaignId")
    body = {
        "campaignName": _PL_CAMPAIGN_NAME,
        "fundingStrategy": {"fundingModel": "COST_PER_SALE"},
        "marketplaceId": config.EBAY_MARKETPLACE_ID,
        "startDate": None,
    }
    r = client.post(f"{base}/sell/marketing/v1/ad_campaign",
                    headers=_headers(token), json=_prune(body))
    if r.status_code in (200, 201):
        loc = r.headers.get("Location", "")
        return loc.rstrip("/").split("/")[-1] or _body(r).get("campaignId")
    log.info("createCampaign -> %s %s", r.status_code, r.text[:300])
    return None


def _suggested_ad_rate(client: httpx.Client, base: str, token: str,
                       listing_id: str) -> Optional[float]:
    """eBay's recommended Promoted Listings ad rate for one listing
    (Recommendation API). Best-effort — None means 'no suggestion'."""
    try:
        r = client.post(
            f"{base}/sell/recommendation/v1/find",
            headers={**_headers(token), "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
            params={"filter": "recommendationTypes:{AD}"},
            json={"listingIds": [listing_id]})
        if r.status_code != 200:
            log.info("suggest_ad_rate(%s) -> %s %s", listing_id,
                     r.status_code, r.text[:200])
            return None
        for rec in r.json().get("listingRecommendations", []):
            for bid in ((rec.get("marketing") or {}).get("ad") or {}).get(
                    "bidPercentages", []):
                try:
                    val = float(bid.get("value"))
                except (TypeError, ValueError):
                    continue
                if 0 < val <= 100:
                    return val
    except Exception as exc:  # noqa: BLE001
        log.info("suggest_ad_rate(%s) failed: %s", listing_id, exc)
    return None


def promote(session_id: str, listing: Listing, listing_id: str,
            creds: Optional[dict]) -> dict:
    """Best-effort: advertise a just-published listing via Promoted Listings.
    Returns {promoted: bool, percent, message}. Never raises."""
    if not listing.promote_enabled or not listing_id:
        return {"promoted": False}
    token = (creds or {}).get("access_token") or _access_token()
    base = config.EBAY_API_BASE
    # Default rate when the user picked "recommended" but we can't fetch one.
    percent = (listing.promote_percent if not listing.promote_recommended
               and listing.promote_percent else None)
    try:
        with httpx.Client(timeout=30) as client:
            campaign_id = _find_or_create_campaign(client, base, token)
            if not campaign_id:
                return {"promoted": False,
                        "message": "Couldn't set up a Promoted Listings campaign."}
            rate = percent
            if not rate:
                # "Recommended" now actually asks eBay for its suggested rate
                # (it used to silently hardcode 2%); 2% stays the fallback
                # when eBay has no suggestion for the listing.
                rate = _suggested_ad_rate(client, base, token, listing_id) or 2.0
            body = {
                "listingId": listing_id,
                "bidPercentage": f"{float(rate):.1f}",
            }
            r = client.post(
                f"{base}/sell/marketing/v1/ad_campaign/{campaign_id}/ad",
                headers=_headers(token), json=body)
            if r.status_code in (200, 201):
                log.info("promoted listing %s @ %.1f%% (campaign %s)",
                         listing_id, float(rate), campaign_id)
                return {"promoted": True, "percent": float(rate),
                        "message": f"Promoted at {float(rate):.1f}% ad rate."}
            log.info("createAd(%s) -> %s %s", listing_id, r.status_code, r.text[:300])
            return {"promoted": False,
                    "message": "Listing published; couldn't start the ad "
                               "(you can promote it from eBay Seller Hub)."}
    except Exception as exc:  # noqa: BLE001
        log.warning("promote(%s) failed: %s", listing_id, exc)
        return {"promoted": False}


def fetch_sold_count(creds: dict, days: int = 90) -> dict:
    """Count of orders in the last `days` for the dashboard tile. Best-effort;
    returns {count, days} or {count: None} on any failure."""
    token = creds["access_token"]
    base = config.EBAY_API_BASE
    import datetime as _dt
    since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days))
    since_s = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"{base}/sell/fulfillment/v1/order",
                headers=_headers(token),
                params={"filter": f"creationdate:[{since_s}..]", "limit": 200})
            if r.status_code == 200:
                return {"count": r.json().get("total", 0), "days": days}
            log.info("getOrders -> %s %s", r.status_code, r.text[:200])
    except Exception as exc:  # noqa: BLE001
        log.info("fetch_sold_count failed: %s", exc)
    return {"count": None, "days": days}


def fetch_live_listings(creds: dict, limit: int = 100) -> list[dict]:
    """Published offers on the seller's account, as listing cards flagged
    from_ebay. Covers Inventory-API-managed listings. Best-effort."""
    token = creds["access_token"]
    base = config.EBAY_API_BASE
    out: list[dict] = []
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(f"{base}/sell/inventory/v1/offer",
                           headers=_headers(token),
                           params={"limit": limit, "offset": 0,
                                   "marketplace_id": config.EBAY_MARKETPLACE_ID})
            if r.status_code != 200:
                # eBay requires a sku filter on some accounts; fall back quietly.
                log.info("getOffers(all) -> %s %s", r.status_code, r.text[:200])
                return []
            for off in r.json().get("offers", []) or []:
                if off.get("status") != "PUBLISHED":
                    continue
                lid = off.get("listing", {}).get("listingId", "")
                price = (off.get("pricingSummary", {}) or {}).get("price", {})
                out.append({
                    "id": f"ebay-{off.get('offerId', lid)}",
                    "from_ebay": True,
                    "ebay_item_id": lid,
                    "sku": off.get("sku", ""),
                    "status": "published",
                    "title": off.get("sku", ""),
                    "listing": {
                        "title": off.get("sku", ""),
                        "price": float(price.get("value")) if price.get("value") else None,
                        "currency": price.get("currency", "USD"),
                        "images": [],
                    },
                    "view_url": (f"https://www.ebay.com/itm/{lid}" if lid else None),
                })
    except Exception as exc:  # noqa: BLE001
        log.info("fetch_live_listings failed: %s", exc)
    return out


# --- Trading API: full active-inventory sync + live-listing edits -----------
# The Inventory API's getOffers only sees listings created *through* this app.
# To manage a seller's ENTIRE active inventory (including items listed
# elsewhere) we use the classic Trading API with an OAuth token passed as an
# IAF token. These calls need only the sell.inventory scope, which every
# connection already grants — so this adds no risk to the Connect flow.

_TRADING_NS = "urn:ebay:apis:eBLBaseComponents"
_TRADING_COMPAT = "1193"


def _xml_escape(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _trading_call(call_name: str, inner_xml: str, token: str,
                  timeout: float = 30) -> ET.Element:
    """POST a Trading API request and return the parsed XML root. Raises on
    transport errors; callers inspect Ack/Errors for API-level failures."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<{call_name}Request xmlns="{_TRADING_NS}">'
        '<ErrorLanguage>en_US</ErrorLanguage>'
        '<WarningLevel>Low</WarningLevel>'
        f'{inner_xml}'
        f'</{call_name}Request>'
    )
    headers = {
        "X-EBAY-API-SITEID": "0",  # US
        "X-EBAY-API-COMPATIBILITY-LEVEL": _TRADING_COMPAT,
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-IAF-TOKEN": token,
        "Content-Type": "text/xml",
    }
    r = httpx.post(f"{config.EBAY_API_BASE}/ws/api.dll",
                   headers=headers, content=body.encode("utf-8"), timeout=timeout)
    if r.status_code >= 400:
        # Feeding an HTML error page to fromstring() raised a ParseError that
        # callers reported as "couldn't reach eBay", hiding the real status.
        log.warning("trading %s -> HTTP %s: %.300s", call_name, r.status_code, r.text)
        r.raise_for_status()
    return ET.fromstring(r.content)


def _lt(elem: Optional[ET.Element], path: str) -> Optional[ET.Element]:
    """find() ignoring XML namespaces (Trading API responses are namespaced)."""
    return elem.find(path) if elem is not None else None


def _lttext(elem: Optional[ET.Element], path: str, default: str = "") -> str:
    node = _lt(elem, path)
    return node.text if (node is not None and node.text is not None) else default


def _trading_errors(root: ET.Element) -> list[str]:
    """Human-readable long messages for any Error (severity Error) in a reply."""
    msgs = []
    for err in root.findall(".//{*}Errors"):
        if _lttext(err, "{*}SeverityCode") == "Error":
            msgs.append(_lttext(err, "{*}LongMessage")
                        or _lttext(err, "{*}ShortMessage") or "eBay error")
    return msgs


def fetch_active_inventory(creds: dict, limit: int = 200) -> list[dict]:
    """Every active listing on the seller's account (Trading API
    GetMyeBaySelling → ActiveList), as from_ebay listing cards. Best-effort."""
    token = (creds or {}).get("access_token") or _access_token()
    per_page = min(max(limit, 1), 200)  # Trading API caps EntriesPerPage at 200
    out: list[dict] = []
    # Page through the whole account (was: page 1 only, so sellers with >200
    # active listings silently lost the rest). Bounded at 5 pages = 1000 items.
    for page in range(1, 6):
        inner = (
            "<ActiveList><Include>true</Include>"
            f"<Pagination><EntriesPerPage>{per_page}</EntriesPerPage>"
            f"<PageNumber>{page}</PageNumber></Pagination></ActiveList>"
        )
        try:
            root = _trading_call("GetMyeBaySelling", inner, token)
        except Exception as exc:  # noqa: BLE001
            log.info("fetch_active_inventory page %d failed: %s", page, exc)
            break
        if _lttext(root, "{*}Ack") in ("Failure",):
            log.info("GetMyeBaySelling failure: %s", "; ".join(_trading_errors(root)))
            break
        out.extend(_parse_active_items(root))
        total_pages = _lttext(
            root, ".//{*}ActiveList/{*}PaginationResult/{*}TotalNumberOfPages", "1")
        try:
            if page >= int(total_pages or "1"):
                break
        except ValueError:
            break
    return out


def _parse_active_items(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    try:
        for item in root.findall(".//{*}ActiveList/{*}ItemArray/{*}Item"):
            item_id = _lttext(item, "{*}ItemID")
            if not item_id:
                continue
            title = _lttext(item, "{*}Title")
            price_node = _lt(item, "{*}SellingStatus/{*}CurrentPrice")
            price = None
            currency = "USD"
            if price_node is not None:
                currency = price_node.get("currencyID", "USD")
                try:
                    price = float(price_node.text) if price_node.text else None
                except (TypeError, ValueError):
                    price = None
            qty_avail = (_lttext(item, "{*}QuantityAvailable")
                         or _lttext(item, "{*}Quantity") or "")
            try:
                quantity = int(qty_avail) if qty_avail else None
            except ValueError:
                quantity = None
            gallery = (_lttext(item, "{*}PictureDetails/{*}GalleryURL")
                       or _lttext(item, "{*}PictureURL"))
            view_url = (_lttext(item, "{*}ListingDetails/{*}ViewItemURL")
                        or (f"https://www.ebay.com/itm/{item_id}"))
            out.append({
                "id": f"ebay-{item_id}",
                "from_ebay": True,
                "ebay_item_id": item_id,
                "sku": _lttext(item, "{*}SKU"),
                "editable_ebay": True,
                "status": "live",
                "title": title,
                "listing": {
                    "title": title,
                    "price": price,
                    "currency": currency,
                    "quantity": quantity,
                    "image_url": gallery,
                    "images": [],
                },
                "view_url": view_url,
            })
    except Exception as exc:  # noqa: BLE001
        log.info("fetch_active_inventory parse failed: %s", exc)
    return out


def revise_live_listing(creds: dict, item_id: str,
                        price: Optional[float] = None,
                        quantity: Optional[int] = None) -> dict:
    """Change price and/or quantity of a live listing (Trading API
    ReviseInventoryStatus). Returns {ok, message}."""
    token = (creds or {}).get("access_token") or _access_token()
    if not item_id or (price is None and quantity is None):
        return {"ok": False, "message": "Nothing to update."}
    parts = [f"<ItemID>{_xml_escape(item_id)}</ItemID>"]
    if price is not None:
        parts.append(f"<StartPrice>{float(price):.2f}</StartPrice>")
    if quantity is not None:
        parts.append(f"<Quantity>{int(quantity)}</Quantity>")
    inner = f"<InventoryStatus>{''.join(parts)}</InventoryStatus>"
    try:
        root = _trading_call("ReviseInventoryStatus", inner, token)
        errors = _trading_errors(root)
        if _lttext(root, "{*}Ack") in ("Success", "Warning") and not errors:
            return {"ok": True, "message": "Listing updated on eBay."}
        return {"ok": False, "message": "; ".join(errors) or "eBay rejected the update."}
    except Exception as exc:  # noqa: BLE001
        log.warning("revise_live_listing(%s) failed: %s", item_id, exc)
        return {"ok": False, "message": "Couldn't reach eBay to update the listing."}


def end_item(creds: dict, item_id: str, reason: str = "NotAvailable") -> dict:
    """End a live listing (Trading API EndItem). Returns {ok, message}."""
    token = (creds or {}).get("access_token") or _access_token()
    if not item_id:
        return {"ok": False, "message": "Missing item id."}
    inner = (f"<ItemID>{_xml_escape(item_id)}</ItemID>"
             f"<EndingReason>{_xml_escape(reason)}</EndingReason>")
    try:
        root = _trading_call("EndItem", inner, token)
        errors = _trading_errors(root)
        if _lttext(root, "{*}Ack") in ("Success", "Warning") and not errors:
            return {"ok": True, "message": "Listing ended on eBay."}
        # Already-ended items report an error we can treat as success.
        if any("already" in e.lower() or "ended" in e.lower() for e in errors):
            return {"ok": True, "message": "Listing was already ended."}
        return {"ok": False, "message": "; ".join(errors) or "eBay rejected the request."}
    except Exception as exc:  # noqa: BLE001
        log.warning("end_item(%s) failed: %s", item_id, exc)
        return {"ok": False, "message": "Couldn't reach eBay to end the listing."}

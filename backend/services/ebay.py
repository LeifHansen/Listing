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
        return [_prune(v) for v in value]
    return value


def sku_for(session_id: str) -> str:
    # Must be STABLE for the life of the listing: republishing the same session
    # reuses the SKU so we update the existing offer instead of creating a
    # duplicate. Deriving it from mutable fields (e.g. brand) would mint a new
    # SKU whenever the user edits that field and silently create a second live
    # listing — so key it on the immutable session id alone.
    return f"THRYFT-{session_id}".upper()


def _image_urls(session_id: str, names: list[str], base_url: str) -> list[str]:
    """eBay requires publicly reachable image URLs.

    With a public R2 base URL configured, hand eBay durable bucket URLs.
    Otherwise serve via the app's /media route — those URLs are public and
    stable, and (with R2 in presigned mode) survive restarts by redirecting
    to the bucket; a presigned URL itself would be a poor fit here, since it
    expires and eBay may re-fetch.
    """
    names = names or storage.list_optimized(session_id)
    if objstore.enabled() and config.r2_public_urls():
        return [objstore.public_url(objstore.key_for(session_id, n)) for n in names]
    return [f"{base_url}/media/{session_id}/optimized/{n}" for n in names]


def image_urls_for(session_id: str, listing: Listing, base_url: str) -> list[str]:
    """Public URLs for a listing's photos — the Trading publish path needs the
    same URLs the Inventory path sends."""
    return _image_urls(session_id, listing.images, base_url)


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


def _is_auction(listing: Listing) -> bool:
    return (getattr(listing, "listing_format", "") or "").upper() in ("AUCTION", "AUCTION_BIN")


def build_inventory_item(session_id: str, listing: Listing, base_url: str,
                         image_urls: Optional[list[str]] = None) -> dict:
    from . import taxonomy
    # Same guard as the Trading path: values coerced to each aspect's
    # constraints so a chatty value can't sink the publish.
    taxonomy.sanitize_specifics(listing)
    aspects: dict[str, list[str]] = {}
    identifiers: dict[str, str] = {}
    # eBay treats MOST aspects as single-value: sending two values (e.g. from a
    # comma-joined string) triggers "<Aspect> should contain only one value" and
    # rejects the whole publish — this hit Brand, then Region of Origin, and
    # would hit any other single-value aspect. Ask the taxonomy which aspects
    # are MULTI; only those may hold several values, everything else is capped
    # to one. If the lookup fails we fall back to treating every aspect as
    # single, which is always safe (it can never over-populate).
    multi_value_names: set[str] = set()
    # Canonical aspect names for the category, keyed by lowercase — plus
    # "Item X" <-> "X" aliases. eBay matches aspects by EXACT name, so a
    # seller-filled "Height" does NOT satisfy a required "Item Height" (error
    # 25002 "The item specific Item Height is missing" even though the height
    # is filled in). Renaming to eBay's own spelling fixes case drift and the
    # dimension-alias mismatch in one place.
    canonical_names: dict[str, str] = {}
    try:
        if listing.category_id:
            from . import taxonomy
            for a in taxonomy.item_aspects(listing.category_id).get("aspects", []):
                aname = (a.get("name") or "").strip()
                if not aname:
                    continue
                if a.get("cardinality") == "MULTI":
                    multi_value_names.add(aname.lower())
                canonical_names[aname.lower()] = aname
                # "Item Height" also answers to plain "Height" (and vice versa
                # for the rare category that defines the bare name).
                if aname.lower().startswith("item "):
                    canonical_names.setdefault(aname.lower()[5:], aname)
                else:
                    canonical_names.setdefault(f"item {aname.lower()}", aname)
    except Exception:  # noqa: BLE001 - best-effort; single-value is the safe default
        multi_value_names = set()
        canonical_names = {}

    # Brand is a SINGLE-value aspect on eBay — sending two values (e.g. a brand
    # field plus a duplicate "Brand" item specific, or a comma-joined value)
    # triggers "Brand should contain only one value". Seed it from the brand
    # field, cleaned to exactly one token.
    if listing.brand:
        aspects["Brand"] = [listing.brand.split(",")[0].strip()[:65]]
    for spec in listing.item_specifics:
        if not (spec.name and spec.value):
            continue
        # eBay aspect limits: name <= 40 chars, value <= 65, <= 30 values each.
        # An over-long value otherwise fails the whole createOrReplace call, so
        # NO specifics land — exactly the "not populating" symptom.
        name = spec.name.strip()[:40]
        raw_value = spec.value.strip()
        if not name or not raw_value:
            continue
        # Snap to eBay's exact aspect name for this category (case drift and
        # the "Height" vs "Item Height" alias both rejected publishes).
        name = canonical_names.get(name.lower(), name)
        key = name.lower()
        # Route UPC/EAN/ISBN to the canonical product fields rather than aspects.
        if key in _IDENTIFIER_KEYS:
            identifiers.setdefault(_IDENTIFIER_KEYS[key], raw_value[:65])
            continue
        # Brand must stay single-valued: never comma-split it, and don't add a
        # second value when the brand field already seeded it.
        if key == "brand":
            aspects.setdefault("Brand", [raw_value.split(",")[0].strip()[:65]])
            continue
        aspects.setdefault(name, [])
        # Only MULTI aspects get comma-split into eBay's multi-value array form
        # (so "Features: Stretch,Breathable" maps to both). A single-value
        # aspect keeps its value whole — splitting it is exactly what caused the
        # "should contain only one value" rejection.
        if key in multi_value_names:
            pieces = [p.strip()[:65] for p in raw_value.split(",")]
        else:
            pieces = [raw_value[:65]]
        for piece in pieces:
            if piece and piece not in aspects[name] and len(aspects[name]) < 30:
                aspects[name].append(piece)
        if not aspects[name]:
            del aspects[name]  # never send an empty aspect array (eBay rejects it)

    # Final guard: any aspect eBay treats as single-value ships with exactly one
    # value, no matter how it got populated (duplicate rows, seeded Brand, etc.).
    for aname in list(aspects):
        if aname.strip().lower() not in multi_value_names:
            aspects[aname] = aspects[aname][:1]

    # eBay validates brand and MPN as a pair ('Input data for tag <BrandMPN>
    # is invalid or missing'): once a brand is present, an MPN must be too.
    # Use the seller's MPN item specific when given, else eBay's official
    # "no part number" sentinel.
    brand = (aspects.get("Brand") or [""])[0] or listing.brand
    mpn = next((s.value for s in listing.item_specifics
                if s.name and s.value and s.name.strip().lower()
                in ("mpn", "manufacturer part number")), "")
    if brand and not mpn:
        mpn = "Does Not Apply"

    product = {
        "title": listing.title[:80],
        "description": listing.description or listing.title,
        "aspects": aspects,
        "imageUrls": image_urls or _image_urls(session_id, listing.images, base_url),
        "brand": brand or None,
        "mpn": (mpn if brand else None) or None,
    }
    # eBay expects identifiers as arrays; "Does Not Apply" is the accepted
    # sentinel for items without one.
    for field, value in identifiers.items():
        product[field] = [value]

    return _prune({
        **_package_weight_and_size(listing),
        "sku": sku_for(session_id),
        "availability": {
            "shipToLocationAvailability": {
                "quantity": 1 if _is_auction(listing) else max(1, listing.quantity)
            }
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

    def _money(v) -> dict:
        return {"value": f"{float(v or 0):.2f}", "currency": currency}

    # Listing format → pricing shape. Auctions use auctionStartPrice + a listing
    # duration and are always single-quantity; AUCTION_BIN adds a Buy It Now
    # price. Fixed price is a GTC (Good 'Til Cancelled) listing.
    fmt = (listing.listing_format or "FIXED_PRICE").upper()
    if fmt in ("AUCTION", "AUCTION_BIN"):
        start = listing.auction_start_price if listing.auction_start_price is not None else price
        pricing = {"auctionStartPrice": _money(start)}
        if fmt == "AUCTION_BIN" and price and price > 0:
            pricing["price"] = _money(price)  # Buy It Now price on the auction
        offer_format = "AUCTION"
        duration = listing.auction_duration or "DAYS_7"
        qty = 1
    else:
        pricing = {"price": _money(price)}
        offer_format = "FIXED_PRICE"
        duration = "GTC"
        qty = max(1, listing.quantity)

    return _prune({
        "sku": sku_for(session_id),
        "marketplaceId": config.EBAY_MARKETPLACE_ID,
        "format": offer_format,
        "availableQuantity": qty,
        "categoryId": listing.category_id or None,
        "listingDescription": listing.description or listing.title,
        "listingDuration": duration,
        "pricingSummary": pricing,
        "listingPolicies": {
            "fulfillmentPolicyId": fulfillment,
            "paymentPolicyId": payment,
            "returnPolicyId": returns,
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


def _existing_offer(client, base: str, token: str, sku: str) -> Optional[dict]:
    """Return the existing offer record for this SKU, or None. Republishing the
    same session reuses the SKU, so we must update that offer rather than
    create a duplicate (eBay error 25002 'Offer entity already exists').
    Queried by SKU only — extra marketplace/format filters can hide a match.
    The record's status/listing also tell us whether the offer is already a
    LIVE listing, which turns a republish into a revision."""
    try:
        r = client.get(
            f"{base}/sell/inventory/v1/offer",
            headers=_headers(token),
            params={"sku": sku},
        )
        if r.status_code == 200:
            offers = r.json().get("offers", []) or []
            if offers:
                return offers[0]
        else:
            log.info("getOffers(sku=%s) -> %s %s", sku, r.status_code, _body(r))
    except Exception as exc:  # noqa: BLE001 - treat as "no existing offer"
        log.info("getOffers(sku=%s) failed: %s", sku, exc)
    return None


def _is_already_published(body: dict) -> bool:
    """True when publishOffer failed only because the offer is already a live
    listing — for us that means the update calls just revised it (success)."""
    for err in (body or {}).get("errors", []) or []:
        msg = str(err.get("message", "")).lower()
        if "already" in msg and "publish" in msg:
            return True
    return False


def _offer_id_from_error(body: dict) -> Optional[str]:
    """eBay's 'Offer entity already exists' (25002) error carries the existing
    offerId in its parameters — pull it out so we can update that offer."""
    for err in (body or {}).get("errors", []) or []:
        for p in err.get("parameters", []) or []:
            if str(p.get("name", "")).lower() == "offerid" and p.get("value"):
                return str(p["value"])
    return None


def _live_inventory_images(session_id: str, listing: Listing,
                           creds: Optional[dict]) -> Optional[list[str]]:
    """The imageUrls eBay currently stores for this SKU's inventory item, or
    None if there's no inventory item yet (a fresh publish). Reusing eBay's own
    set on a REVISE avoids two problems: the local optimized photos may have
    aged off disk ("photos aren't on the server"), and re-sending our own URLs
    when eBay already hosts the pictures triggers eBay's "can't have a
    combination of self-hosted and eBay-hosted pictures" rejection."""
    token = (creds or {}).get("access_token") or _access_token()
    if not token:
        return None
    try:
        r = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/inventory_item/{sku_for(session_id)}",
            headers=_headers(token), timeout=30)
        if r.status_code == 200:
            urls = ((r.json().get("product") or {}).get("imageUrls")) or []
            return [u for u in urls if u] or None
    except Exception as exc:  # noqa: BLE001 - treat as "no reusable images"
        log.info("getInventoryItem images failed (sku=%s): %s",
                 sku_for(session_id), exc)
    return None


def _push_live(session_id: str, listing: Listing, mode: str, base_url: str,
               creds: Optional[dict] = None, do_publish: bool = True,
               image_urls: Optional[list[str]] = None) -> dict:
    token = (creds or {}).get("access_token") or _access_token()
    sku = sku_for(session_id)
    item = build_inventory_item(session_id, listing, base_url, image_urls=image_urls)
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

            offer_rec = _existing_offer(client, base, token, sku)
            offer_id = (offer_rec or {}).get("offerId")
            # A PUBLISHED offer = this session is already a live eBay listing.
            # The item/offer updates above+below then revise the live listing
            # directly — publishOffer isn't needed (and would only error).
            already_live = bool(
                offer_rec and str(offer_rec.get("status", "")).upper() == "PUBLISHED")
            live_listing_id = ((offer_rec or {}).get("listing") or {}).get("listingId")
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
            # An already-live offer is different: the update calls above have
            # ALREADY revised the live listing (that's how the Inventory API
            # does revisions), so report it as a successful revision whether
            # or not do_publish was requested.
            published = already_live
            revised = already_live
            listing_id = live_listing_id if already_live else None
            if do_publish and not already_live:
                r3 = client.post(
                    f"{base}/sell/inventory/v1/offer/{offer_id}/publish",
                    headers=_headers(token),
                )
                r3_body = _body(r3)
                steps.append({"step": "publishOffer", "status": r3.status_code, "body": r3_body})
                if not r3.is_success and _is_already_published(r3_body):
                    # Raced/recovered offer that was live all along — the
                    # updates above revised it; that's success, not failure.
                    published = True
                    revised = True
                else:
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
        "revised": revised,  # edits pushed onto an already-live listing
        "ebay_draft": not published,  # unpublished offer created on eBay
        "message": ("Live listing updated — your changes are on eBay now."
                    if revised else None if published else
                    "Draft saved — it's staged on your eBay account as an "
                    "unpublished offer, ready for a one-click publish. Heads-up: "
                    "eBay does NOT show unpublished offers anywhere in Seller Hub "
                    "(not even the Drafts page), so you won't see it on eBay until "
                    "you press Publish Live here."),
        "listing_id": listing_id,
        "steps": steps,
    }


def withdraw(session_id: str, listing: Listing, creds: Optional[dict] = None) -> dict:
    """End the live eBay listing for this session (withdrawOffer). Returns
    {ended | not_live, message}; raises ValueError with a clear reason when
    eBay refuses."""
    token = (creds or {}).get("access_token") or _access_token()
    sku = sku_for(session_id)
    base = config.EBAY_API_BASE
    with httpx.Client(timeout=30) as client:
        # Definitive lookup only: _existing_offer treats an API blip as "no
        # offer", which here would mark a still-live listing as ended. Only a
        # clean 200/404 may proceed; anything else must error out.
        try:
            r0 = client.get(f"{base}/sell/inventory/v1/offer",
                            headers=_headers(token), params={"sku": sku})
        except httpx.HTTPError as exc:
            raise ValueError("Couldn't check this listing on eBay just now — "
                             "try again in a moment.") from exc
        if r0.status_code == 404:
            offers = []
        elif r0.status_code == 200:
            offers = r0.json().get("offers", []) or []
        else:
            raise ValueError("Couldn't check this listing on eBay just now — "
                             "try again in a moment.")
        rec = offers[0] if offers else None
        if not rec or str(rec.get("status", "")).upper() != "PUBLISHED":
            return {"ended": False, "not_live": True,
                    "message": "This listing isn't live on eBay anymore — "
                               "nothing to end."}
        r = client.post(
            f"{base}/sell/inventory/v1/offer/{rec['offerId']}/withdraw",
            headers=_headers(token),
        )
        if not r.is_success:
            issues = ebay_errors.from_response(r.text)
            raise ValueError(ebay_errors.headline(issues, "withdrawOffer", r.status_code))
        log.info("ebay: listing ended (sku=%s offer=%s)", sku, rec["offerId"])
        return {"ended": True, "message": "Listing ended — it's no longer for sale on eBay."}


def live_status(session_id: str, listing: Listing,
                creds: Optional[dict] = None) -> tuple[Optional[str], str]:
    """('published'|'sold'|'ended'|None, listing_id) for this session's offer on
    eBay. 'sold' when eBay reports units sold (auto-archive candidate). None
    means "couldn't determine" (network/API blip) — callers must NOT change
    anything on None, only on a definitive answer."""
    try:
        token = (creds or {}).get("access_token") or _access_token()
        r = httpx.get(
            f"{config.EBAY_API_BASE}/sell/inventory/v1/offer",
            headers=_headers(token),
            params={"sku": sku_for(session_id)},
            timeout=30,
        )
    except Exception:  # noqa: BLE001 - unknown, not "ended"
        return None, ""
    if r.status_code != 200:
        # 404 with "no offers found" = definitively gone; anything else = unknown.
        return ("ended", "") if r.status_code == 404 else (None, "")
    offers = r.json().get("offers", []) or []
    if not offers:
        return "ended", ""
    rec = offers[0]
    lst = rec.get("listing") or {}
    lid = str(lst.get("listingId") or "")
    offer_status = str(rec.get("status", "")).upper()
    listing_status = str(lst.get("listingStatus", "")).upper()
    try:
        sold_qty = int(lst.get("soldQuantity") or 0)
    except (TypeError, ValueError):
        sold_qty = 0
    # A sale (soldQuantity > 0) ends a single-item listing — archive it.
    if sold_qty > 0 or listing_status == "SOLD":
        return "sold", lid
    if offer_status == "PUBLISHED" and listing_status in ("", "ACTIVE"):
        return "published", lid
    return "ended", lid


def publish(session_id: str, listing: Listing, mode: str, base_url: str,
            creds: Optional[dict] = None, is_revise: bool = False) -> dict:
    """Push to eBay, or dry-run if not configured.

    creds: a connected user's eBay credentials (access_token + policy ids +
    location). When present we publish with those; otherwise we fall back to
    env config, and dry-run if neither is available.
    mode: "draft" saves in QuickFlip only (no eBay call); "live" publishes to eBay.
    is_revise: this listing is already live on eBay — reuse eBay's hosted images
      rather than re-sending ours (avoids the "photos aren't on the server"
      block and eBay's self-hosted/eBay-hosted image-mix rejection).
    """
    # Revising a live listing: reuse the images eBay already hosts, so editing
    # a listing's text/price/specifics works even after the local photos aged
    # off disk, and we never hand eBay a mix of self-hosted + eBay-hosted URLs.
    image_urls_override = (_live_inventory_images(session_id, listing, creds)
                           if (creds and is_revise) else None)

    item = build_inventory_item(session_id, listing, base_url,
                                image_urls=image_urls_override)
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
                "message": ("Saved to your QuickFlip drafts — find it under Drafts. "
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

    # eBay fetches every image URL when the inventory item is created; if a
    # photo is reachable neither on disk nor in R2 it fails with an opaque
    # 25001 'system error'. Applies to drafts too (both create the inventory
    # item). Fail clearly instead — unless eBay already hosts the images (a
    # revise), in which case we reuse those and don't need the files.
    if not image_urls_override:
        names = listing.images or storage.list_optimized(session_id)
        opt_dir = storage.optimized_dir(session_id)

        def _fetchable(n: str) -> bool:
            """Will eBay's fetch of this photo's URL succeed? Checks the copy
            the URL actually resolves to, and heals the fixable gaps: a local
            file whose best-effort R2 upload was dropped is re-pushed (public
            mode sends bucket URLs), and an offloaded file is restored to disk
            (/media URLs must serve bytes, not lean on eBay following a
            redirect to a short-lived signed URL)."""
            path, key = opt_dir / n, objstore.key_for(session_id, n)
            if objstore.enabled() and config.r2_public_urls():
                return (objstore.exists(key)
                        or (path.is_file() and objstore.upload(path, key) is not None))
            return path.is_file() or objstore.restore(key, path)

        unfetchable = not names or any(not _fetchable(n) for n in names)
        if unfetchable:
            # Last resort before erroring: if eBay already hosts images for this
            # SKU, reuse them so the edit still goes through.
            image_urls_override = (_live_inventory_images(session_id, listing, creds)
                                   if creds else None)
        if unfetchable and not image_urls_override:
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

    try:
        result = _push_live(session_id, listing, mode, base_url, creds,
                            do_publish=(mode == "live"),
                            image_urls=image_urls_override)
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

"""What survives of the eBay Sell (Inventory) API integration.

The Inventory publish engine — createOrReplaceInventoryItem / createOffer /
publishOffer, and the withdraw and status calls that went with it — is gone.
Listings are created and revised through the Trading API (services/
ebay_trading.py, driven by services/listing_sync.py), because an
Inventory-API listing is "inventory-based" and eBay then refuses to let the
seller edit it anywhere but the tool that made it.

What is left here is the part that was never about the Inventory API: turning
a session's photos into the public URLs eBay fetches, and the legacy SKU that
old Promoted Listings ads are still keyed by.
"""
from __future__ import annotations

from .. import config, objstore, storage
from ..models import Listing


def rest_headers(token: str) -> dict:
    """Auth headers for eBay's REST APIs.

    Used by the Marketing API (Promoted Listings), which outlives the
    Inventory publish engine. `Content-Language` is required by eBay's REST
    WRITE calls and harmless on reads.

    services/ebay_orders.py carries its own near-copy of this without the
    Content-Language header. Left alone deliberately: the Fulfillment API
    reads it makes don't need it, and quietly changing the headers on the
    orders path is not this change's business.
    """
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
        "Accept": "application/json",
    }


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

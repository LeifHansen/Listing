"""What survives of the eBay Sell (Inventory) API integration.

The Inventory publish engine — createOrReplaceInventoryItem / createOffer /
publishOffer, and the withdraw and status calls that went with it — is gone.
Listings are created and revised through the Trading API (services/
ebay_trading.py, driven by services/listing_sync.py), because an
Inventory-API listing is "inventory-based" and eBay then refuses to let the
seller edit it anywhere but the tool that made it.

What is left here is the part that was never about the Inventory API: turning
a session's photos into the public URLs eBay fetches, and the legacy SKU that
old Promoted Listings ads are still keyed by -- plus the few transport rules
every outbound client shares (the eBay REST and Trading clients, and the Etsy
and Depop ones beside them).
"""
from __future__ import annotations

import httpx

from .. import config, objstore, storage
from ..models import Listing

# Transport failures that prove the request never reached the other side: no
# connection was ever established, so no bytes were delivered and nothing there
# could have acted on it. Everything NOT in this list is treated as unknown --
# including exception types nobody here anticipated. That default is the whole
# point: being wrong in the "nothing happened" direction is how a live listing
# ends up with no record of itself, while being wrong the other way costs one
# unnecessary look at the seller's listings.
#
# One definition for every client that writes to somebody else's system: the
# eBay Trading and Message clients, Etsy and Depop each carried a copy.
NEVER_SENT = (
    httpx.ConnectError,        # DNS failure, connection refused, TLS refused
    httpx.ConnectTimeout,      # gave up before the connection was made
    httpx.PoolTimeout,         # never even got a connection out of the pool
    httpx.UnsupportedProtocol,
    httpx.InvalidURL,
)


def rest_headers(token: str) -> dict:
    """Auth headers for eBay's REST APIs.

    Used by the Marketing API (Promoted Listings), which outlives the
    Inventory publish engine. `Content-Language` is required by eBay's REST
    WRITE calls and harmless on reads.
    """
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Content-Language": "en-US",
        "Accept": "application/json",
    }


def is_scope_error(resp) -> bool:
    """A Sell API refusal the seller fixes by RECONNECTING eBay: the token
    predates a scope the call needs (refreshes keep the scopes first
    granted), as opposed to a transient blip worth retrying.

    One definition for every Sell API client. There were five, and two had
    already drifted -- the orders client and ebay_messages missed eBay's
    `access_denied` body, so the same refusal read as "reconnect" to the
    offers, metrics and promotions code and as an outage to those two."""
    if resp.status_code in (401, 403):
        return True
    body = resp.text.lower()
    return ("insufficient" in body and "scope" in body) or "access_denied" in body


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

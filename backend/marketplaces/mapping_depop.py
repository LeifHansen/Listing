"""Listing -> Depop product mapping + Depop preflight. PURE (stdlib + models
only) and deliberately the single place partner-doc corrections land: Depop's
Selling API is partner-gated, so field names and limits below follow the
public product surface and get confirmed against the docs on the first
credentialed run — services/depop.py owns the transport, this module owns
the shape.

Depop's model: fixed price only (no auctions, no drafts — creating a product
puts it on sale), fashion-centric (brand/size/condition), and a short title.
"""
from __future__ import annotations

from ..models import Listing

TITLE_LIMIT = 65
DESCRIPTION_LIMIT = 1000
MAX_PHOTOS = 4

# Total over every condition the app's taxonomy/AI layer can emit — a missing
# key here would silently drop the condition, so test_mapping_depop asserts
# totality. Depop's public vocabulary: brand_new / like_new / excellent /
# good / fair.
CONDITION_MAP = {
    "NEW": "brand_new",
    "NEW_OTHER": "like_new",
    "NEW_WITH_DEFECTS": "good",
    "CERTIFIED_REFURBISHED": "excellent",
    "SELLER_REFURBISHED": "excellent",
    "LIKE_NEW": "like_new",
    # eBay's apparel grades, which is exactly the stock Depop sells.
    "PRE_OWNED_EXCELLENT": "excellent",
    "PRE_OWNED_FAIR": "fair",
    "USED_EXCELLENT": "excellent",
    "USED_VERY_GOOD": "good",
    "USED_GOOD": "good",
    "USED_ACCEPTABLE": "fair",
    "FOR_PARTS_OR_NOT_WORKING": "fair",
}


def truncate_title(title: str, limit: int = TITLE_LIMIT) -> str:
    """Cut at a word boundary — a mid-word chop reads like a glitch on a
    Depop card. Falls back to a hard cut for one giant word."""
    title = (title or "").strip()
    if len(title) <= limit:
        return title
    cut = title[:limit + 1]
    cut = cut[:cut.rfind(" ")] if " " in cut else title[:limit]
    return (cut or title[:limit]).rstrip(" ,;:-—")


def truncate_description(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    """Cut at a paragraph, then a sentence, then a word boundary.

    The listing description is written for eBay, which has no practical limit,
    so it runs to several thousand characters of labelled sections. Depop takes
    1000, and a hard slice lands mid-word — usually halfway through a heading,
    which reads like a bug rather than an edit."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    # Only accept a boundary in the back half: cutting a 1000-character
    # description down to 200 to land on a paragraph break loses more than the
    # ragged edge costs.
    floor = limit // 2
    para = head.rfind("\n\n")
    if para >= floor:
        return head[:para].rstrip()
    stop = max(head.rfind(". "), head.rfind(".\n"),
               head.rfind("! "), head.rfind("? "))
    if stop >= floor:
        return head[:stop + 1].rstrip()
    space = head.rfind(" ")
    return (head[:space] if space >= floor else head).rstrip(" ,;:-—")


def size_for(listing: Listing) -> str:
    """The explicit Depop size wins; else any "Size" item specific."""
    if listing.depop.size.strip():
        return listing.depop.size.strip()
    for s in listing.item_specifics:
        if s.name.strip().lower() == "size":
            return s.value.strip()
    return ""


def build_product_payload(listing: Listing) -> dict:
    payload = {
        "title": truncate_title(listing.title),
        "description": truncate_description(listing.description),
        "price": round(float(listing.price or 0), 2),
        "currency": listing.currency or "USD",
        "condition": CONDITION_MAP.get(
            (listing.condition or "").upper(), "good"),
    }
    if listing.brand:
        payload["brand"] = listing.brand
    size = size_for(listing)
    if size:
        payload["size"] = size
    if listing.depop.category:
        payload["category"] = listing.depop.category
    return payload


def preflight(listing: Listing) -> list[dict]:
    """Everything Depop would reject, as UI-ready {target, level, title, fix}
    issues with depop_-namespaced targets where the fix lives in its card."""
    issues: list[dict] = []

    def add(target: str, title: str, fix: str, level: str = "error") -> None:
        issues.append({"target": target, "level": level, "title": title, "fix": fix})

    if listing.listing_format != "FIXED_PRICE":
        add("format", "Depop doesn't support auctions",
            "Switch the listing to Buy It Now (fixed price), or unselect "
            "Depop for this publish.")
    if not (listing.images or []) and not (listing.image_urls or []):
        add("photos", "At least one photo is required",
            "Add a photo — Depop requires at least one image.")
    if not (listing.title or "").strip():
        add("title", "Title is missing", "Give the listing a title.")
    if float(listing.price or 0) <= 0:
        add("price", "Price is missing",
            "Set a price — Depop listings are fixed-price.")
    if int(listing.quantity or 1) > 1:
        add("quantity", "Depop sells one of each",
            "Depop listings are single-quantity — the extra units stay on "
            "your other marketplaces.", level="warn")
    return issues

"""Listing -> Etsy payload mapping + Etsy preflight. PURE on purpose —
stdlib + models only — so CI's minimal install can unit-test every rule.

Etsy's hard limits enforced here: title <= 140 chars (ours cap at 80, so
pass-through), price >= $0.20, <= 13 tags of <= 20 chars, <= 13 materials,
<= 10 photos. Etsy also requires seller attribution (who_made / when_made /
is_supply), a taxonomy category, and — for physical listings — a shipping
profile; those arrive via listing.etsy with account-level defaults from the
connection's settings.
"""
from __future__ import annotations

import re

from ..models import Listing

TITLE_LIMIT = 140
TAG_LIMIT = 13
TAG_CHAR_LIMIT = 20
MATERIAL_LIMIT = 13
PRICE_FLOOR = 0.20
MAX_PHOTOS = 10

WHO_MADE = ("i_did", "someone_else", "collective")
# Etsy's when_made vocabulary: made-to-order, year buckets, then decades.
WHEN_MADE = (
    "made_to_order", "2020_2026", "2010_2019", "2007_2009", "before_2007",
    "2000_2006", "1990s", "1980s", "1970s", "1960s", "1950s", "1940s",
    "1930s", "1920s", "1910s", "1900s", "1800s", "1700s", "before_1700",
)

_TAG_ALLOWED = re.compile(r"[^A-Za-z0-9' \-]+")
_HTML_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """Etsy descriptions are plain text; our descriptions may carry light
    HTML. <br>/<p> boundaries become newlines so paragraphs survive."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text or "")
    text = re.sub(r"(?i)</p>\s*<p[^>]*>", "\n\n", text)
    text = _HTML_TAG.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_tag(value: str) -> str:
    tag = _TAG_ALLOWED.sub("", (value or "").strip())
    tag = re.sub(r"\s+", " ", tag).strip()
    return tag if len(tag) <= TAG_CHAR_LIMIT else ""


def build_tags(listing: Listing) -> list[str]:
    """<= 13 search tags: explicit listing.etsy.tags first, then brand and
    item-specific values. Sanitized to Etsy's charset, deduped, length-capped
    (too-long candidates are dropped, not truncated — a cut-off word is a
    worse search term than none)."""
    candidates = list(listing.etsy.tags) + [listing.brand] + [
        s.value for s in listing.item_specifics
        if s.name.lower() not in ("material",)]
    tags: list[str] = []
    seen = set()
    for cand in candidates:
        tag = _clean_tag(cand)
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        tags.append(tag)
        if len(tags) >= TAG_LIMIT:
            break
    return tags


def build_materials(listing: Listing) -> list[str]:
    """Explicit materials first, then any "Material" item specifics."""
    candidates = list(listing.etsy.materials) + [
        s.value for s in listing.item_specifics if s.name.lower() == "material"]
    out: list[str] = []
    seen = set()
    for cand in candidates:
        m = _clean_tag(cand)
        if not m or m.lower() in seen:
            continue
        seen.add(m.lower())
        out.append(m)
        if len(out) >= MATERIAL_LIMIT:
            break
    return out


def _description(listing: Listing) -> str:
    """Plain-text description; the condition rides along as a trailing note
    because Etsy has no condition field of its own."""
    desc = strip_html(listing.description)
    cond_bits = []
    if listing.condition:
        cond_bits.append(listing.condition.replace("_", " ").title())
    if listing.condition_description:
        cond_bits.append(listing.condition_description.strip())
    if cond_bits and "condition:" not in desc.lower():
        desc = f"{desc}\n\nCondition: {' — '.join(cond_bits)}".strip()
    return desc


def shipping_profile_for(listing: Listing, settings: dict) -> str:
    """Per-listing override wins, else the account default from Settings."""
    return (listing.etsy.shipping_profile_id
            or str(settings.get("shipping_profile_id") or ""))


def return_policy_for(listing: Listing, settings: dict) -> str:
    return (listing.etsy.return_policy_id
            or str(settings.get("return_policy_id") or ""))


def build_listing_payload(listing: Listing, settings: dict) -> dict:
    """The createDraftListing body (also the PATCH body minus immutables)."""
    e = listing.etsy
    payload = {
        "quantity": max(int(listing.quantity or 1), 1),
        "title": (listing.title or "").strip()[:TITLE_LIMIT],
        "description": _description(listing),
        "price": round(float(listing.price or 0), 2),
        "who_made": e.who_made,
        "when_made": e.when_made,
        "taxonomy_id": int(e.taxonomy_id or 0),
        "is_supply": bool(e.is_supply),
    }
    tags = build_tags(listing)
    if tags:
        payload["tags"] = tags
    materials = build_materials(listing)
    if materials:
        payload["materials"] = materials
    shipping = shipping_profile_for(listing, settings)
    if shipping:
        payload["shipping_profile_id"] = int(shipping)
    ret = return_policy_for(listing, settings)
    if ret:
        payload["return_policy_id"] = int(ret)
    # Package weight/dims help Etsy's calculated shipping when present.
    oz = (listing.package_weight_lb or 0) * 16 + (listing.package_weight_oz or 0)
    if oz > 0:
        payload["item_weight"] = round(oz, 2)
        payload["item_weight_unit"] = "oz"
    if all((listing.package_length_in, listing.package_width_in,
            listing.package_height_in)):
        payload.update({
            "item_length": listing.package_length_in,
            "item_width": listing.package_width_in,
            "item_height": listing.package_height_in,
            "item_dimensions_unit": "in",
        })
    return payload


def preflight(listing: Listing, settings: dict) -> list[dict]:
    """Everything Etsy would reject at publish time, as UI-ready issues in
    the same {target, level, title, fix} shape the eBay preflight uses.
    Targets are namespaced etsy_* so the editor can jump to the Etsy card."""
    issues: list[dict] = []

    def add(target: str, title: str, fix: str, level: str = "error") -> None:
        issues.append({"target": target, "level": level, "title": title, "fix": fix})

    if listing.listing_format != "FIXED_PRICE":
        add("format", "Etsy doesn't support auctions",
            "Switch the listing to Buy It Now (fixed price), or unselect Etsy "
            "for this publish.")
    if not (listing.images or []) and not (listing.image_urls or []):
        add("photos", "At least one photo is required",
            "Add a photo — Etsy requires at least one image on every listing.")
    if not (listing.title or "").strip():
        add("title", "Title is missing", "Give the listing a title.")
    if not strip_html(listing.description):
        add("description", "Description is missing",
            "Write a description — Etsy requires one.")
    price = float(listing.price or 0)
    if price < PRICE_FLOOR:
        add("price", f"Etsy's minimum price is ${PRICE_FLOOR:.2f}",
            "Raise the price — Etsy rejects listings under 20 cents.")
    if not listing.etsy.taxonomy_id:
        add("etsy_taxonomy", "Etsy category is missing",
            "Pick an Etsy category in the Etsy section (or tap Suggest).")
    if listing.etsy.who_made not in WHO_MADE:
        add("etsy_attribution", "Who made this item?",
            "Etsy only allows handmade items, vintage (20+ years old), and "
            "craft supplies — set who made it in the Etsy section.")
    if listing.etsy.when_made not in WHEN_MADE:
        add("etsy_attribution", "When was it made?",
            "Set when the item was made in the Etsy section — required by "
            "Etsy's handmade/vintage/supplies policy.")
    if not shipping_profile_for(listing, settings):
        add("etsy_shipping_profile", "No Etsy shipping profile",
            "Pick a shipping profile in the Etsy section, or set a default "
            "under Settings → Cross-posting marketplaces → Etsy.")
    return issues

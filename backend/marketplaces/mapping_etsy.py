"""Listing -> Etsy payload mapping + Etsy preflight. PURE on purpose —
stdlib + models only — so CI's minimal install can unit-test every rule.

Etsy's hard limits enforced here: title <= 140 chars (ours cap at 80, so
pass-through), price >= $0.20, <= 13 tags of <= 20 chars, <= 13 materials,
<= 10 photos. Etsy also requires seller attribution (who_made / when_made /
is_supply), a taxonomy category, and — for physical listings — a shipping
profile, a return policy and a processing profile (readiness state); those
arrive via listing.etsy with account-level defaults from the connection's
settings.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from ..models import Listing
from ..money import money

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

# Etsy's own line: an item is vintage once it is at least this old, and only
# vintage items, handmade items and craft supplies may be listed at all.
VINTAGE_YEARS = 20


def when_made_latest_year(when_made: str) -> Optional[int]:
    """The most recent year a when_made bucket can mean ("1990s" -> 1999,
    "before_2007" -> 2006, "2020_2026" -> 2026); None for made_to_order or
    a value outside Etsy's vocabulary."""
    if when_made not in WHEN_MADE or when_made == "made_to_order":
        return None
    if when_made.startswith("before_"):
        return int(when_made[len("before_"):]) - 1
    if "_" in when_made:
        return int(when_made.split("_")[1])
    if when_made.endswith("s"):
        return int(when_made[:-1]) + 9
    return None


def is_vintage(when_made: str, year: Optional[int] = None) -> bool:
    """Whether a when_made bucket lies wholly VINTAGE_YEARS or more in the
    past — computed, not listed, because Etsy renames the top bucket every
    January and the line moves with the calendar."""
    latest = when_made_latest_year(when_made)
    if latest is None:
        return False
    year = year or _dt.date.today().year
    return latest <= year - VINTAGE_YEARS


def needs_production_partner(listing: Listing, year: Optional[int] = None) -> bool:
    """Etsy's third category, the one this app cannot fill in for the seller.

    "Someone else made it" and it is neither vintage nor a supply means
    handmade by a production partner, and Etsy then requires that partner
    named on the listing (production_partner_ids) — a relationship set up in
    Shop Manager, not a field to type here. For a reseller it is the polite
    name for "this is not allowed on Etsy", which is why the issue points at
    the two answers that would make it allowed rather than at the partner.
    """
    e = listing.etsy
    return (e.who_made == "someone_else" and not e.is_supply
            and e.when_made in WHEN_MADE and not is_vintage(e.when_made, year))


def _digits(value) -> str:
    """An Etsy numeric id as a string, or "" for anything that is not one.

    Ids reach here from Settings and from the editor as whatever string the
    client sent; int() on a stray letter used to escape the provider's own
    try block and crash the publish with a Python message. A value that is
    not an id is treated as no value, and the preflight then names the field.
    """
    text = str(value or "").strip()
    return text if text.isdigit() else ""


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
    return (_digits(listing.etsy.shipping_profile_id)
            or _digits(settings.get("shipping_profile_id")))


def return_policy_for(listing: Listing, settings: dict) -> str:
    return (_digits(listing.etsy.return_policy_id)
            or _digits(settings.get("return_policy_id")))


def readiness_state_for(listing: Listing, settings: dict) -> str:
    """The processing profile (Etsy: readiness state), same precedence."""
    return (_digits(listing.etsy.readiness_state_id)
            or _digits(settings.get("readiness_state_id")))


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
        # Said explicitly rather than left to Etsy's default: every rule that
        # follows (shipping profile, processing profile) is conditional on
        # the listing being physical, and everything this app photographs is.
        "type": "physical",
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
    readiness = readiness_state_for(listing, settings)
    if readiness:
        payload["readiness_state_id"] = int(readiness)
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


def build_inventory_body(listing: Listing, current: dict, settings: dict) -> dict:
    """The updateListingInventory body: the listing's price and stock, on the
    inventory record Etsy already holds.

    The PUT replaces the whole record, so everything that is not this app's
    to change — the SKU, the property values, the three *_on_property lists —
    is carried over from `current` exactly as read. Only the offerings move:
    one per product, at the listing's price and quantity, enabled, with the
    processing profile the listing resolves to (or the one Etsy already had).

    Refuses a listing with more than one product: that is variations, which
    this app has no model for, and a single price written across them would
    flatten a shirt's S/M/L into one stock count. The eBay side has the same
    rule (has_variations).
    """
    products = list((current or {}).get("products") or [])
    if len(products) > 1:
        raise ValueError("This Etsy listing has variations — change its price "
                         "and stock on Etsy.")
    product = products[0] if products else {}
    offerings = list(product.get("offerings") or []) or [{}]
    price = round(float(listing.price or 0), 2)
    quantity = max(int(listing.quantity or 1), 1)
    readiness = readiness_state_for(listing, settings)
    rebuilt = []
    for offering in offerings:
        entry = {"price": price, "quantity": quantity, "is_enabled": True}
        state = readiness or _digits((offering or {}).get("readiness_state_id"))
        if state:
            entry["readiness_state_id"] = int(state)
        rebuilt.append(entry)
    return {
        "products": [{
            "sku": str(product.get("sku") or ""),
            "property_values": list(product.get("property_values") or []),
            "offerings": rebuilt,
        }],
        "price_on_property": list((current or {}).get("price_on_property") or []),
        "quantity_on_property": list((current or {}).get("quantity_on_property") or []),
        "sku_on_property": list((current or {}).get("sku_on_property") or []),
    }


def preflight(listing: Listing, settings: dict, mode: str = "live") -> list[dict]:
    """Everything Etsy would reject, as UI-ready issues in the same
    {target, level, title, fix} shape the eBay preflight uses. Targets are
    namespaced etsy_* so the editor can jump to the Etsy card.

    `mode` is "draft", "live" or "revise". Nearly every rule is an error in
    all three, because createDraftListing itself refuses a listing without
    a category, attribution, a shipping profile or a processing profile —
    a draft that skipped them would only move the refusal from here, where
    the field is named, to Etsy, where it is a sentence. What a draft may
    still leave for later is what Etsy checks at ACTIVATION: a photo and a
    return policy come back as warnings so the seller sees them before the
    day they press Publish live.
    """
    issues: list[dict] = []
    strict = mode != "draft"

    def add(target: str, title: str, fix: str, level: str = "error") -> None:
        issues.append({"target": target, "level": level, "title": title, "fix": fix})

    if listing.listing_format != "FIXED_PRICE":
        add("format", "Etsy doesn't support auctions",
            "Switch the listing to Buy It Now (fixed price), or unselect Etsy "
            "for this publish.")
    if listing.has_variations:
        add("variations", "This listing has variations",
            "Etsy gets one price and one stock count from this app, and this "
            "listing has sizes or colours with their own. List it on Etsy by "
            "hand, or unselect Etsy for this publish.")
    if not (listing.images or []) and not (listing.image_urls or []):
        add("photos", "At least one photo is required",
            "Add a photo — Etsy requires at least one image on every listing.",
            level="error" if strict else "warn")
    if not (listing.title or "").strip():
        add("title", "Title is missing", "Give the listing a title.")
    if not strip_html(listing.description):
        add("description", "Description is missing",
            "Write a description — Etsy requires one.")
    price = float(listing.price or 0)
    if price < PRICE_FLOOR:
        # In the listing's own currency, and without "20 cents": an Etsy shop
        # sets its own currency, and both the symbol and the word were claims
        # about money a seller outside the US does not use.
        floor = money(PRICE_FLOOR, listing.currency)
        add("price", f"Etsy's minimum price is {floor}",
            f"Raise the price — Etsy rejects listings under {floor}.")
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
    elif needs_production_partner(listing):
        add("etsy_attribution", "Etsy needs a production partner for this item",
            "An item someone else made counts as handmade with a partner "
            f"unless it is vintage ({VINTAGE_YEARS}+ years old) or a craft "
            "supply, and Etsy then wants that partner named in Shop Manager. "
            f"Set when it was made to {VINTAGE_YEARS}+ years ago, tick craft "
            "supply, or choose \"I did\".")
    if not shipping_profile_for(listing, settings):
        add("etsy_shipping_profile", "No Etsy shipping profile",
            "Pick a shipping profile in the Etsy section, or set a default "
            "under Settings → Cross-posting marketplaces → Etsy.")
    if not readiness_state_for(listing, settings):
        add("etsy_readiness_state", "No Etsy processing time",
            "Pick a processing profile in the Etsy section, or set a default "
            "under Settings → Cross-posting marketplaces → Etsy — Etsy "
            "requires one on every physical listing.")
    if not return_policy_for(listing, settings):
        add("etsy_return_policy", "No Etsy return policy",
            "Pick a return policy in the Etsy section, or set a default under "
            "Settings → Cross-posting marketplaces → Etsy — Etsy requires one "
            "before a physical listing can go live.",
            level="error" if strict else "warn")
    shop_currency = str(settings.get("currency_code") or "").strip().upper()
    if shop_currency and (listing.currency or "").strip().upper() != shop_currency:
        listing_currency = (listing.currency or "").strip().upper() or "another currency"
        add("price", f"Your Etsy shop prices in {shop_currency}",
            f"This listing's price is in {listing_currency}, and Etsy will "
            f"read the number as {shop_currency}. Convert the price, or "
            "unselect Etsy for this publish.",
            level="warn")
    return issues

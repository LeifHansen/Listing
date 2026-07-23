"""Pre-publish validation: everything eBay requires, checked BEFORE we call eBay.

eBay's publishOffer requirements (developer.ebay.com → "Required fields for
publishing an offer") boil down to:

  Inventory item : ≥1 image, title (≤80), condition, quantity ≥1,
                   category-required item specifics (aspects),
                   package weight (when the fulfillment policy ships by weight)
  Offer          : price (≥ $0.99 on EBAY_US), leaf categoryId,
                   fulfillment + payment + return policy ids,
                   merchantLocationKey
  Account        : payments opted in, policies exist, location created

On top of that, individual shipping services carry their own package limits —
the classic trap being eBay Standard Envelope (3 oz max, flats only), which
surfaces as the opaque publish error 25007 AFTER everything else looks fine.
This module turns all of it into a checklist the UI can show up front.
"""
from __future__ import annotations

from typing import Optional

from ..models import Listing

# Per-service package weight caps, in ounces, matched case-insensitively as
# substrings of eBay's shippingServiceCode. Only services with caps BELOW the
# common 70 lb parcel ceiling matter here — anything unlisted is effectively
# unconstrained for resale-sized packages.
SERVICE_WEIGHT_CAPS_OZ: list[tuple[str, float, str, str]] = [
    ("standardenvelope", 3.0, "eBay Standard Envelope",
     "flats only: cards, coins, stamps and similar — max 3 oz"),
    ("firstclass", 15.9, "USPS First Class",
     "USPS First Class packages max out just under 1 lb"),
    ("uspsground", 70.0 * 16, "USPS Ground Advantage", ""),
    ("mediamail", 70.0 * 16, "USPS Media Mail", ""),
]

EBAY_MIN_PRICE = 0.99  # EBAY_US fixed-price minimum
MAX_PHOTOS = 24


def weight_oz(listing: Listing) -> float:
    return (listing.package_weight_lb or 0) * 16.0 + (listing.package_weight_oz or 0)


def service_cap(service_code: str) -> Optional[tuple[float, str, str]]:
    """(cap_oz, friendly_name, note) for a service code, if we know its cap."""
    code = (service_code or "").lower().replace("_", "").replace(" ", "")
    for frag, cap, name, note in SERVICE_WEIGHT_CAPS_OZ:
        if frag in code:
            return cap, name, note
    return None


def check_weight_vs_services(listing: Listing, services: list[dict]) -> list[dict]:
    """Flag services in the chosen fulfillment policy that can't carry this
    package. services: [{"code": ..., "name": ...}] from the policy detail."""
    w = weight_oz(listing)
    if w <= 0 or not services:
        return []
    issues = []
    for svc in services:
        cap = service_cap(svc.get("code", ""))
        if cap and w > cap[0]:
            cap_oz, name, note = cap
            pretty_cap = f"{cap_oz:g} oz" if cap_oz < 16 else f"{cap_oz / 16:g} lb"
            issues.append({
                "target": "shipping",
                "level": "error",
                "title": f"Too heavy for {name} in your shipping policy",
                "fix": (f"This package weighs {w:g} oz but {name} maxes out at {pretty_cap}"
                        + (f" ({note})" if note else "") +
                        ". Pick a different shipping service on the Shipping card — "
                        "USPS Ground Advantage handles up to 70 lb."),
            })
    return issues


def validate(listing: Listing, mode: str, *,
             has_fulfillment: bool, has_payment: bool, has_return: bool,
             has_location: bool, connected: bool,
             policy_services: Optional[list[dict]] = None,
             required_aspects: Optional[list[str]] = None) -> list[dict]:
    """Everything eBay will reject at publish time, as UI-ready issues.

    Draft mode checks only what createOrReplaceInventoryItem needs (title,
    photo); live mode checks the full publishOffer contract. Each issue is
    {target, level ('error'|'warn'), title, fix}.
    """
    issues: list[dict] = []

    def add(target: str, title: str, fix: str, level: str = "error") -> None:
        issues.append({"target": target, "level": level, "title": title, "fix": fix})

    # --- inventory item ---
    if not (listing.images or []):
        add("photos", "At least one photo is required",
            "Add a photo — listings without photos can't be saved to eBay.")
    elif len(listing.images) > MAX_PHOTOS:
        add("photos", f"Too many photos ({len(listing.images)})",
            f"eBay allows up to {MAX_PHOTOS} photos — remove a few.")
    if not (listing.title or "").strip():
        add("title", "A title is required", "Give the listing a title (up to 80 characters).")
    elif len(listing.title) > 80:
        add("title", "The title is over 80 characters", "Shorten the title to 80 characters or fewer.")
    if not (listing.condition or "").strip():
        add("condition", "A condition is required", "Pick a condition on the Pricing card.")

    if mode != "live":
        return issues

    # --- offer ---
    fmt = (getattr(listing, "listing_format", "") or "FIXED_PRICE").upper()
    if fmt in ("AUCTION", "AUCTION_BIN"):
        start = getattr(listing, "auction_start_price", None)
        if start is None or start <= 0:
            add("price", "A starting bid is required",
                "Set the starting bid on the Pricing card.")
        if fmt == "AUCTION_BIN" and (listing.price is None or listing.price <= 0):
            add("price", "A Buy It Now price is required",
                "Set the Buy It Now price, or switch to a plain auction.")
    else:
        if listing.price is None or listing.price <= 0:
            add("price", "A price is required", "Set a price on the Pricing card.")
        elif listing.price < EBAY_MIN_PRICE:
            add("price", f"eBay's minimum price is ${EBAY_MIN_PRICE:.2f}",
                f"Raise the price to at least ${EBAY_MIN_PRICE:.2f}.")
        if (listing.quantity or 0) < 1:
            add("price", "Quantity must be at least 1", "Set the quantity to 1 or more.")
    cid = (listing.category_id or "").strip()
    if not cid:
        add("category", "An eBay category is required",
            "Use “Suggest eBay categories” on the Category card and pick the closest match.")
    elif not cid.isdigit():
        add("category", "The eBay category ID must be numeric",
            "Pick a category from the suggestions — the ID fills in automatically.")
    if not (listing.description or "").strip():
        add("description", "No description yet",
            "eBay accepts it (we fall back to the title), but a real description sells better.",
            level="warn")

    # --- shipping ---
    if weight_oz(listing) <= 0:
        add("weight", "eBay needs a package weight to publish",
            "Enter the shipping weight (lb / oz) on the Shipping card.")
    issues.extend(check_weight_vs_services(listing, policy_services or []))

    # --- account prerequisites ---
    if not has_fulfillment:
        add("shipping", "No shipping service selected",
            "Pick a shipping service on the Shipping card (or a default in Settings).")
    if not has_payment:
        add("policies", "No payment policy selected", "Choose a payment policy in Settings.")
    if not has_return:
        add("policies", "No return policy selected", "Choose a return policy in Settings.")
    if not has_location:
        add("location", "No ship-from location set",
            "Add your ship-from ZIP in Settings and save — we create the eBay location for you.")
    if not connected:
        add("generic", "No eBay account connected",
            "Publishing will run as a dry-run payload until you connect eBay.", level="warn")

    # --- category-required item specifics ---
    if required_aspects:
        have = {(s.name or "").strip().lower() for s in listing.item_specifics if s.value}
        if listing.brand:
            have.add("brand")
        missing = [a for a in required_aspects if a.strip().lower() not in have]
        if missing:
            add("specifics",
                "Missing required item specifics: " + ", ".join(missing[:6])
                + ("…" if len(missing) > 6 else ""),
                "Fill these on the Item specifics card — eBay requires them for this category.")
    return issues


def errors_only(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("level", "error") == "error"]

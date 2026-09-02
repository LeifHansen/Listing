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

from ..money import money
from ..models import TITLE_MAX_CHARS, Listing

# Per-service package weight caps, in ounces, matched case-insensitively as
# substrings of eBay's shippingServiceCode. Only services with caps BELOW the
# common 70 lb parcel ceiling matter here — anything unlisted is effectively
# unconstrained for resale-sized packages.
#
# Mirrored by SERVICE_CAPS_OZ in frontend/src/views/listing/cards.jsx, which
# warns as the seller types. This list is the authority; keep the two in step.
SERVICE_WEIGHT_CAPS_OZ: list[tuple[str, float, str, str]] = [
    ("standardenvelope", 3.0, "eBay Standard Envelope",
     "flats only: cards, coins, stamps and similar — max 3 oz"),
    ("firstclass", 15.9, "USPS First Class",
     "USPS First Class packages max out just under 1 lb"),
    ("uspsground", 70.0 * 16, "USPS Ground Advantage", ""),
    ("mediamail", 70.0 * 16, "USPS Media Mail", ""),
]

# eBay's fixed-price minimum. The figure is EBAY_US's; the other sites this
# app can be pointed at (EBAY_GB, EBAY_DE, EBAY_AU, EBAY_CA -- see
# ebay_trading._site_id) are ASSUMED to use the same number in their own
# currency, which is why the message quotes it in the listing's currency
# rather than in dollars. The number is not site-verified; the currency label
# at least stops it naming money the seller does not use.
EBAY_MIN_PRICE = 0.99
MAX_PHOTOS = 24

# The short name of each thing an issue can point at, by target. This is
# what a seller sees on the publish bar's jump chips ("Package weight"),
# so it names the FIELD they have to touch — not the rule that failed.
FIELD_LABELS: dict[str, str] = {
    "photos": "Photos",
    "title": "Title",
    "condition": "Condition",
    "price": "Price",
    "category": "eBay category",
    "specifics": "Required item specifics",
    "weight": "Package weight",
    "shipping": "Shipping service",
    "policies": "Business policies",
    "location": "Ship-from location",
    "description": "Description",
}


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
                "blocking": True,
                "field": FIELD_LABELS["shipping"],
                "title": f"Too heavy for {name} in your shipping policy",
                "fix": (f"This package weighs {w:g} oz but {name} maxes out at {pretty_cap}"
                        + (f" ({note})" if note else "") +
                        ". Pick a shipping policy on the Shipping card that uses a "
                        "different service — USPS Ground Advantage handles up to 70 lb."),
            })
    return issues


def condition_label(enum: str) -> str:
    """"USED_GOOD" -> "Used Good". eBay's own wording for the grade is better
    and the checklist uses it wherever the lookup supplied one; this is for
    the value the listing is carrying, which by definition eBay did not."""
    return (enum or "").replace("_", " ").title()


def _check_condition_fits_category(listing: Listing,
                                   allowed_conditions: Optional[list[dict]],
                                   add) -> None:
    """The condition has to be one the CATEGORY offers, not just one eBay has.

    eBay publishes a different ladder per category — Very Good / Good /
    Acceptable only in media, the pre-owned grades only in apparel, one plain
    "Used" across most of the rest — and rejects anything else with error
    25021 ("condition id is invalid for the selected primary category"). That
    rejection costs a whole publish and names nothing the seller can act on,
    so it is worth catching here, where the fix is a dropdown away.

    `allowed_conditions` is eBay's own answer for this category
    ([{enum, id, label}]) or None when we could not ask — and None means the
    check is skipped, never that the category allows everything.
    """
    if not allowed_conditions:
        return
    cond = (listing.condition or "").strip().upper()
    if any(cond == (c.get("enum") or "").upper() for c in allowed_conditions):
        return
    names = ", ".join(c.get("label") or condition_label(c.get("enum", ""))
                      for c in allowed_conditions[:8])
    add("condition",
        f"eBay doesn't offer “{condition_label(cond)}” in this category",
        f"Pick one of the conditions this category takes: {names}. "
        "(Categories differ — clothing has its own pre-owned grades, and "
        "Very Good / Good / Acceptable exist only for media.)")


def validate(listing: Listing, mode: str, *,
             has_fulfillment: bool, has_payment: bool, has_return: bool,
             has_location: bool, connected: bool,
             policy_services: Optional[list[dict]] = None,
             required_aspects: Optional[list[str]] = None,
             allowed_conditions: Optional[list[dict]] = None) -> list[dict]:
    """Everything eBay will reject at publish time, as UI-ready issues.

    Draft mode checks only what createOrReplaceInventoryItem needs (title,
    photo); live mode checks the full publishOffer contract; revise mode sits
    between the two — the content an edit pushes to an already-live listing,
    without the account/package prerequisites that listing already met. Each
    issue is
    {target, level ('error'|'warn'), blocking, field, title, fix} — see add()
    for what `blocking` and `field` are for.
    """
    issues: list[dict] = []

    def add(target: str, title: str, fix: str, level: str = "error",
            field: str = "", fields: Optional[list[str]] = None) -> None:
        """One checklist entry.

        `blocking` is the whole point of the list: True means eBay refuses
        the listing until this is fixed, False means it publishes fine and
        this is only advice. The UI shows the two in separate groups and
        counts only the blocking ones, so a seller never hunts through
        suggestions looking for what actually stopped the publish.

        `field` is the short name of the thing to fix ("Title", "Package
        weight") — what the publish bar's jump chips are labelled with.

        `fields` is different, and only some checks can fill it: the NAMES of
        the individual inputs at fault ("Sleeve Length", "Size Type"), which
        the editor rings so a seller isn't left comparing a sentence against
        forty item specifics to work out which one it means.
        """
        issues.append({"target": target, "level": level,
                       "blocking": level != "warn",
                       "field": field or FIELD_LABELS.get(target, ""),
                       "fields": list(fields or []),
                       "title": title, "fix": fix})

    # A listing with eBay variations, and nothing else. This app has no
    # variation model: the listing imported as one flat record with a single
    # price and quantity, and a revise would send an item-level Quantity into
    # a structure eBay says ReviseItem cannot revise, where a variation
    # reaching zero is REMOVED from the listing.
    #
    # Returned alone because it is not a field to go and fix, and listing the
    # usual checklist beside it would read as "correct these and it will
    # publish" -- which is not true and never will be until variations are
    # modelled. The browser makes the same call in views/listing/blockers.js;
    # this is the authority.
    if listing.has_variations and mode != "draft":
        add("variations",
            "This listing has size or colour variations",
            "Thryft Shop can't edit those yet — changing it here could remove "
            "them. Edit it on eBay in Seller Hub instead.",
            field="Variations")
        return issues

    # --- inventory item ---
    # An imported eBay listing has no local files — its photos are the
    # eBay-hosted image_urls, which satisfy the requirement just the same.
    if not (listing.images or []) and not (listing.image_urls or []):
        add("photos", "At least one photo is required",
            "Add a photo — listings without photos can't be saved to eBay.")
    elif len(listing.images) > MAX_PHOTOS:
        add("photos", f"Too many photos ({len(listing.images)})",
            f"eBay allows up to {MAX_PHOTOS} photos — remove a few.")
    if not (listing.title or "").strip():
        add("title", "A title is required",
            f"Give the listing a title (up to {TITLE_MAX_CHARS} characters).")
    elif len(listing.title) > TITLE_MAX_CHARS:
        add("title", f"The title is over {TITLE_MAX_CHARS} characters",
            f"Shorten the title to {TITLE_MAX_CHARS} characters or fewer.")
    if not (listing.condition or "").strip():
        add("condition", "A condition is required", "Pick a condition on the Pricing card.")
    else:
        _check_condition_fits_category(listing, allowed_conditions, add)

    if mode not in ("live", "revise"):
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
            floor = money(EBAY_MIN_PRICE, listing.currency)
            add("price", f"eBay's minimum price is {floor}",
                f"Raise the price to at least {floor}.")
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

    if mode != "live":
        # "revise" edits a listing eBay has already accepted. Everything above
        # is content this app is about to send, so it still has to be valid.
        # Everything below is about the package and the account setup, which
        # the live listing already satisfies — a revise doesn't resend the
        # business policies, and an imported listing often never carried a
        # local package weight or the category's full aspect list. Demanding
        # them here would block edits to listings that are publishing fine.
        return issues

    # --- shipping ---
    if weight_oz(listing) <= 0:
        add("weight", "eBay needs a package weight to publish",
            "Enter the shipping weight (lb / oz) on the Shipping card.")
    issues.extend(check_weight_vs_services(listing, policy_services or []))

    # --- account prerequisites ---
    if not has_fulfillment:
        add("shipping", "No shipping policy selected",
            "Pick a shipping policy on the Shipping card, or set a default in "
            "Settings — it's what tells eBay which carrier service to use.")
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
        # Any row for the aspect that carries a value answers it — an aspect
        # can own several rows (eBay's multi-selects) and a blank one can sit
        # in front of the real answer. A value of nothing but spaces is not an
        # answer, and eBay does not accept it as one.
        have = {(s.name or "").strip().lower() for s in listing.item_specifics
                if (s.value or "").strip()}
        if listing.brand:
            have.add("brand")
        missing = [a for a in required_aspects if a.strip().lower() not in have]
        if missing:
            add("specifics",
                "Missing required item specifics: " + ", ".join(missing[:6])
                + ("…" if len(missing) > 6 else ""),
                "Fill these on the Item specifics card — eBay requires them for this category.",
                fields=missing)
    return issues


def errors_only(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("level", "error") == "error"]

"""The field alignment map: one listing, three vocabularies.

A listing here is eBay-shaped (models.Listing grew up as "a full eBay
listing draft"), and Etsy asks different questions of the same item. This
module writes the alignment down ONCE — for each field: where it lives on
our record, what it is on eBay, what it becomes on Etsy, and how it gets
filled when a listing crosses from one to the other:

  shared   the value is the same on both sides and copied as-is
  derived  Etsy's value is computed from ours (a tidied title, tags from
           the item specifics, a category matched then confirmed)
  manual   Etsy asks something eBay never did; the seller answers, once
           per batch in the crosspost or per listing on the Etsy card
  absent   Etsy has no such field; the value stays here

It is read by three things and must agree with a fourth. The crosspost
review reads it to say what will be filled and what is still wanted; the
README's alignment table is written from it; the client mirrors it as a
JSON literal (frontend/src/lib/fieldMap.js) so a listing can be judged
"Etsy-ready" without a round trip; and every `etsy_*` target that
mapping_etsy.preflight can emit has to name a rule here, which is what
keeps this a map of the code rather than a wish about it
(test_the_field_map_names_every_etsy_preflight_target).

Pure on purpose: dataclasses and the mapping module only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..models import Listing
from . import mapping_etsy

SHARED, DERIVED, MANUAL, ABSENT = "shared", "derived", "manual", "absent"
FILLS = (SHARED, DERIVED, MANUAL, ABSENT)


@dataclass(frozen=True)
class FieldRule:
    key: str                  # stable id ("title", "readiness_state", ...)
    label: str                # seller-facing name
    ours: str                 # where it lives on Listing ("etsy.taxonomy_id")
    ebay: str                 # what it is on eBay ("" = nothing there)
    etsy: str                 # what it becomes on Etsy ("" = nothing there)
    fill: str                 # SHARED | DERIVED | MANUAL | ABSENT
    derive: str = ""          # the rule, when DERIVED (a name, for the UI)
    required_for_etsy: bool = False
    preflight_target: str = ""   # the mapping_etsy.preflight target it maps to
    note: str = ""


FIELD_MAP: tuple[FieldRule, ...] = (
    FieldRule("title", "Title", "title", "Title", "title", DERIVED,
              derive="clean_title", required_for_etsy=True, preflight_target="title",
              note="eBay allows 80 characters and capitals; Etsy 140, no $ ^ ` and "
                   "few words in capitals — tidied on the way, shown before sending"),
    FieldRule("description", "Description", "description", "Description",
              "description", DERIVED, derive="strip_html+condition",
              required_for_etsy=True, preflight_target="description",
              note="Plain text on Etsy; the condition is written in, because Etsy "
                   "has no condition field"),
    FieldRule("price", "Price", "price", "StartPrice", "price", SHARED,
              required_for_etsy=True, preflight_target="price",
              note="Etsy's floor is 0.20 in the shop's currency; on a revise it "
                   "travels through the inventory record"),
    FieldRule("currency", "Currency", "currency", "Currency", "", SHARED,
              preflight_target="price",
              note="An Etsy shop prices in one currency; a listing in another "
                   "is a warning"),
    FieldRule("quantity", "Quantity", "quantity", "Quantity", "quantity", SHARED,
              required_for_etsy=True,
              note="One number; a listing with variations is refused for Etsy"),
    FieldRule("photos", "Photos", "images | image_urls", "PictureDetails",
              "images", SHARED, required_for_etsy=True, preflight_target="photos",
              note="Etsy takes up to 10 photo files (bytes, not URLs); an "
                   "imported eBay listing's photos are fetched and re-uploaded"),
    FieldRule("format", "Selling format", "listing_format", "ListingType", "",
              SHARED, required_for_etsy=True, preflight_target="format",
              note="Etsy has no auctions — Buy It Now only"),
    FieldRule("condition", "Condition", "condition | condition_description",
              "ConditionID", "", DERIVED, derive="strip_html+condition",
              note="Etsy has no condition field; it is appended to the description"),
    FieldRule("brand", "Brand", "brand", "Brand (item specific)", "tags", DERIVED,
              derive="tags_from_brand_and_specifics",
              note="Becomes the first Etsy search tag"),
    FieldRule("item_specifics", "Item specifics", "item_specifics",
              "ItemSpecifics", "tags", DERIVED, derive="tags_from_brand_and_specifics",
              note="Values become Etsy search tags: up to 13, 20 characters each, "
                   "accents plain"),
    FieldRule("materials", "Materials", "etsy.materials | item_specifics[Material]",
              "Material (item specific)", "materials", DERIVED,
              derive="materials_from_specifics",
              note="Etsy shows materials on the listing; read off the Material specific"),
    FieldRule("package", "Package weight and size",
              "package_weight_lb/oz | package_*_in", "ShippingPackageDetails",
              "item_weight | item_dimensions", SHARED,
              note="Feeds Etsy's calculated shipping when present"),
    FieldRule("category", "Category", "etsy.taxonomy_id",
              "PrimaryCategory (category_id)", "taxonomy_id", DERIVED,
              derive="taxonomy_suggest", required_for_etsy=True,
              preflight_target="etsy_taxonomy",
              note="Etsy's tree is its own: the eBay category path is matched to "
                   "it first, the AI picks from a shortlist when it isn't, and "
                   "the seller confirms"),
    FieldRule("who_made", "Who made it", "etsy.who_made", "", "who_made", MANUAL,
              required_for_etsy=True, preflight_target="etsy_attribution",
              note="Etsy's policy question — handmade, vintage or supplies — "
                   "answered once per crosspost batch, never defaulted"),
    FieldRule("when_made", "When it was made", "etsy.when_made",
              "Decade / Era / Year (item specifics)", "when_made", DERIVED,
              derive="when_made_from_specifics", required_for_etsy=True,
              preflight_target="etsy_attribution",
              note="Read off a decade or year specific, or a year in the title, "
                   "when there is one; otherwise the batch default or the seller"),
    FieldRule("is_supply", "Craft supply", "etsy.is_supply", "", "is_supply", MANUAL,
              preflight_target="etsy_attribution",
              note="Etsy's third allowed kind of item"),
    FieldRule("shipping_profile", "Shipping profile", "etsy.shipping_profile_id",
              "fulfillment policy (business policy)", "shipping_profile_id", MANUAL,
              required_for_etsy=True, preflight_target="etsy_shipping_profile",
              note="Account default under Settings, per-listing override on the Etsy card"),
    FieldRule("return_policy", "Return policy", "etsy.return_policy_id",
              "return policy (business policy)", "return_policy_id", MANUAL,
              required_for_etsy=True, preflight_target="etsy_return_policy",
              note="Required before an Etsy listing goes live; a draft may wait"),
    FieldRule("readiness_state", "Processing time", "etsy.readiness_state_id",
              "DispatchTimeMax", "readiness_state_id", MANUAL,
              required_for_etsy=True, preflight_target="etsy_readiness_state",
              note="Etsy's processing profile, required on every physical listing"),
    FieldRule("variations", "Variations", "has_variations", "Variations", "", ABSENT,
              preflight_target="variations",
              note="No variation model here; a listing with them is refused for Etsy"),
    FieldRule("subtitle", "Subtitle", "subtitle", "Subtitle", "", ABSENT,
              note="An eBay paid upgrade; Etsy has nothing like it"),
    FieldRule("store_category", "Store category", "store_category_id",
              "StoreCategoryID", "", ABSENT,
              note="The seller's own eBay Store shelf; Etsy shop sections are "
                   "not mapped yet"),
    FieldRule("promote", "Promoted Listings", "promote | ad_rate_percent",
              "Promoted Listings", "", ABSENT, note="eBay only"),
    FieldRule("sku", "SKU", "sku", "SKU", "", ABSENT,
              note="Etsy's SKU lives on the inventory product and is carried "
                   "over on a revise, never written"),
)


def as_json() -> list[dict]:
    """The map as plain dicts — what the client's copy is compared against."""
    return [asdict(rule) for rule in FIELD_MAP]


def etsy_required() -> tuple[FieldRule, ...]:
    return tuple(rule for rule in FIELD_MAP if rule.required_for_etsy)


def rule(key: str) -> FieldRule:
    for candidate in FIELD_MAP:
        if candidate.key == key:
            return candidate
    raise KeyError(key)


def readiness(listing: Listing, settings: dict, mode: str = "live") -> dict:
    """One listing's distance from Etsy, in the map's terms.

    `issues` is mapping_etsy.preflight's answer, unchanged; `missing` names
    the rules behind its errors, in map order; `derived` names what the
    crosspost will fill for this listing without asking; `ready` is whether
    nothing is missing.
    """
    issues = mapping_etsy.preflight(listing, settings, mode)
    errors = {i["target"] for i in issues if i.get("level", "error") == "error"}
    # Only the rules Etsy REQUIRES: `etsy_attribution` also covers the
    # optional craft-supply answer, and "missing: is_supply" would ask for
    # something nobody has to give.
    missing = [rule.key for rule in FIELD_MAP
               if rule.required_for_etsy and rule.preflight_target in errors]
    derived: list[str] = []
    title = (listing.title or "").strip()
    if title and mapping_etsy.clean_title(title) != title:
        derived.append("title")
    derived.append("description")
    if not listing.etsy.taxonomy_id:
        derived.append("category")
    if listing.etsy.when_made not in mapping_etsy.WHEN_MADE:
        derived.append("when_made")
    if mapping_etsy.build_tags(listing):
        derived.append("item_specifics")
    if mapping_etsy.build_materials(listing):
        derived.append("materials")
    return {"ready": not missing, "missing": missing, "derived": derived,
            "issues": issues}

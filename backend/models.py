"""Pydantic data models shared across the app."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ItemSpecific(BaseModel):
    name: str
    value: str
    # Where this value came from, driving the ✓/⚠ badges in the editor:
    # "high" = the AI read it straight off the item (tag, label, print) or
    # it's unambiguous from the photos; "medium" = a reasonable inference the
    # seller should glance over; "" = entered or confirmed by the seller.
    confidence: str = ""


class MarketplaceState(BaseModel):
    """One marketplace's live state for a listing (kept under
    Listing.marketplaces, keyed by marketplace: "ebay", "etsy", ...). The
    server owns this map — publish outcomes write it; client-sent copies are
    replaced with the stored record's before merging, so a stale browser tab
    can never wipe another marketplace's listing id."""

    listing_id: str = ""
    url: str = ""                # public view URL on that marketplace
    status: str = ""             # "" | "draft" | "published" | "ended"
    published_at: str = ""       # ISO-8601 UTC of the first live publish
    error: str = ""              # last failed attempt's message ("" when ok)


class EtsyFields(BaseModel):
    """Etsy-only listing fields (Etsy Open API v3). who_made / when_made are
    deliberately NOT defaulted: Etsy only allows handmade, vintage (20+
    years), and craft supplies, so the seller must attest — a silent default
    could publish a policy-violating listing."""

    taxonomy_id: int = 0         # Etsy seller-taxonomy leaf id
    who_made: str = ""           # i_did | someone_else | collective
    when_made: str = ""          # made_to_order | 2020_2026 | ... | before_1700
    is_supply: bool = False      # craft supply rather than finished item
    materials: list[str] = Field(default_factory=list)   # <=13, shown on the listing
    tags: list[str] = Field(default_factory=list)        # <=13 search tags, <=20 chars
    # Per-listing overrides; "" = use the account defaults saved in Settings.
    shipping_profile_id: str = ""
    return_policy_id: str = ""


class DepopFields(BaseModel):
    """Depop-only listing fields (partner Selling API)."""

    category: str = ""           # Depop category id/slug ("" until chosen)
    size: str = ""               # explicit size; "" = derive from item specifics


class Listing(BaseModel):
    """A full eBay listing draft, editable by the user before publishing."""

    title: str = Field(default="", description="eBay title, max 80 chars")
    subtitle: str = ""
    brand: str = ""
    condition: str = "USED_EXCELLENT"  # eBay condition enum
    condition_description: str = ""
    category_suggestion: str = ""
    category_id: str = ""
    description: str = ""  # HTML-safe plain text / light HTML
    price: Optional[float] = None
    # What the seller PAID for the item (Shop Mode "Buy", or typed in later).
    # Auto-filled from a visible price sticker when the AI can read one;
    # optional — profit reporting works only for items that have it.
    # Profit (once sold) = sale price − purchase_price − fees.
    purchase_price: Optional[float] = None
    currency: str = "USD"
    quantity: int = 1
    # Listing format: FIXED_PRICE (Buy It Now, default), AUCTION, or AUCTION_BIN
    # (an auction that also has a Buy It Now price = `price`). `price` is the
    # fixed / Buy-It-Now price; `auction_start_price` is the starting bid.
    listing_format: str = "FIXED_PRICE"
    auction_start_price: Optional[float] = None
    auction_duration: str = "DAYS_7"  # DAYS_1 | DAYS_3 | DAYS_5 | DAYS_7 | DAYS_10
    # Shipping package — eBay requires a valid package weight to publish an
    # offer. Weight is split pounds + ounces (US); dimensions are optional and
    # only sent when all three are provided (needed for calculated shipping).
    package_weight_lb: float = 0.0
    package_weight_oz: float = 0.0
    package_length_in: float = 0.0
    package_width_in: float = 0.0
    package_height_in: float = 0.0
    # Per-listing shipping service, as an eBay fulfillment-policy id. Empty
    # means "use the account default from Settings".
    fulfillment_policy_id: str = ""
    item_specifics: list[ItemSpecific] = Field(default_factory=list)
    # Promoted Listings (eBay Promoted Listings Standard): when `promote` is on,
    # publishing live also creates an ad at `ad_rate_percent`% — eBay only
    # charges that % of the sale price if the item sells through the promotion.
    promote: bool = False
    ad_rate_percent: float = 0.0
    # filenames (relative to the session image dir) of optimized images
    images: list[str] = Field(default_factory=list)
    # Absolute photo URLs hosted by eBay. Listings IMPORTED from eBay have no
    # local files, so the app renders these directly; app-created listings
    # leave this empty and use `images`.
    image_urls: list[str] = Field(default_factory=list)
    # fields the model was unsure about; surfaced to the user to fill in
    missing_info: list[str] = Field(default_factory=list)
    # Set once the listing goes live: eBay's item id (the /itm/ number).
    # Powers "View on eBay" links and survives every save/publish round-trip.
    ebay_listing_id: str = ""
    # Where this listing came from: "" / "app" = created here (managed through
    # the Inventory API), "ebay" = imported from the seller's eBay account and
    # edited back through the Trading API. Routes every publish/end correctly.
    source: str = ""
    # eBay's SKU for an imported listing, when it has one.
    sku: str = ""
    # ISO-8601 UTC timestamp of when the listing went live on eBay, carried
    # over on import so "most recent first" can mean what the seller expects.
    ebay_start_time: str = ""
    # Live eBay counters carried along on import/sync (display only).
    watch_count: int = 0
    sold_quantity: int = 0
    # eBay's own view URL for an imported listing (avoids guessing the domain).
    view_url: str = ""
    # Per-marketplace publish state, keyed by marketplace ("ebay", "etsy",
    # ...). `ebay_listing_id` above remains the authoritative legacy slot for
    # eBay — the two are mirrored on every publish (marketplaces/state.py).
    marketplaces: dict[str, MarketplaceState] = Field(default_factory=dict)
    # Marketplace-specific listing fields, edited in their own cards.
    etsy: EtsyFields = Field(default_factory=EtsyFields)
    depop: DepopFields = Field(default_factory=DepopFields)


class IdentifyResult(BaseModel):
    listing: Listing
    confidence: str = "medium"
    raw_observations: str = ""


class RefineRequest(BaseModel):
    session_id: str
    listing: Listing
    prompt: str


class PublishRequest(BaseModel):
    session_id: str
    listing: Listing
    mode: str = "draft"  # "draft" or "live"


class SessionOnlyRequest(BaseModel):
    session_id: str

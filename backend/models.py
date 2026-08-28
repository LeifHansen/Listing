"""Pydantic data models shared across the app."""
from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, Field, field_validator

# eBay's hard ceiling on a listing title. Every editable title in the app
# is capped at this — the editor and the bulk queue stop typing at it, and
# the validator below is the backstop for every other write path (AI
# drafts, refine, merge, import, a client that never enforced it). eBay
# rejects the whole publish over a title one character too long, and the
# rejection arrives after the photos have uploaded, so this is the one
# limit worth holding in the model rather than at the API boundary.
TITLE_MAX_CHARS = 80


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

    title: str = Field(default="",
                       description=f"eBay title, max {TITLE_MAX_CHARS} chars")
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
    # WHICH eBay account `ebay_listing_id` lives on, as the eBay username.
    # Records predate this field, so "" means "unknown, assume the connected
    # account". Without it a seller who connects a second eBay account keeps
    # seeing the first account's store: the records are keyed by APP user, the
    # item ids stay on the rows, and GetItem answers for any seller's item, so
    # every sync happily re-confirmed the old account's listings as live under
    # the new one.
    ebay_account: str = ""
    # ISO-8601 UTC timestamp of when the listing went live on eBay, carried
    # over on import so "most recent first" can mean what the seller expects.
    ebay_start_time: str = ""
    # Live eBay counters carried along on import/sync (display only).
    watch_count: int = 0
    sold_quantity: int = 0
    # What the item ACTUALLY sold for, per unit, in `currency`. `price` above
    # is the ASKING price and keeps that meaning forever — an accepted Best
    # Offer, an auction close, or a markdown all settle BELOW it, and eBay
    # reports the real amount only on the transaction. None means "not sold,
    # or eBay hasn't told us what it went for"; the UI falls back to `price`
    # and says so rather than inventing a number.
    sold_price: Optional[float] = None
    # ISO-8601 UTC of the sale — eBay's transaction date when it reported one,
    # otherwise the moment this app first saw the listing flip to sold. This
    # is what the dashboard's sold-in-the-last-N-days tile counts against;
    # updated_at can't stand in for it, because an imported record's timestamp
    # is the listing's START time.
    sold_at: str = ""
    # eBay's own view URL for an imported listing (avoids guessing the domain).
    view_url: str = ""
    # Per-marketplace publish state, keyed by marketplace ("ebay", "etsy",
    # ...). `ebay_listing_id` above remains the authoritative legacy slot for
    # eBay — the two are mirrored on every publish (marketplaces/state.py).
    marketplaces: dict[str, MarketplaceState] = Field(default_factory=dict)
    # Marketplace-specific listing fields, edited in their own cards.
    etsy: EtsyFields = Field(default_factory=EtsyFields)
    depop: DepopFields = Field(default_factory=DepopFields)

    @field_validator("title", mode="before")
    @classmethod
    def _cap_title(cls, value):
        """Trim to TITLE_MAX_CHARS wherever a title enters the model.

        Truncating rather than raising is deliberate: the sources that can
        overrun are the AI draft, a refine, and an old record — none of
        them a seller in a position to fix a 422, and all of them better
        served by a title eBay will accept. The editable surfaces stop at
        80 as the seller types, so a truncation here is never their
        keystrokes being cut off mid-word."""
        if not isinstance(value, str):
            return value
        return value.strip()[:TITLE_MAX_CHARS]


class IdentifyResult(BaseModel):
    listing: Listing
    confidence: str = "medium"
    raw_observations: str = ""
    # Tag/label bounding boxes the identify pass spotted ({photo, box, kind}),
    # consumed by the zoom-and-transcribe step — not shown in the UI.
    tags: list = []
    # True when the server-side enrichment (category item specifics) already
    # ran for this draft — the editor's auto-autofill effect checks this so it
    # doesn't re-run (and re-charge) the same passes right after the preview
    # opens. False when enrichment was skipped (no category, taxonomy down),
    # which is exactly when the client-side fill still earns its keep.
    specifics_autofilled: bool = False


# A session id off the wire. Natively 12 hex chars (storage.new_session_id);
# imported listings carry ids like "ebay-123456789012". Bounded because these
# arrive on routes that need no login, and an unbounded one became a permanent
# dict key in services/publish_guard — a request body could size the process's
# memory. 128 is far above anything the app itself mints.
SessionId = Annotated[str, Field(max_length=128)]


class RefineRequest(BaseModel):
    session_id: SessionId
    listing: Listing
    prompt: str


class ImageOrderRequest(BaseModel):
    """Just the photo order — see PATCH /api/listings/{id}/images/order."""

    images: list[str]


class PublishRequest(BaseModel):
    session_id: SessionId
    listing: Listing
    mode: str = "draft"  # "draft" or "live"
    # Which marketplaces to publish to. Empty (every pre-multi client) means
    # the legacy behavior: eBay only, byte-identical response shape. With
    # entries, the response is the {multi: true, results: {...}} shape and
    # each marketplace succeeds or fails independently.
    marketplaces: list[str] = Field(default_factory=list)


class SessionOnlyRequest(BaseModel):
    session_id: SessionId

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

# eBay's ceiling on a Subtitle. Held here for the same reason as the title:
# one character over and eBay rejects the whole publish, after the photos have
# already uploaded. (Subtitle is a paid listing upgrade -- see the SubtitleFee
# note where the Trading request emits it.)
SUBTITLE_MAX_CHARS = 55

# eBay's own ceiling on a listing description. Nothing this app writes comes
# near it -- the point is that the field had NO bound at all, and every write
# path lands on the volume: `POST /api/publish` needs no login, and each new
# session_id writes a fresh listing.json under /data/sessions. With ~500MB free
# on the 1GB volume, a handful of requests carrying a multi-megabyte
# description filled it, and the orphan sweep only reclaims directories
# untouched for three hours.
#
# Set to what eBay actually accepts, so a real listing -- including one
# imported from eBay with a long HTML description -- is never truncated. The
# bound exists to make one request finite, not to second-guess the marketplace.
DESCRIPTION_MAX_CHARS = 500_000
# Long free-text the seller or the AI can fill in. eBay's limits are far
# tighter than these (subtitle 55, condition description 1000); these are the
# backstop, not the product rule.
TEXT_FIELD_MAX_CHARS = 4_000
# eBay tops out around 30 aspects on a listing; an import can carry more.
MAX_ITEM_SPECIFICS = 500
# eBay defines three condition descriptors for a graded card and one for an
# ungraded one; the bound exists so the list is finite, not to model eBay.
MAX_CONDITION_DESCRIPTORS = 20

# How many videos eBay allows on one listing. The answer today is ONE -- eBay
# says so in its seller help and enforces it by ignoring the rest, which is
# the worst way to find out: the upload succeeds and the listing has no video.
#
# The plumbing either side of this number carries a list anyway (Listing.videos
# below, the repeatable <VideoID> the Trading schema takes), so raising the
# ceiling the day eBay does is this constant and the one beside it in
# services/ebay_video.py. Held here as well because models.py is the layer with
# no eBay dependency -- the route and the editor both need the number, and
# neither should have to import an httpx-backed module to learn it.
MAX_VIDEOS = 1

# How a listing sells. FIXED_PRICE is Buy It Now (the default and the great
# majority); AUCTION takes bids only; AUCTION_BIN is an auction that also
# carries a Buy It Now price. The choice decides which eBay call publishes it
# and which field holds the asking price -- `price` for Buy It Now,
# `auction_start_price` for the opening bid -- so an unrecognised value is not
# a cosmetic problem: it publishes as a Buy It Now at whatever `price` holds.
# Named here so the API boundary can check a value against the same list the
# publisher branches on (services/ebay_trading._item_fields).
LISTING_FORMATS = ("FIXED_PRICE", "AUCTION", "AUCTION_BIN")


class ItemSpecific(BaseModel):
    name: str
    value: str
    # Where this value came from, driving the ✓/⚠ badges in the editor:
    # "high" = the AI read it straight off the item (tag, label, print) or
    # it's unambiguous from the photos; "medium" = a reasonable inference the
    # seller should glance over; "" = entered or confirmed by the seller.
    confidence: str = ""


class ConditionDescriptor(BaseModel):
    """One of eBay's condition DESCRIPTORS -- the second step of the condition
    on a trading card, where "Graded" or "Ungraded" is only half the answer.

    In the three single-card categories (Sports 261328, CCG 183454, Non-Sport
    183050) eBay stopped taking "Used" in 2023. A card is Graded (2750) or
    Ungraded (4000), and each of those REQUIRES descriptors: a graded card
    names its grading service (27501) and grade (27502), optionally its
    certification number (27503, free text); an ungraded one names its card
    condition (40001: Near Mint or Better / Excellent / Very Good / Poor, or
    the CCG played-ness ladder). A listing in those categories without them
    is refused outright, which is why the editor asks in two steps.

    Every id here is eBay's, read from the Sell Metadata API for the
    category (taxonomy.item_conditions) -- nothing in this app invents one.
    The labels ride along so a card, a merge dialog or a sold archive can
    say "PSA 10" without a round trip to eBay for the table."""

    id: str = ""                      # eBay's descriptor id: "27501", "40001", ...
    values: list[str] = Field(default_factory=list)   # eBay's value ids: ["275010"]
    text: str = ""                    # free text where the descriptor takes it (cert no.)
    label: str = ""                   # eBay's wording for the descriptor ("Grade")
    value_labels: list[str] = Field(default_factory=list)  # eBay's wording per value ("10")

    @field_validator("id", "text", "label", mode="before")
    @classmethod
    def _text_fields(cls, value):
        if value is None:
            return ""
        return str(value).strip()[:TEXT_FIELD_MAX_CHARS]

    @field_validator("values", "value_labels", mode="before")
    @classmethod
    def _list_fields(cls, value):
        """A single value arrives as a string as often as a one-item list --
        the Trading API's <Value> is repeatable but a card has one grade."""
        if value is None or value == "":
            return []
        if isinstance(value, (str, int, float)):
            return [str(value).strip()]
        if isinstance(value, list):
            return [str(v).strip() for v in value if v is not None and str(v).strip()]
        return []


class ListingVideo(BaseModel):
    """One video attached to a listing, and where eBay has got to with it.

    A video is not a photo with a bigger file. Photos are pulled -- the
    publish hands eBay a URL and eBay fetches it -- so a photo needs nothing
    on the record but its filename. A video is PUSHED through eBay's Media
    API and comes back as an id, then sits in eBay's moderation queue for
    hours or days before it appears on the listing. So all three facts have
    to be kept: the file this app holds, the id eBay gave it, and what eBay
    last said about it. Losing any one of them means re-uploading 150MB the
    seller already sent, or publishing a listing that silently has no video.

    See services/ebay_video.py for the statuses (eBay's own words, unmapped)
    and for what each one means for a publish.
    """

    # Filename under the session's video dir. Empty for a video that reached
    # us from eBay on an import -- we never held those bytes and never will.
    file: str = ""
    # eBay's id for the video, once the Media API has it. "" while the file is
    # here and eBay has not been asked yet (a draft made before the seller
    # connected eBay, or an upload that failed and will be retried).
    ebay_video_id: str = ""
    # eBay's own status word: PENDING_UPLOAD | PROCESSING | LIVE | BLOCKED |
    # PROCESSING_FAILED, or "" before eBay has seen it.
    status: str = ""
    # Why eBay blocked or failed it, in eBay's words. The seller has no other
    # way to learn this -- the refusal arrives days after they stopped looking.
    message: str = ""
    # Bytes, as stored. Kept because createVideo has to be told the exact size
    # BEFORE the upload and eBay rejects a mismatch, and because it is what
    # the editor shows.
    size: int = 0

    @field_validator("file", "ebay_video_id", "status", "message", mode="before")
    @classmethod
    def _text_fields(cls, value):
        if value is None:
            return ""
        return str(value).strip()[:TEXT_FIELD_MAX_CHARS]

    @field_validator("size", mode="before")
    @classmethod
    def _size_field(cls, value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0


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
    # This eBay listing carries VARIATIONS (a shirt in S/M/L, each with its own
    # SKU, price and stock). This app has no variation model, so such a listing
    # imports as one flat record with a single price and quantity — which is
    # not what it is. The flag exists so nothing pretends otherwise: the record
    # stays visible and end-able, and the revise refuses rather than sending an
    # item-level Quantity into a structure eBay says ReviseItem cannot revise,
    # where a variation reaching 0 is removed from the listing.
    has_variations: bool = False
    brand: str = ""
    condition: str = "USED_EXCELLENT"  # eBay condition enum
    condition_description: str = ""
    # The second half of the condition where eBay wants one: a trading card's
    # grading service + grade (+ certification number) or its card condition.
    # Empty everywhere else. See ConditionDescriptor.
    condition_descriptors: list[ConditionDescriptor] = Field(default_factory=list)
    category_suggestion: str = ""
    category_id: str = ""
    # The seller's OWN storefront category — the left-hand nav of their eBay
    # Store ("Vintage Tees", "Beanie Babies"), not one of eBay's site
    # categories. Their own invention, their own ids, and only sellers with a
    # Store have any: "" means this listing is filed at the store's top level,
    # which is what every listing did before this field existed. Name kept
    # beside the id for the same reason `category_suggestion` sits beside
    # `category_id` — so a card can say where the listing is filed without a
    # round trip to eBay for the tree.
    store_category_id: str = ""
    store_category_name: str = ""
    description: str = ""  # HTML-safe plain text / light HTML
    price: Optional[float] = None
    # What the seller PAID for the item (Shop Mode "Buy", or typed in later).
    # Auto-filled from a visible RESALE sticker — thrift, consignment, outlet —
    # when the AI can read one; optional: profit reporting works only for items
    # that have it. Profit (once sold) = sale price − purchase_price − fees.
    purchase_price: Optional[float] = None
    # MSRP: the price printed on the BRAND'S OWN hang tag, swing ticket or box,
    # read off the photos. A different fact from purchase_price and it must
    # stay one — the seller did not pay it. It is the price anchor under an
    # item that is still new, which is the whole reason it is stored: a shirt
    # whose tag says $130 is not a $49 shirt, and the draft that said it was
    # had thrown that number into purchase_price and stopped looking at it.
    retail_price: Optional[float] = None
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
    # The listing's video(s) -- at most MAX_VIDEOS, which is what eBay allows.
    # A list rather than a single field because the Trading schema repeats
    # <VideoID> and because the day eBay raises its limit nothing here has to
    # change shape. See ListingVideo for why a video carries three facts where
    # a photo carries one.
    videos: list[ListingVideo] = Field(default_factory=list)
    # fields the model was unsure about; surfaced to the user to fill in
    missing_info: list[str] = Field(default_factory=list)
    # How sure the identify pass was of what this item IS: "low", "medium" or
    # "high", as claude_ai.identify reported it, or "" for a listing the AI
    # never drafted (an eBay import, a relist, a stub saved when the AI
    # failed). Stamped by every drafting path in main -- the synchronous
    # route, the polled job behind the uploader and "Start over", the bulk
    # worker -- the moment the draft exists.
    #
    # The same answer used to reach only the editor's header, on the
    # IdentifyResult, and was gone the moment the draft was saved. So the
    # cards a seller actually triages from -- the drafts strip, the bulk
    # queue, the dashboard -- showed forty drafts that all looked equally
    # sure, and the one drafted from photos that settled nothing looked
    # exactly like the ones read off a label. This is a different fact from
    # the per-specific ✓/⚠ flags: those say which FIELDS want a glance, this
    # says whether the title, the brand and the price -- the three a wrong
    # answer costs most on -- were read or guessed.
    #
    # Server-owned (state.SERVER_OWNED_FIELDS): a refine rebuilds the listing
    # from the model's echo and would drop it; a relist clears it on purpose.
    ai_confidence: str = ""
    # When the AI item-specifics fill last actually RAN on this listing
    # (ISO-8601 UTC), set by main._enrich_listing whichever path called it.
    # "" means it has never run — an imported listing, or one whose category
    # or photos were not there at the time.
    #
    # The dashboard's "Fill in details" group is what this exists for. That
    # group used to be built from `missing_info`, which is a different
    # question with a different answer: an imported listing carries no notes
    # at all and so was never offered the fill however blank its specifics
    # were, while an app-made draft carried notes the fill cannot answer
    # ("exact measurements") and was offered it forever, coming back "nothing
    # the photos could answer" every time. A listing the fill has already run
    # on has nothing more to gain from running it again, and this is how the
    # suggestion knows to stop asking.
    enriched_at: str = ""
    # When the seller said "these are fine, stop asking" about the notes the
    # AI left for a person (ISO-8601 UTC), on the server's own clock. ""
    # means they never have.
    #
    # `missing_info` is what the AI declined to invent -- a measurement, a
    # signature to confirm, an exact model number. It is right to leave those
    # to a person, and it is right that "Check details" says so. What was
    # missing is any way for the person to ANSWER: the group is rebuilt from
    # the listing on every load, so a seller with 177 of them faced a list
    # that could only shrink one hand-checked listing at a time and would not
    # otherwise move. Their reply: "I want all of this done and submitted to
    # eBay with one click, not individually."
    #
    # So this is that reply, written down. The notes STAY on the listing --
    # nothing is deleted, and the editor still shows them -- but the chore
    # list stops asking, because the seller has answered it. Nothing about
    # this reaches eBay: `missing_info` is a note to the seller, never
    # listing content, so accepting one publishes nothing and changes nothing
    # a buyer sees.
    #
    # Server-owned (state.SERVER_OWNED_FIELDS), like `enriched_at` and for
    # the same reason: a tab that loaded before the seller accepted carries a
    # blank one, and honouring that blank would put every listing straight
    # back on the list they just cleared.
    notes_accepted_at: str = ""
    # When this listing's asking price was last LOWERED (ISO-8601 UTC), on the
    # server's own clock. "" means it never has been.
    #
    # This is to the dashboard's "Lower prices" group what `enriched_at` above
    # is to "Fill in details", and it exists for the same report. Both price
    # nudges are computed from signals a price drop does not move: the age
    # heuristic counts from `created_at`, which never changes, and the
    # traffic one reads eBay's VIEWS, which are cumulative for the life of the
    # listing and so still say "30 views and no watchers" the second after the
    # price comes down. So the seller pressed "Lower all…", waited through a
    # dozen serial eBay revises, was told "Lowered 12 prices by 10%" — and the
    # group came back in the same slot, same twelve listings, same count,
    # saying the price may be high. Which is indistinguishable from the button
    # having done nothing.
    #
    # Nothing here is a client's to send. It is derived on every write from
    # the price already stored (services/recommender.price_drop_stamp), so a
    # stale tab cannot erase it and a forged payload cannot use it to silence
    # advice — which is why it is NOT in state.SERVER_OWNED_FIELDS: that list
    # makes the STORED value win, and this one has to be able to move forward
    # on the very write that drops the price.
    price_lowered_at: str = ""
    # Why the LAST live publish attempt did not put this listing on the
    # marketplace, in the sentence the seller should read -- "" when the
    # last attempt went live, or when there has never been one.
    #
    # A refusal used to live only in a toast and in one React state map
    # keyed by listing id. Both are gone the moment the page reloads, and
    # what was left behind was a card sitting in Drafts looking exactly
    # like a draft nobody had ever tried to publish -- for a seller who
    # knows perfectly well they pressed Publish. "It says it published and
    # it will not clear from Drafts" is that gap, reported.
    #
    # Only a REFUSAL is recorded. A publish whose answer never came back
    # is not one (the listing may well be live, and painting it refused
    # sends the seller to list it twice), and neither is a dry run or a
    # draft save. Cleared by the publish that succeeds, never by an edit:
    # the card words it as a verdict on the last attempt for exactly that
    # reason.
    #
    # Server-owned (state.SERVER_OWNED_FIELDS): it is the server's record
    # of what a marketplace said, and a client echo must not forge it.
    publish_error: str = ""
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
    # eBay's IMMUTABLE account id for the same account. `ebay_account` above
    # is a display name the seller can change, so it cannot decide ownership:
    # a rename orphans the record, and a released-then-reused handle can make
    # it match a different seller. This is what listing_sync.owns prefers.
    # "" on records written before the field existed.
    ebay_account_id: str = ""
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
    # ISO-8601 UTC of when this listing ENDED without selling — the moment the
    # app filed it that way, whether the seller pressed End or eBay reported
    # it finished. An ended listing this app created is kept for a grace
    # period and then removed automatically (listing_sync.ENDED_GRACE_DAYS),
    # and this is the clock that measures it. `updated_at` cannot: a relist,
    # an edit, or any sweep that touches the row moves it, which would keep
    # renewing the grace period on a listing nobody is coming back for.
    ended_at: str = ""
    # eBay's own view URL for an imported listing (avoids guessing the domain).
    view_url: str = ""
    # Per-marketplace publish state, keyed by marketplace ("ebay", "etsy",
    # ...). `ebay_listing_id` above remains the authoritative legacy slot for
    # eBay — the two are mirrored on every publish (marketplaces/state.py).
    marketplaces: dict[str, MarketplaceState] = Field(default_factory=dict)
    # Marketplace-specific listing fields, edited in their own cards.
    etsy: EtsyFields = Field(default_factory=EtsyFields)
    depop: DepopFields = Field(default_factory=DepopFields)
    # Which fields the SELLER actually changed since this record last agreed
    # with the marketplace. Empty means "nothing known to be edited".
    #
    # A revise used to send every field it could build, on the theory that
    # sending a value equal to the stored one is a no-op. It isn't: the stored
    # value is a snapshot, and eBay's copy may have moved on (Seller Hub, the
    # eBay app, a category remap). Re-sending the snapshot then overwrites the
    # newer value with an older one, and the seller's work vanishes with a
    # success message. Quantity is the sharpest case — see
    # tests/test_ebay_quantity_contract.py — because eBay reads the Quantity
    # on a revise as the new AVAILABLE stock, so re-sending an import-time
    # total puts already-sold units back on sale.
    #
    # What eBay last told us this listing said — the BASE for reconciling it.
    #
    # Without it there are only two versions (ours and eBay's), and two
    # versions cannot tell "the seller edited this here" from "we are holding
    # an old copy of it": both look like a difference. That is why the old
    # sync kept every content field local unless blank, and why a revise then
    # pushed a stale title back over a newer one. See services/sync_merge.py.
    remote_shadow: dict = Field(default_factory=dict)
    # Fields the seller and eBay have BOTH changed since the shadow, held as
    # {"field": {"local": ..., "remote": ...}}. Neither value may be chosen
    # silently, and a conflicted field is never included in a revise.
    conflicts: dict = Field(default_factory=dict)
    # Names are Listing field names ("title", "price", "quantity", ...).
    #
    # A sorted list rather than a set because every listing round-trips
    # through model_dump() into a JSON column and into API responses, and
    # json.dumps has no set. Sorted so a record's serialization is stable and
    # two equal drafts don't diff.
    dirty_fields: list[str] = Field(default_factory=list)

    def mark_dirty(self, *names: str) -> "Listing":
        """Record that the seller edited these fields. Chainable."""
        self.dirty_fields = sorted(set(self.dirty_fields) | {n for n in names if n})
        return self

    def clear_dirty(self, *names: str) -> "Listing":
        """Forget edits that have now been accepted by the marketplace. With
        no names, forgets all of them (the record and eBay agree again)."""
        if names:
            self.dirty_fields = sorted(set(self.dirty_fields) - set(names))
        else:
            self.dirty_fields = []
        return self

    def is_dirty(self, name: str) -> bool:
        """True when `name` is a field the seller explicitly changed.

        Records that predate dirty-tracking carry an empty set. That is
        deliberately read as "nothing to send" rather than "send everything":
        the whole point is that an unproven field must not overwrite a
        marketplace value that may be newer.
        """
        return name in self.dirty_fields

    @field_validator("dirty_fields", mode="before")
    @classmethod
    def _coerce_dirty(cls, value):
        """Accept anything JSON or a caller might hand over — None from an
        older record, a set from calling code — and normalize to the sorted,
        de-duplicated list this field stores."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set, frozenset)):
            return sorted({str(v) for v in value if v})
        return value

    @field_validator("ai_confidence", mode="before")
    @classmethod
    def _known_confidence(cls, value):
        """Only the three levels the UI knows reach the record. The field is
        rendered into the DOM as a chip, so anything else -- a prompt-injected
        string echoed back by a refine, a client's typo -- is treated as
        "the AI never said", the same guard claude_ai puts on the response."""
        level = str(value or "").strip().lower()
        return level if level in ("low", "medium", "high") else ""

    @field_validator("quantity", mode="before")
    @classmethod
    def _floor_quantity_at_zero(cls, value):
        """Zero is a real inventory state (eBay's out-of-stock control), so it
        must survive; negative is not, and eBay has been seen reporting sold
        greater than quantity on variation and out-of-stock listings."""
        if isinstance(value, (int, float)) and value < 0:
            return 0
        return value

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

    @field_validator("description", mode="before")
    @classmethod
    def _cap_description(cls, value):
        """Bound the description, for the reason _cap_title gives above:
        truncating beats a 422 the seller cannot act on. The cap is eBay's own,
        so this only ever fires on something no marketplace would accept."""
        if not isinstance(value, str):
            return value
        return value[:DESCRIPTION_MAX_CHARS]

    @field_validator("subtitle", "condition_description", "brand",
                     "category_suggestion", mode="before")
    @classmethod
    def _cap_text(cls, value):
        """Same rule for the shorter free-text fields."""
        if not isinstance(value, str):
            return value
        return value[:TEXT_FIELD_MAX_CHARS]

    @field_validator("item_specifics", mode="before")
    @classmethod
    def _cap_specifics(cls, value):
        """A listing carries tens of aspects, not thousands. Unbounded, this
        list was the other half of the same unauthenticated write."""
        if not isinstance(value, list):
            return value
        return value[:MAX_ITEM_SPECIFICS]

    @field_validator("condition_descriptors", mode="before")
    @classmethod
    def _cap_descriptors(cls, value):
        """eBay defines a handful of descriptors per condition (three for a
        graded card). A record written before the field existed holds None.
        Entries with no descriptor id are noise -- an empty row the editor
        left behind -- and are dropped rather than sent."""
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        kept = []
        for entry in value[:MAX_CONDITION_DESCRIPTORS]:
            if isinstance(entry, ConditionDescriptor):
                if entry.id:
                    kept.append(entry)
            elif isinstance(entry, dict) and str(entry.get("id") or "").strip():
                kept.append(entry)
        return kept

    @field_validator("videos", mode="before")
    @classmethod
    def _cap_videos(cls, value):
        """eBay's ceiling, held at the model because every write path lands on
        it -- the upload route, a save from the editor, an import, a client
        that never enforced it. Past MAX_VIDEOS eBay does not refuse the
        listing, it silently ignores the extras, so a record that carried
        three would publish one and look like it had lost two.

        Entries with neither a local file nor an eBay id are dropped: they
        describe no video at all, and keeping them would have the editor show
        a slot for something that does not exist."""
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        kept = []
        for entry in value:
            if isinstance(entry, ListingVideo):
                if entry.file or entry.ebay_video_id:
                    kept.append(entry)
            elif isinstance(entry, dict) and (
                    str(entry.get("file") or "").strip()
                    or str(entry.get("ebay_video_id") or "").strip()):
                kept.append(entry)
            if len(kept) >= MAX_VIDEOS:
                break
        return kept


class IdentifyResult(BaseModel):
    listing: Listing
    confidence: str = "medium"
    raw_observations: str = ""
    # Tag/label bounding boxes the identify pass spotted ({photo, box, kind}),
    # consumed by the zoom-and-transcribe step — not shown in the UI.
    tags: list = []
    # Product identifiers read off the item's barcodes and plates, already
    # CHECK-DIGIT VERIFIED by services.barcodes: [{"value", "kind",
    # "symbology", "source"}]. The verified ones are written onto the listing
    # as UPC/EAN/ISBN item specifics; the ones that failed their check digit
    # never appear here — they become a "confirm this code" note instead,
    # because a misread identifier names somebody else's product in eBay's
    # catalogue. Not shown in the UI; it is the pricing lookup's best input.
    identifiers: list = []
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

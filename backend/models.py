"""Pydantic data models shared across the app."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ItemSpecific(BaseModel):
    name: str
    value: str


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
    currency: str = "USD"
    quantity: int = 1
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
    # Best Offer: let buyers make offers; optional auto-decline floor and
    # auto-accept threshold (both in the listing currency).
    best_offer_enabled: bool = False
    best_offer_min: Optional[float] = None
    best_offer_accept: Optional[float] = None
    # Promoted Listings: advertise this listing at an ad rate. When
    # promote_recommended is true, eBay's suggested bid is used and
    # promote_percent is ignored.
    promote_enabled: bool = False
    promote_recommended: bool = True
    promote_percent: Optional[float] = None
    item_specifics: list[ItemSpecific] = Field(default_factory=list)
    # filenames (relative to the session image dir) of optimized images
    images: list[str] = Field(default_factory=list)
    # fields the model was unsure about; surfaced to the user to fill in
    missing_info: list[str] = Field(default_factory=list)


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

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
    item_specifics: list[ItemSpecific] = Field(default_factory=list)
    # filenames (relative to the session image dir) of optimized images
    images: list[str] = Field(default_factory=list)
    # fields the model was unsure about; surfaced to the user to fill in
    missing_info: list[str] = Field(default_factory=list)
    notes: str = ""


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

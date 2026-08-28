"""Bounds on what one request can write to the volume.

`POST /api/publish` needs no login and, with no eBay account connected, falls
through to the dry run — and every distinct session_id writes a fresh
listing.json under /data/sessions. None of the free-text fields had a bound, so
a handful of requests carrying a multi-megabyte description could fill a volume
that runs with ~500MB free. The sweep only reclaims directories untouched for
three hours.

Truncation, not rejection, for the same reason models._cap_title gives: the
sources that overrun are AI drafts, refines and imported records, none of them
a seller who can act on a 422.
"""
from __future__ import annotations

from backend.models import (
    DESCRIPTION_MAX_CHARS, MAX_ITEM_SPECIFICS, TEXT_FIELD_MAX_CHARS, Listing,
)


def test_description_is_bounded_but_never_raises():
    listing = Listing(title="t", description="x" * (DESCRIPTION_MAX_CHARS + 5_000))
    assert len(listing.description) == DESCRIPTION_MAX_CHARS


def test_a_real_description_is_untouched():
    """The cap is eBay's own ceiling, so nothing a marketplace would accept is
    ever cut — including a long imported HTML description."""
    body = "<p>Vintage denim jacket.</p>" * 2_000     # ~56k chars
    assert len(body) < DESCRIPTION_MAX_CHARS
    assert Listing(title="t", description=body).description == body


def test_short_text_fields_are_bounded():
    long = "y" * (TEXT_FIELD_MAX_CHARS + 100)
    listing = Listing(title="t", subtitle=long, condition_description=long,
                      brand=long, category_suggestion=long)
    assert len(listing.subtitle) == TEXT_FIELD_MAX_CHARS
    assert len(listing.condition_description) == TEXT_FIELD_MAX_CHARS
    assert len(listing.brand) == TEXT_FIELD_MAX_CHARS
    assert len(listing.category_suggestion) == TEXT_FIELD_MAX_CHARS


def test_item_specifics_are_bounded():
    rows = [{"name": f"n{i}", "value": "v"} for i in range(MAX_ITEM_SPECIFICS + 50)]
    assert len(Listing(title="t", item_specifics=rows).item_specifics) == MAX_ITEM_SPECIFICS


def test_a_normal_listing_keeps_every_specific():
    rows = [{"name": f"n{i}", "value": "v"} for i in range(30)]
    assert len(Listing(title="t", item_specifics=rows).item_specifics) == 30


def test_session_id_is_bounded_on_the_request_models():
    """An unbounded id became a permanent key in publish_guard's lock
    registry, so a request body could size the process's memory."""
    import pytest
    from pydantic import ValidationError

    from backend.models import PublishRequest

    with pytest.raises(ValidationError):
        PublishRequest(session_id="a" * 200, listing={"title": "t"})
    # The ids the app actually mints, and the imported shape, both fit.
    assert PublishRequest(session_id="abc123def456",
                          listing={"title": "t"}).session_id == "abc123def456"
    assert PublishRequest(session_id="ebay-123456789012",
                          listing={"title": "t"}).session_id == "ebay-123456789012"

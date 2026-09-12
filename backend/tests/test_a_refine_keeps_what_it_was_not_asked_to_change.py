"""A refine rewrites the listing COPY and nothing else.

Reported as "when I have the agent rewrite a listing in the editor, it does
not include the eBay category id again". It did not: `claude_ai.refine` built
its answer with `_to_listing`, which knows only the fields the identify schema
produces, so every field outside that schema came back at its default. The
seller had to re-pick the eBay category after every rewrite, because a listing
with no `category_id` cannot publish (services/preflight) -- and nine other
fields went the same way unnoticed: their own store shelf, the selling format
with its starting bid and duration, the per-listing shipping policy, the
promotion they had agreed to pay eBay for, the video, and the Etsy and Depop
fields.

Needs the anthropic package for the import alone -- every call here is
stubbed. It runs in the smoke job, which installs from requirements.txt; the
fast unit job has no anthropic and skips the file whole.
"""
from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("anthropic")

from backend.models import (                   # noqa: E402
    ConditionDescriptor, DepopFields, EtsyFields, Listing, ListingVideo,
)
from backend.services import claude_ai         # noqa: E402


def _draft() -> Listing:
    """A draft with a value in every field a refine has no business writing."""
    return Listing(
        title="Levi's 501 Jeans", brand="Levi's", price=25.00,
        description="A pair of jeans.", images=["img_000.jpg"],
        # eBay's category, and the seller's own storefront shelf.
        category_id="11483", category_suggestion="Men's Jeans",
        store_category_id="4567", store_category_name="Vintage Denim",
        # How it sells, and for how long.
        listing_format="AUCTION_BIN", auction_start_price=9.99,
        auction_duration="DAYS_3", currency="GBP",
        # How it ships, and the ad spend they agreed to.
        fulfillment_policy_id="fp-1", promote=True, ad_rate_percent=5.0,
        videos=[ListingVideo(file="clip.mp4", ebay_video_id="9")],
        etsy=EtsyFields(taxonomy_id=123, who_made="i_did", tags=["denim"]),
        depop=DepopFields(category="jeans", size="M"),
    )


def _answers(payload: dict):
    """A stubbed model that hands back exactly this listing JSON."""
    resp = types.SimpleNamespace(
        stop_reason="end_turn",
        content=[types.SimpleNamespace(type="text", text=json.dumps(payload))],
    )
    return types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: resp))


def _echo(listing: Listing, **changes) -> dict:
    """The whole draft echoed back, the way the prompt asks for it."""
    payload = listing.model_dump()
    payload.pop("images", None)
    payload.update(changes)
    return payload


def test_a_refine_keeps_the_ebay_category_id(monkeypatch):
    """The reported bug. Without the id the publish is refused outright, so a
    refine that drops it costs the seller a trip back to the Category card."""
    listing = _draft()
    monkeypatch.setattr(claude_ai, "_client", lambda: _answers(
        _echo(listing, title="Levi's 501 Vintage Straight Leg Jeans")))

    out = claude_ai.refine(listing, "make the title punchier")

    assert out.title == "Levi's 501 Vintage Straight Leg Jeans"
    assert out.category_id == "11483"


def test_a_refine_changes_the_copy_and_nothing_else(monkeypatch):
    """The whole class the category id belonged to, in one assertion: the
    fields the model is not asked to write come back exactly as they went in."""
    listing = _draft()
    monkeypatch.setattr(claude_ai, "_client", lambda: _answers(
        _echo(listing, title="Levi's 501 Vintage Straight Leg Jeans")))

    out = claude_ai.refine(listing, "make the title punchier")

    before, after = listing.model_dump(), out.model_dump()
    moved = {name for name in before if before[name] != after[name]}
    assert moved == {"title"}, (
        "a refine asked only for a new title also moved: "
        + ", ".join(sorted(moved - {"title"})))


def test_a_refine_still_rewrites_the_copy_it_is_asked_for(monkeypatch):
    """The other half: keeping the rest must not freeze the listing text."""
    listing = _draft()
    monkeypatch.setattr(claude_ai, "_client", lambda: _answers(_echo(
        listing, title="Levi's 501 Jeans W32 L34", brand="Levi Strauss",
        description="A much better description.", condition="NEW_OTHER",
        condition_description="New without tags.", quantity=2,
        category_suggestion="Clothing > Men > Jeans",
        item_specifics=[{"name": "Waist Size", "value": "32",
                         "confidence": "high"}],
        missing_info=["inseam"], package_weight_lb=1, package_weight_oz=4,
        package_length_in=12)))

    out = claude_ai.refine(listing, "add the measurements and price it new")

    assert out.title == "Levi's 501 Jeans W32 L34"
    assert out.brand == "Levi Strauss"
    assert out.description == "A much better description."
    assert (out.condition, out.condition_description) == (
        "NEW_OTHER", "New without tags.")
    assert out.quantity == 2
    assert out.category_suggestion == "Clothing > Men > Jeans"
    assert [(s.name, s.value) for s in out.item_specifics] == [("Waist Size", "32")]
    assert out.missing_info == ["inseam"]
    assert (out.package_weight_lb, out.package_weight_oz) == (1, 4.0)
    assert out.package_length_in == 12


def test_a_refine_keeps_the_video_the_seller_uploaded(monkeypatch):
    """Worth its own test: a save that omits a video is the seller DELETING
    it (state.restore_video_state), so a refine that dropped the list could
    not be undone further down -- the upload was simply gone."""
    listing = _draft()
    monkeypatch.setattr(claude_ai, "_client",
                        lambda: _answers(_echo(listing, videos=[])))

    out = claude_ai.refine(listing, "tighten the description")

    assert [(v.file, v.ebay_video_id) for v in out.videos] == [("clip.mp4", "9")]


def test_the_graded_card_rule_still_holds(monkeypatch):
    """A condition the refine left alone keeps the descriptors the seller
    picked, whatever the echo did to them -- they are eBay's ids and a garbled
    echo is a refused publish. Unchanged behaviour, re-checked here because
    the descriptors are now the one echoed field that is also carried."""
    listing = _draft()
    listing.condition = "LIKE_NEW"   # eBay 2750, a graded card
    listing.condition_descriptors = [
        ConditionDescriptor(id="27501", values=["275010"], label="Grader")]
    monkeypatch.setattr(claude_ai, "_client", lambda: _answers(
        _echo(listing, condition_descriptors=[{"id": "99999"}])))

    out = claude_ai.refine(listing, "mention the slab in the description")

    assert [(d.id, d.values) for d in out.condition_descriptors] == [
        ("27501", ["275010"])]


def test_the_refined_fields_are_exactly_what_the_model_answers_with():
    """The guard on the list itself. REFINED_FIELDS has to track what
    `_to_listing` builds out of the answer: a field added there and not here
    would have the model's new answer silently ignored, and one removed there
    and left here would go on being rebuilt from an answer that no longer
    carries it -- the original bug, one field at a time.

    Found by comparing a blank answer with a loud one: a field whose value
    moves with the answer is the model's to write.
    """
    loud = {
        "title": "A thing", "subtitle": "and a subtitle", "brand": "A brand",
        "condition": "NEW", "condition_description": "Sealed.",
        "condition_descriptors": [{"id": "27501", "values": ["275010"]}],
        "category_suggestion": "Things > A thing", "description": "About it.",
        "price": 40, "purchase_price": 4.00, "retail_price": 130.00,
        "quantity": 3,
        "package_weight_lb": 2, "package_weight_oz": 3, "package_length_in": 4,
        "package_width_in": 5, "package_height_in": 6,
        "item_specifics": [{"name": "Colour", "value": "Blue"}],
        "missing_info": ["exact model number"],
    }
    blank = claude_ai._to_listing({}, [])
    filled = claude_ai._to_listing(loud, [])
    answered = {name for name in Listing.model_fields
                if getattr(blank, name) != getattr(filled, name)}

    assert answered == set(claude_ai.REFINED_FIELDS), (
        "REFINED_FIELDS and _to_listing disagree about what the model writes: "
        + ", ".join(sorted(answered ^ set(claude_ai.REFINED_FIELDS))))

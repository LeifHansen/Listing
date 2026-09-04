"""A seller called "Ben & Jerry's" must not break their own publish.

The Trading API takes XML built by string concatenation, and every field a
seller can type goes into it. `_esc` is applied field by field, which works
until somebody adds a field and forgets — and the failure is not subtle: an
unescaped `&` makes the whole request malformed, so eBay rejects EVERY publish
from that seller, on a character that appears in ordinary brand names.

Per-field tests already exist for the subtitle and for item specifics. What
did not exist is the claim over the whole builder, which is the one a new
field has to keep: fill every free-text field with XML metacharacters, render
both the create and the revise, and require that the result parses and that
each value comes back out exactly as it went in.

Parsing is the assertion that matters. A test looking for "&amp;" only checks
the fields somebody thought of; a parser checks all of them at once, including
whichever one is added next.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("pydantic")

from backend.models import ItemSpecific, Listing  # noqa: E402
from backend.services import ebay_trading  # noqa: E402

# One string with every character that ends an element, an attribute or a
# CDATA section, plus a payload that would be visible if it were injected.
HOSTILE = "Ben & Jerry's <Quantity>999</Quantity> \"quoted\" ]]> <![CDATA[ 'x'"


def _listing(**kw) -> Listing:
    base = dict(
        title=f"{HOSTILE} jacket",
        subtitle=HOSTILE,
        description=f"<p>{HOSTILE}</p>",
        condition_description=HOSTILE,
        brand="Ben & Jerry's",
        category_id="11450",
        currency="USD",
        price=45.0,
        quantity=1,
        item_specifics=[ItemSpecific(name="Style & Fit", value=HOSTILE),
                        ItemSpecific(name="Colour", value="<b>red</b>")],
    )
    base.update(kw)
    return Listing(**base)


def _create_xml(**kw) -> str:
    _call, body = ebay_trading.build_add_item(
        _listing(**kw),
        ["https://i.ebayimg.com/a.jpg?x=1&y=2"],
        policies={"fulfillment_policy_id": "1 & 2"},
        postal_code="97214")
    return body


def _revise_xml(**kw) -> str:
    listing = _listing(**kw)
    listing.mark_dirty("title", "subtitle", "description", "brand",
                       "condition_description", "item_specifics", "price",
                       "quantity", "category_id")
    _call, body = ebay_trading.build_revise_item(
        listing, "110000000001",
        image_urls=["https://i.ebayimg.com/a.jpg?x=1&y=2"])
    return body


@pytest.mark.parametrize("render", [_create_xml, _revise_xml],
                         ids=["create", "revise"])
def test_the_request_is_still_well_formed_xml(render):
    """The whole point: one unescaped character and eBay rejects everything
    this seller publishes, for ever, on a character in their brand name."""
    ET.fromstring(render())          # raises ParseError if anything leaked


@pytest.mark.parametrize("render", [_create_xml, _revise_xml],
                         ids=["create", "revise"])
def test_nothing_typed_becomes_an_element(render):
    """`<Quantity>999</Quantity>` in a title must be text, not a second
    quantity. Parsing back is what proves it — a substring check would not."""
    root = ET.fromstring(render())
    quantities = [e.text for e in root.iter("Quantity")]
    assert "999" not in (quantities or []), (
        f"a typed <Quantity> became a real one: {quantities}")


@pytest.mark.parametrize("render", [_create_xml, _revise_xml],
                         ids=["create", "revise"])
def test_every_typed_field_comes_back_exactly(render):
    root = ET.fromstring(render())

    def text(tag: str) -> str:
        found = root.find(f".//{tag}")
        return "" if found is None or found.text is None else found.text

    # Compared against the builder's own limits, not hardcoded ones: the
    # fields are truncated for eBay, and that truncation is correct — what is
    # being checked here is that whatever is sent comes back unchanged.
    assert text("Title") == f"{HOSTILE} jacket"[:ebay_trading.TITLE_MAX_CHARS]
    assert text("SubTitle") == HOSTILE[:ebay_trading.SUBTITLE_MAX_CHARS]
    assert text("ConditionDescription") == HOSTILE[:1000]
    # The description rides in CDATA (it is light HTML), and "]]>" inside it
    # is split across two sections — which the parser rejoins.
    assert text("Description") == f"<p>{HOSTILE}</p>"

    specifics = {nvl.findtext("Name"): [v.text for v in nvl.findall("Value")]
                 for nvl in root.iter("NameValueList")}
    assert specifics["Style & Fit"] == [HOSTILE[:65]]
    assert specifics["Colour"] == ["<b>red</b>"]
    assert specifics["Brand"] == ["Ben & Jerry's"]


def test_a_photo_url_with_a_query_string_survives():
    """`&` between query parameters is the everyday case, not the exotic one."""
    root = ET.fromstring(_create_xml())
    urls = [e.text for e in root.iter("PictureURL")]
    assert urls == ["https://i.ebayimg.com/a.jpg?x=1&y=2"]


def test_the_scan_covers_every_free_text_field_on_the_model():
    """A field added to Listing and left out of the hostile fixture above is
    a field this test silently stops covering."""
    typed = {name for name, field in Listing.model_fields.items()
             if field.annotation is str}
    # Fields that are enums, ids the seller cannot type freely, or not sent.
    not_free_text = {
        # Enums and ids the seller picks rather than types.
        "condition", "listing_format", "auction_duration", "currency",
        "category_id", "category_suggestion", "fulfillment_policy_id",
        # Server-owned: set by the sync, never sent back into the XML.
        "source", "status", "ebay_listing_id", "view_url", "sku",
        "ebay_account", "ebay_account_id", "ebay_start_time", "sold_at",
        # Written on the server's own clock when the AI specifics fill runs,
        # read only by the dashboard's suggestions. Never sent to eBay.
        "enriched_at",
    }
    covered = {"title", "subtitle", "description", "condition_description",
               "brand"}
    missed = typed - not_free_text - covered
    assert not missed, (
        "these free-text fields are not in the hostile fixture: "
        + ", ".join(sorted(missed)))

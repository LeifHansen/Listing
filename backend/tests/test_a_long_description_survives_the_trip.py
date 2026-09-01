"""What happens to a 400-word description on its way to a marketplace.

The AI now drafts the description as plain text in labelled sections
("Key Details:", "Condition:", ...) separated by blank lines. Every
marketplace takes that same string and does something different with it:

  - eBay renders <Description> as HTML, where a newline is whitespace. A
    sectioned body sent raw arrives as one unbroken wall of text — the
    longer the description, the worse that is.
  - Depop caps the field at 1000 characters, so the body IS cut; the only
    question is whether the cut lands mid-word.
  - Etsy strips HTML back to text, and appends the condition unless the
    description already has a "Condition:" line of its own.

These are the three ends of the same change, which is why they are tested
together.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("pydantic")

from backend.marketplaces import mapping_depop, mapping_etsy  # noqa: E402
from backend.models import Listing  # noqa: E402
from backend.services import ebay_trading, sync_merge  # noqa: E402

BODY = (
    "Pyrex Cinderella 441 mixing bowl in the Butterprint pattern, vintage "
    "1960s milk glass.\n"
    "Turquoise print on white, the small 1.5 pint size.\n"
    "\n"
    "Key Details:\n"
    "Brand: Pyrex\n"
    "Pattern: Butterprint\n"
    "\n"
    "Condition: Good used condition with light utensil marks inside.\n"
)


def _listing(**kw) -> Listing:
    base = dict(title="Pyrex Butterprint 441 mixing bowl", description=BODY,
                brand="Pyrex", price=24.0, quantity=1,
                condition="USED_GOOD", category_id="11450", currency="USD")
    base.update(kw)
    return Listing(**base)


def _sent_description(listing: Listing) -> str:
    _call, body = ebay_trading.build_add_item(
        listing, ["https://i.ebayimg.com/a.jpg"],
        policies={"fulfillment_policy_id": "1"}, postal_code="97214")
    found = ET.fromstring(body).find(".//Description")
    return "" if found is None or found.text is None else found.text


def test_the_sections_survive_as_paragraphs_on_ebay():
    """The blank lines the model was told to write are the structure. eBay
    only sees them if they are markup."""
    sent = _sent_description(_listing())
    assert sent.count("<p>") == 3, "the blank-line sections did not become paragraphs"
    # A line break INSIDE a section (the "Label: value" lines) is a break too.
    assert "Brand: Pyrex<br>Pattern: Butterprint" in sent
    # And the words themselves are untouched.
    assert "Pyrex Cinderella 441 mixing bowl" in sent


def test_a_description_that_is_already_html_is_left_alone():
    """Everything GetItem imports is real HTML, often a whole styled template.
    Rewriting it would mangle a listing the seller never asked us to touch."""
    html = '<div class="tpl"><p>Pyrex 441</p>\n\n<p>Butterprint</p></div>'
    assert _sent_description(_listing(description=html)) == html


def test_plain_text_is_escaped_on_the_way_into_the_markup():
    """`&` and `<` are ordinary characters in a description a seller typed,
    and markup once it is rendered as HTML."""
    sent = _sent_description(_listing(description="Ben & Jerry's < 5 left"))
    assert sent == "<p>Ben &amp; Jerry's &lt; 5 left</p>"


def test_an_empty_description_sends_nothing_at_all():
    """An empty <Description> on a revise is a request to WIPE the listing's
    description, which is not the same as not mentioning it."""
    _call, body = ebay_trading.build_add_item(
        _listing(description=""), ["https://i.ebayimg.com/a.jpg"],
        policies={"fulfillment_policy_id": "1"}, postal_code="97214")
    assert "<Description>" not in body


def test_depop_cuts_a_long_body_at_a_boundary():
    long = ("Levi's 501 straight-leg jeans in dark wash denim. " * 40).strip()
    cut = mapping_depop.build_product_payload(
        _listing(description=long))["description"]
    assert len(cut) <= mapping_depop.DESCRIPTION_LIMIT
    # What is kept is the start of the body, unaltered...
    assert long.startswith(cut)
    # ...and it stops between words, not inside one.
    assert long[len(cut)] == " "


def test_depop_keeps_a_short_body_exactly():
    kept = mapping_depop.build_product_payload(_listing())["description"]
    assert kept == BODY.strip()


def test_etsy_does_not_append_a_second_condition_line():
    """The body now carries its own "Condition:" section; the Etsy mapping
    appends one only when the description has none."""
    desc = mapping_etsy.build_listing_payload(_listing(), {})["description"]
    assert desc.lower().count("condition:") == 1
    assert desc.startswith("Pyrex Cinderella 441 mixing bowl")


# ------------------------------------------------- and back again, on a sync

def _remote(**over) -> dict:
    """What GetItem reports for a listing this app published: the same words,
    in the markup the publish sent."""
    base = {"title": "Pyrex Butterprint 441 mixing bowl", "price": 24.0,
            "quantity": 1, "category_id": "11450", "condition": "USED_GOOD",
            "description": _sent_description(_listing())}
    base.update(over)
    return base


def test_the_markup_we_sent_is_not_read_back_as_a_sellers_edit():
    """The local copy is plain text and eBay's is the paragraphs we made of
    it. Byte-compared, that is an edit on every sync forever: the description
    rides along on every revise, and a real Seller Hub edit later lands as a
    conflict over formatting nobody typed."""
    remote = _remote()
    merged = sync_merge.three_way(_listing(), shadow=remote, remote=remote)

    assert merged.conflicts == {}
    assert "description" not in merged.kept_local
    assert "description" not in merged.took_remote
    # And the seller's copy stays the plain text they can edit.
    assert merged.listing.description == BODY


def test_a_real_edit_on_ebay_still_arrives():
    """Compared as words, not ignored: different words are still different."""
    edited = _remote(description="<p>Pyrex 441 bowl, now with the lid.</p>")
    merged = sync_merge.three_way(_listing(), shadow=_remote(), remote=edited)

    assert merged.took_remote == ["description"]
    assert merged.listing.description == edited["description"]


def test_an_edit_on_both_sides_is_still_a_conflict():
    local = _listing(description=BODY + "\nComes with the original box.\n")
    edited = _remote(description="<p>Pyrex 441 bowl, now with the lid.</p>")
    merged = sync_merge.three_way(local, shadow=_remote(), remote=edited)

    assert set(merged.conflicts) == {"description"}

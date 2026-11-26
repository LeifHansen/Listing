"""A control character pasted into a listing does not sink the publish.

XML 1.0 forbids most C0 control characters outright -- inside CDATA too --
and text pasted from Word, a PDF or a spreadsheet carries them (Word's soft
line break is a vertical tab). Escaping does nothing for them, so a title or
description carrying one produced a Trading request no XML parser accepts,
and eBay refused the whole publish with nothing on screen to fix.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from backend.models import ItemSpecific, Listing
from backend.services import ebay_trading


def _parses(call: str, body: str) -> ET.Element:
    xml = (f'<?xml version="1.0" encoding="utf-8"?>'
           f'<{call}Request xmlns="urn:ebay:apis:eBLBaseComponents">'
           f"{body}</{call}Request>")
    return ET.fromstring(xml.encode("utf-8"))


def _listing(**extra):
    return Listing(title="Brass\x0blamp", description="Line one\x0bline two\x0c",
                   price=20, category_id="1", condition="USED_GOOD",
                   listing_format="FIXED_PRICE",
                   item_specifics=[ItemSpecific(name="Br\x01and", value="Acme\x1b")],
                   **extra)


def test_a_create_with_control_characters_is_well_formed():
    call, body = ebay_trading.build_add_item(
        _listing(), ["https://x/a.jpg"], postal_code="94105")
    root = _parses(call, body)
    title = next(e for e in root.iter() if e.tag.endswith("Title"))
    assert title.text == "Brass lamp"


def test_a_revise_with_control_characters_is_well_formed():
    listing = _listing()
    listing.mark_dirty("title", "description", "item_specifics")
    call, body = ebay_trading.build_revise_item(listing, "110000000001")
    _parses(call, body)


def test_ordinary_whitespace_and_text_are_untouched():
    assert ebay_trading._esc("a\tb\nc\r & <d>") == "a\tb\nc\r &amp; &lt;d&gt;"
    assert ebay_trading._cdata("café — ok") == "<![CDATA[café — ok]]>"

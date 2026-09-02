"""eBay's quantity contract: Item.Quantity is not what's left to sell.

For a fixed-price listing GetItem reports Item.Quantity as the quantity the
listing was CREATED with, and SellingStatus.QuantitySold as how many of those
have sold. What is still buyable is the difference. Importing Item.Quantity as
this app's `quantity` therefore imports a number that is too high by exactly
the number of units already sold.

That would be a display bug on its own. It is a P0 because `quantity` is also
an OUTPUT: ReviseFixedPriceItem treats the Quantity it is sent as the new
available quantity, so a seller who edits only the title re-publishes the
original total and eBay puts the sold units back on sale. The seller oversells
stock they no longer have, and finds out when a buyer pays for it.

The second half of the same defect is that `max(1, ...)` makes zero
unrepresentable: a sold-out listing imports as "1 available", which is both
wrong on screen and the number a later revise would restock from.

Contract: https://developer.ebay.com/devzone/xml/docs/reference/ebay/getitem.html
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from backend.models import Listing
from backend.services import ebay_trading

_NS = "urn:ebay:apis:eBLBaseComponents"


def _item(quantity: int, sold: int, *, listing_type: str = "FixedPriceItem",
          extra: str = "") -> ET.Element:
    """A minimal GetItem <Item> carrying just the quantity contract."""
    xml = (
        f'<Item xmlns="{_NS}">'
        f"<ItemID>110000000001</ItemID>"
        f"<Title>A thing</Title>"
        f"<ListingType>{listing_type}</ListingType>"
        f"<Quantity>{quantity}</Quantity>"
        f"<StartPrice>25.00</StartPrice>"
        f"<SellingStatus><QuantitySold>{sold}</QuantitySold>"
        f"<ListingStatus>Active</ListingStatus></SellingStatus>"
        f"{extra}"
        f"</Item>"
    )
    return ET.fromstring(xml)


def test_import_subtracts_the_units_already_sold():
    """10 listed, 3 sold, 7 left. Importing 10 is what restocks the 3."""
    data = ebay_trading._item_to_listing(_item(10, 3))
    assert data["quantity"] == 7
    # The raw halves stay available: "3 sold" is a real fact about the listing
    # and the dashboard shows it.
    assert data["sold_quantity"] == 3


def test_a_sold_out_listing_imports_as_zero_not_one():
    """1 listed, 1 sold, nothing left. max(1, ...) turns this into "1
    available", and the next revise offers a unit that does not exist."""
    data = ebay_trading._item_to_listing(_item(1, 1))
    assert data["quantity"] == 0


def test_quantity_never_goes_negative():
    """Defensive: eBay has reported sold > quantity on multi-variation and
    out-of-stock listings. Clamp at zero rather than emit a negative."""
    data = ebay_trading._item_to_listing(_item(2, 5))
    assert data["quantity"] == 0


def test_an_unsold_listing_is_unchanged():
    """The common case must not regress: nothing sold, everything available."""
    data = ebay_trading._item_to_listing(_item(4, 0))
    assert data["quantity"] == 4


def _revise_body(listing: Listing) -> str:
    """The XML a revise would send for this listing."""
    return ebay_trading.build_revise_item(listing, "110000000001")[1]


def test_a_title_only_edit_does_not_resend_quantity():
    """The restock itself. The seller changed a title; eBay must not be told
    anything about inventory, because the number this app holds is a snapshot
    that may already be stale."""
    listing = Listing(title="A better title", price=25.0, quantity=7,
                      listing_format="FIXED_PRICE").mark_dirty("title")
    body = _revise_body(listing)
    assert "<Quantity>" not in body


def test_an_explicit_inventory_edit_does_send_quantity():
    """The other side of the trade: when the seller really did change stock,
    the revise has to carry it or the edit silently does nothing."""
    listing = Listing(title="A thing", price=25.0, quantity=7,
                      listing_format="FIXED_PRICE").mark_dirty("quantity")
    body = _revise_body(listing)
    assert "<Quantity>7</Quantity>" in body


def test_an_explicit_zero_is_sent_not_dropped():
    """Zero is a real inventory state (out-of-stock control), and `if
    quantity > 0` drops it — so the one edit that takes a listing out of stock
    is the one that never reaches eBay."""
    listing = Listing(title="A thing", price=25.0, quantity=0,
                      listing_format="FIXED_PRICE").mark_dirty("quantity")
    body = _revise_body(listing)
    assert "<Quantity>0</Quantity>" in body


def test_dirty_marks_survive_a_json_round_trip():
    """Edits are recorded on a draft and read back on a later publish, with a
    JSON column in between. A set would not survive that trip at all."""
    import json

    listing = Listing(title="A thing", quantity=3).mark_dirty("quantity")
    restored = Listing(**json.loads(json.dumps(listing.model_dump())))
    assert restored.is_dirty("quantity")
    assert not restored.is_dirty("title")


def test_an_untracked_legacy_record_sends_no_quantity():
    """Records predating dirty-tracking carry no marks. That has to read as
    "nothing proven edited", not "send everything" — otherwise every legacy
    listing keeps exactly the restock behaviour this fix removes."""
    listing = Listing(title="A thing", price=25.0, quantity=7,
                      listing_format="FIXED_PRICE")
    assert listing.dirty_fields == []
    assert "<Quantity>" not in _revise_body(listing)

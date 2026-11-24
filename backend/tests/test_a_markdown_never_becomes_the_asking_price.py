"""A sale on eBay never becomes the listing's asking price here.

eBay runs a markdown ("sale event" in Seller Hub, or one this app made — see
services/sale_events) by showing the listing at a discount for a while, with
the price it had before struck through. GetItem reports that discount as the
listing's CurrentPrice, and carries the price before the sale under
SellingStatus/PromotionalSaleDetails/OriginalPrice.

`price` is one of the fields the store sync takes from eBay on every re-sync
(listing_sync._LIVE_FIELDS). Read from the discounted figure, a sale would:

  * overwrite the seller's asking price with the sale price;
  * be stamped on the listing as a markdown the SELLER made
    (price_lowered_at), which it was not;
  * and stay that way when the sale ended, because nothing would ever put the
    old price back.

So the import reads the original price whenever eBay says there is one.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from backend.services import ebay_trading, listing_sync

_NS = "urn:ebay:apis:eBLBaseComponents"


def _item(*, start="40.00", current="32.00", original="40.00", sale=True,
          listing_type="FixedPriceItem") -> ET.Element:
    promo = (
        "<PromotionalSaleDetails>"
        f"<OriginalPrice currencyID=\"USD\">{original}</OriginalPrice>"
        "<StartTime>2026-11-20T00:00:00.000Z</StartTime>"
        "<EndTime>2026-11-27T00:00:00.000Z</EndTime>"
        "</PromotionalSaleDetails>"
    ) if sale else ""
    return ET.fromstring(
        f'<Item xmlns="{_NS}">'
        "<ItemID>110000000001</ItemID><Title>Denim jacket</Title>"
        f"<ListingType>{listing_type}</ListingType>"
        "<Quantity>1</Quantity>"
        f"<StartPrice currencyID=\"USD\">{start}</StartPrice>"
        "<SellingStatus>"
        f"<CurrentPrice currencyID=\"USD\">{current}</CurrentPrice>"
        "<QuantitySold>0</QuantitySold><ListingStatus>Active</ListingStatus>"
        f"{promo}"
        "</SellingStatus>"
        "</Item>")


def test_a_listing_on_sale_imports_at_its_price_before_the_sale():
    assert ebay_trading._item_to_listing(_item())["price"] == 40.0


def test_the_original_price_wins_even_if_ebay_reports_the_sale_price_as_the_start():
    """Whichever field eBay moves during a sale, the price before it is the
    asking price."""
    item = _item(start="32.00", current="32.00", original="40.00")
    assert ebay_trading._item_to_listing(item)["price"] == 40.0


def test_a_listing_not_on_sale_reads_as_it_always_did():
    item = _item(start="40.00", current="40.00", sale=False)
    assert ebay_trading._item_to_listing(item)["price"] == 40.0


def test_a_resync_during_a_sale_keeps_the_asking_price_and_stamps_no_markdown():
    """The full path the store sync takes: a record held at $40, eBay showing
    it at $32 in a sale. The record stays at $40 and is not marked down."""
    prior = {"title": "Denim jacket", "price": 40.0, "price_lowered_at": "",
             "ebay_listing_id": "110000000001", "source": "ebay"}
    fresh = ebay_trading._item_to_listing(_item())
    fresh.pop(ebay_trading.ITEM_STATE_KEY, None)
    merged = listing_sync._merge(prior, fresh)
    out = listing_sync._reconcile(prior, merged, fresh)
    assert out["price"] == 40.0
    assert out["price_lowered_at"] == ""

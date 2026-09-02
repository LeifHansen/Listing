"""Fields the seller filled in that never reached eBay.

Three of them, each silent:

  - **Subtitle.** The editor has a Subtitle field, the importer reads eBay's
    `SubTitle` back, and the request builder never emitted one. A seller who
    typed a subtitle got no subtitle and no explanation.
  - **Auction duration.** The editor offers 1/3/5/7/10 days. `create_listing`
    hard-coded `Days_7`, so picking 10 days produced a 7-day auction — the app
    overriding a commercial decision without saying so.
  - **Package dimensions.** `int(10.5)` is 10. eBay wants whole inches, but
    truncating DOWN under-declares the box, and on calculated postage the
    seller pays the difference on every sale.

Both of the first two carry eBay fees — SubtitleFee, and AuctionLengthFee for
Days_10 — which is why "just start sending them" is not the whole fix. They
are sent because the seller asked for them by filling the field in, and the
fee is now disclosed where they choose, rather than a charge appearing on
their eBay invoice for something the app had been quietly discarding.

These are XML contract tests: they assert what goes on the wire, which is the
only place these three bugs were visible.
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import ebay_trading


def _xml(**over) -> str:
    base = {"title": "Blue lamp", "price": 25.0, "quantity": 1,
            "description": "A lamp.", "category_id": "112581"}
    base.update(over)
    # build_add_item returns (call name, body); the body is what goes on the
    # wire and is the only place these three bugs were ever visible.
    return ebay_trading.build_add_item(Listing(**base), ["https://x/1.jpg"])[1]


# ------------------------------------------------------------- subtitle

def test_a_subtitle_the_seller_typed_is_sent():
    """The finding. `SubTitle` appeared in this module exactly once — in the
    IMPORT parser."""
    assert "<SubTitle>Mid-century, restored</SubTitle>" in \
        _xml(subtitle="Mid-century, restored")


def test_no_subtitle_sends_no_element():
    """An empty <SubTitle/> is not "no subtitle" — on a revise it is a request
    to remove one, and on a create it is a field eBay may still bill for."""
    assert "SubTitle" not in _xml(subtitle="")


def test_a_subtitle_is_cut_to_ebays_limit():
    """55 characters. Over it, eBay rejects the whole listing."""
    xml = _xml(subtitle="x" * 200)
    body = xml.split("<SubTitle>")[1].split("</SubTitle>")[0]
    assert len(body) == 55


def test_a_subtitle_is_escaped_like_every_other_field():
    assert "<SubTitle>Ben &amp; Jerry" in _xml(subtitle="Ben & Jerry")


# ------------------------------------------------------- auction duration

def test_the_chosen_auction_duration_is_sent():
    """The finding: this was hard-coded to Days_7, so a seller who picked 10
    days got 7 — the app overriding a commercial decision in silence."""
    xml = _xml(listing_format="AUCTION", auction_start_price=1.0,
               auction_duration="DAYS_10")

    assert "<ListingDuration>Days_10</ListingDuration>" in xml
    assert "Days_7" not in xml


@pytest.mark.parametrize("chosen,sent", [
    ("DAYS_1", "Days_1"), ("DAYS_3", "Days_3"), ("DAYS_5", "Days_5"),
    ("DAYS_7", "Days_7"), ("DAYS_10", "Days_10"),
])
def test_every_duration_the_editor_offers_is_sent_in_ebays_spelling(chosen, sent):
    """eBay's ListingDuration is `Days_10`, not `DAYS_10`. Sending the model's
    own spelling is a rejected listing."""
    xml = _xml(listing_format="AUCTION", auction_start_price=1.0,
               auction_duration=chosen)
    assert f"<ListingDuration>{sent}</ListingDuration>" in xml


def test_an_unknown_duration_falls_back_to_seven_days():
    """A stale client or a hand-edited record must not produce a listing eBay
    rejects. Seven days is eBay's own default and what this always sent."""
    xml = _xml(listing_format="AUCTION", auction_start_price=1.0,
               auction_duration="DAYS_42")
    assert "<ListingDuration>Days_7</ListingDuration>" in xml


def test_a_fixed_price_listing_is_still_good_till_cancelled():
    xml = _xml(auction_duration="DAYS_10")
    assert "<ListingDuration>GTC</ListingDuration>" in xml


# ------------------------------------------------------ package dimensions

def test_a_fractional_dimension_is_rounded_up_not_truncated():
    """A 10.5-inch item does not fit in a 10-inch box. eBay wants whole
    inches, so the question is which way to round — and under-declaring means
    the seller pays the difference on every calculated-postage sale, while
    over-declaring costs the buyer pennies."""
    xml = _xml(package_weight_lb=2, package_length_in=10.5,
               package_width_in=4.2, package_height_in=3.1)

    assert "<PackageLength>11</PackageLength>" in xml
    assert "<PackageWidth>5</PackageWidth>" in xml
    assert "<PackageDepth>4</PackageDepth>" in xml


def test_a_whole_number_dimension_is_unchanged():
    xml = _xml(package_weight_lb=2, package_length_in=12,
               package_width_in=8, package_height_in=6)
    assert "<PackageLength>12</PackageLength>" in xml
    assert "<PackageWidth>8</PackageWidth>" in xml


def test_dimensions_are_omitted_when_the_seller_gave_none():
    xml = _xml(package_weight_lb=2)
    assert "PackageLength" not in xml


# ------------------------------------------- and the same on the way back IN

def test_a_decimal_dimension_from_ebay_is_not_truncated_on_import():
    """`float(_int(...))` ran eBay's value through int() first, so a package
    eBay reported as 10.5 inches was read back as 10 — and the seller's next
    edit sent that shrunken box back to eBay. These are MeasureType (decimal)
    fields; the whole-inch rounding belongs at the emit, not at the read."""
    from xml.etree import ElementTree as ET

    xml = ('<Item xmlns="urn:ebay:apis:eBLBaseComponents">'
           "<ItemID>1</ItemID><Title>Lamp</Title>"
           "<ShippingPackageDetails><WeightMajor>2</WeightMajor>"
           "<PackageLength>10.5</PackageLength>"
           "<PackageWidth>4.2</PackageWidth>"
           "<PackageDepth>3.1</PackageDepth>"
           "</ShippingPackageDetails></Item>")
    got = ebay_trading._item_to_listing(ET.fromstring(xml))

    assert got["package_length_in"] == 10.5
    assert got["package_width_in"] == 4.2
    assert got["package_height_in"] == 3.1


def test_a_listing_with_no_package_details_reads_as_zero():
    from xml.etree import ElementTree as ET

    xml = ('<Item xmlns="urn:ebay:apis:eBLBaseComponents">'
           "<ItemID>1</ItemID><Title>Lamp</Title></Item>")
    got = ebay_trading._item_to_listing(ET.fromstring(xml))

    assert got["package_length_in"] == 0.0


def test_a_nonsense_dimension_takes_the_whole_container_out():
    """A negative length is not something to send eBay. Floored at 0, which
    the all-three-truthy guard then reads as "no dimensions given"."""
    xml = _xml(package_weight_lb=2, package_length_in=-5,
               package_width_in=4, package_height_in=3)
    assert "PackageLength" not in xml
    # ...and the weight, which the seller DID give, still goes.
    assert "<WeightMajor unit=\"lbs\">2</WeightMajor>" in xml

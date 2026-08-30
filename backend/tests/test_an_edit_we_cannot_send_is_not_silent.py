"""An edit this app cannot push to eBay must not be reported as pushed.

`dirty_fields.TRACKED` names 22 fields as "the seller changed this, send it".
`build_revise_item` can emit ten of them plus price, quantity and the shipping
policy — thirteen. The other nine are marked dirty by an edit, travel through
the whole revise, and are never in the request:

    package_weight_lb, package_weight_oz, package_length_in,
    package_width_in, package_height_in, listing_format,
    auction_duration, auction_start_price, currency

Most of those eBay does not let a live listing change at all. One of them is
the everyday case: **package weight**. A seller who listed with the wrong
weight and fixes it here is told the listing was updated, and eBay keeps
charging buyers calculated postage off the old number — real money, on the
edit most likely to be made after a listing goes live.

This is P1-04's shape ("collected by the editor and never sent") on the
revise side. What it does NOT do is start sending ShippingPackageDetails on a
revise: eBay's own documentation is explicit that omitting a shipping field on
a revise REMOVES it, and this app cannot reach developer.ebay.com from here to
establish which of the package fields are revisable and what a partial block
does to the ones left out. Sending a guess would risk clearing dimensions the
seller never touched.

So the honest thing, and the thing this branch does everywhere else: say what
did not happen. The revise still sends everything it can; the answer names
the edits that stayed local and where to make them instead.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.models import Listing  # noqa: E402
from backend.services import dirty_fields, ebay_trading  # noqa: E402


def _live(**kw) -> Listing:
    base = dict(title="Vintage Levi's 501", description="Nice.", price=45.0,
                quantity=1, ebay_listing_id="110000000001",
                package_weight_lb=2.0, package_weight_oz=4.0)
    base.update(kw)
    return Listing(**base)


def test_the_builder_names_what_it_can_send():
    """A list derived from the builder, not a second copy of it — a field the
    revise learns to send must leave this set on its own."""
    assert ebay_trading.REVISABLE_FIELDS <= set(dirty_fields.TRACKED)
    for name in ("title", "price", "quantity", "item_specifics",
                 "fulfillment_policy_id"):
        assert name in ebay_trading.REVISABLE_FIELDS


def test_a_corrected_package_weight_is_reported_as_unsent():
    listing = _live()
    listing.mark_dirty("package_weight_lb", "package_weight_oz")
    assert ebay_trading.unsendable_revise_fields(listing) == [
        "package_weight_lb", "package_weight_oz"]


def test_an_ordinary_edit_reports_nothing():
    listing = _live()
    listing.mark_dirty("title", "price")
    assert ebay_trading.unsendable_revise_fields(listing) == []


def test_a_mixed_edit_reports_only_the_half_that_stayed(monkeypatch):
    listing = _live()
    listing.mark_dirty("price", "package_length_in")
    assert ebay_trading.unsendable_revise_fields(listing) == ["package_length_in"]
    # and the half that CAN go still goes
    _call, body = ebay_trading.build_revise_item(listing, "110000000001")
    assert "<StartPrice>45.00</StartPrice>" in body
    assert "PackageLength" not in body


def test_the_revise_answer_carries_it(monkeypatch):
    """`revise_listing` is what the provider reads, so the fact has to survive
    the call rather than being worked out again somewhere else."""
    listing = _live()
    listing.mark_dirty("title", "package_weight_lb")

    import xml.etree.ElementTree as ET
    ns = "urn:ebay:apis:eBLBaseComponents"
    root = ET.fromstring(
        f'<ReviseItemResponse xmlns="{ns}"><Ack>Success</Ack>'
        f"<ItemID>110000000001</ItemID></ReviseItemResponse>")
    monkeypatch.setattr(ebay_trading, "_call", lambda *a, **k: root)

    out = ebay_trading.revise_listing("tok", "110000000001", listing)
    assert out["ok"] is True
    assert out["unsent"] == ["package_weight_lb"]


def test_a_clean_revise_says_nothing_extra(monkeypatch):
    listing = _live()
    listing.mark_dirty("title")

    import xml.etree.ElementTree as ET
    ns = "urn:ebay:apis:eBLBaseComponents"
    root = ET.fromstring(
        f'<ReviseItemResponse xmlns="{ns}"><Ack>Success</Ack>'
        f"<ItemID>110000000001</ItemID></ReviseItemResponse>")
    monkeypatch.setattr(ebay_trading, "_call", lambda *a, **k: root)

    assert "unsent" not in ebay_trading.revise_listing(
        "tok", "110000000001", listing)


# --- what the seller is told, and what stays pending -----------------------

def test_the_message_names_the_edit_that_stayed():
    from backend.marketplaces.ebay_provider import revise_message

    said = revise_message(None, relist=False,
                          unsent=["package_weight_lb", "package_weight_oz"])
    assert "updated" in said
    assert "package weight" in said
    # Both halves of the weight fold onto one word.
    assert said.count("package weight") == 1
    assert "Seller Hub" in said, "the seller needs somewhere to go"


def test_an_ordinary_revise_message_is_unchanged():
    from backend.marketplaces.ebay_provider import revise_message

    assert revise_message(None, relist=False) == \
        "Your eBay listing has been updated."


def test_it_still_leads_with_a_real_conflict():
    """A held-back field is a question for the seller; an unsendable one is
    not. Both can be true at once and neither may hide the other."""
    from backend.marketplaces.ebay_provider import revise_message

    said = revise_message({"title": {"local": "a", "remote": "b"}},
                          relist=False, unsent=["package_weight_lb"])
    assert "both changed" in said and "title" in said
    assert "package weight" in said


def test_a_relist_says_nothing_about_it():
    """A relist creates a NEW listing from the whole record, so every field
    goes — there is nothing left behind to report."""
    from backend.marketplaces.ebay_provider import revise_message

    assert "package" not in revise_message(
        None, relist=True, unsent=["package_weight_lb"])

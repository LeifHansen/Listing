"""A live listing with a Best Offer waiting on it must stay editable.

The reported bug, in the seller's words: "all I have to do is open editor and
can no longer save due to rejection and it was ALREADY LIVE". Every save of
that listing came back

    Missing required item specific — eBay's reason: Item specifics cannot be
    changed if an auction-style listing has a bid or ends within 12 hours, or
    a fixed price listing has a pending Best Offer.

which is wrong twice over. Nothing was missing — eBay was refusing to change
specifics that were already complete — and the refusal took the whole revise
with it, so the price or title the seller had actually come to edit never
reached eBay either.

It repeats forever because of a rule that is right everywhere else: dirty
marks are cleared when the marketplace ACCEPTS, so a refused revise keeps its
marks and the retry still carries them. "Fill in details" marks item_specifics
outright (main._enrich_one). Once that revise is refused, every later save
rebuilds the same refused request. The listing is unsaveable until the offer
is resolved, and nothing on screen says so.

So: the locked field comes out, the rest of the edit goes through, and it
stays marked for next time.
"""
from __future__ import annotations

import pytest

from backend import ebay_errors
from backend.models import ItemSpecific, Listing
from backend.services import ebay_trading

EBAY_SAYS = ("Item specifics cannot be changed if an auction-style listing "
             "has a bid or ends within 12 hours, or a fixed price listing has "
             "a pending Best Offer.")


def live(**over) -> Listing:
    listing = Listing(
        title="Vintage Basketball Card", description="A card.",
        price=110.0, quantity=1, condition="USED_EXCELLENT",
        category_id="261328", brand="Topps",
        item_specifics=[ItemSpecific(name="Sport", value="Basketball")],
        ebay_listing_id="123456789", source="ebay", **over)
    return listing


class Calls:
    """Stands in for eBay: refuses anything carrying <ItemSpecifics>."""

    def __init__(self, refuse_specifics=True):
        self.bodies: list[str] = []
        self.refuse_specifics = refuse_specifics

    def __call__(self, call, token, body):
        self.bodies.append(body)
        if self.refuse_specifics and "<ItemSpecifics>" in body:
            raise ebay_trading.TradingError(EBAY_SAYS, code="21916626", detail="")
        return _ok_response()


def _ok_response():
    import xml.etree.ElementTree as ET
    return ET.fromstring("<r><ItemID>123456789</ItemID></r>")


@pytest.fixture
def ebay(monkeypatch):
    calls = Calls()
    monkeypatch.setattr(ebay_trading, "_call", calls)
    return calls


# --- the seller's edit reaches eBay ----------------------------------------

def test_a_price_edit_lands_even_with_specifics_stuck(ebay):
    """The whole bug. Fails against the old revise: one TradingError out, no
    price on eBay, and the seller told an item specific was missing."""
    listing = live()
    listing.mark_dirty("price", "item_specifics")

    res = ebay_trading.revise_listing("tok", "123456789", listing)

    assert res["ok"] is True
    assert len(ebay.bodies) == 2                      # refused, then retried
    assert "<ItemSpecifics>" in ebay.bodies[0]
    assert "<ItemSpecifics>" not in ebay.bodies[1]
    assert "<StartPrice>110.00</StartPrice>" in ebay.bodies[1]


def test_the_specifics_stay_pending_rather_than_being_called_delivered(ebay):
    listing = live()
    listing.mark_dirty("price", "item_specifics")
    res = ebay_trading.revise_listing("tok", "123456789", listing)
    # Reported so the caller can keep them marked — they are not "unsent"
    # (which means never sendable), they are "not yet".
    assert res["deferred"] == ["item_specifics"]
    assert "item_specifics" not in res.get("unsent", [])


def test_the_brand_is_locked_by_the_same_refusal(ebay):
    """Brand travels as the Brand aspect inside <ItemSpecifics>, so eBay's
    lock covers it — leaving it in would just fail the retry too."""
    listing = live()
    listing.mark_dirty("title", "brand")
    res = ebay_trading.revise_listing("tok", "123456789", listing)
    assert res["deferred"] == ["brand"]
    assert "<ItemSpecifics>" not in ebay.bodies[-1]
    assert "<Title>" in ebay.bodies[-1]


# --- the root cause: a revise that carried specifics it was never asked to --

def test_an_unrelated_edit_does_not_carry_the_specifics_at_all():
    """THE bug. `_item_fields` gated the specifics list on what the seller
    edited and then seeded the Brand aspect OUTSIDE that gate, so a listing
    with a brand put a one-row <ItemSpecifics> into every revise it ever made.

    Two harms. <ItemSpecifics> on a revise REPLACES the aspect set, so a price
    edit was overwriting the live listing's specifics with a one-aspect
    snapshot. And eBay refuses any revise carrying specifics while a Best
    Offer is pending — which is why a seller with an offer waiting could not
    save a price change, a title, or anything else.

    Fails against the old builder: the body contains <ItemSpecifics>.
    """
    listing = live()
    listing.mark_dirty("price")            # a price edit and nothing else
    _, body = ebay_trading.build_revise_item(listing, "123456789")
    assert "<ItemSpecifics>" not in body
    assert "<StartPrice>110.00</StartPrice>" in body


def test_the_brand_still_rides_along_wherever_specifics_are_sent():
    """The gate must not cost the thing the seeding was for. A create carries
    every field, and a specifics edit carries the block — Brand belongs in
    both, or the listing publishes brand-less (invisible to brand filters,
    refused outright in Brand-required categories), and a specifics revise
    would DROP the live listing's Brand aspect by omission."""
    listing = live()
    # A create: every field, no `only`.
    assert "<Name>Brand</Name>" in "".join(ebay_trading._item_fields(listing))
    # A revise of the specifics: the block goes, and Brand is in it.
    listing.mark_dirty("item_specifics")
    _, body = ebay_trading.build_revise_item(listing, "123456789")
    assert "<Name>Brand</Name>" in body
    # A revise of the brand alone: likewise.
    listing.clear_dirty()
    listing.mark_dirty("brand")
    _, body = ebay_trading.build_revise_item(listing, "123456789")
    assert "<Name>Brand</Name>" in body


# --- what it must NOT do ---------------------------------------------------

def test_a_specifics_only_edit_still_reports_the_refusal(ebay):
    """Nothing left to send once the specifics come out. An <Item> carrying
    only its own id is not a smaller revise, it is an empty one — and eBay's
    refusal is the honest answer."""
    listing = live()
    listing.mark_dirty("item_specifics")
    with pytest.raises(ebay_trading.TradingError):
        ebay_trading.revise_listing("tok", "123456789", listing)
    assert len(ebay.bodies) == 1                      # no pointless second call


def test_any_other_refusal_is_still_a_refusal(monkeypatch):
    """The retry is for this one restriction. A real validation error must not
    be answered by quietly sending less and reporting success."""
    def refuse(call, token, body):
        raise ebay_trading.TradingError("The category is invalid.", code="87",
                                        detail="")
    monkeypatch.setattr(ebay_trading, "_call", refuse)
    listing = live()
    listing.mark_dirty("price", "item_specifics")
    with pytest.raises(ebay_trading.TradingError):
        ebay_trading.revise_listing("tok", "123456789", listing)


def test_an_ordinary_revise_is_untouched(monkeypatch):
    """One call, specifics included, no retry — the path every other edit
    takes must not have grown a round trip."""
    calls = Calls(refuse_specifics=False)
    monkeypatch.setattr(ebay_trading, "_call", calls)
    listing = live()
    listing.mark_dirty("price", "item_specifics")
    res = ebay_trading.revise_listing("tok", "123456789", listing)
    assert len(calls.bodies) == 1
    assert "<ItemSpecifics>" in calls.bodies[0]
    assert "deferred" not in res


# --- and what the seller is told -------------------------------------------

def test_the_refusal_is_not_reported_as_a_missing_field():
    """Fails against the old taxonomy: eBay's sentence contains "item
    specifics", the specifics branch claimed it, and the seller was told
    "Missing required item specific" about a listing with none missing."""
    it = ebay_errors.explain({"errorId": "21916626", "message": EBAY_SAYS})
    assert "missing" not in it["title"].lower()
    # No field to open and fix, so no "Fix this" button pointing at one.
    assert it["target"] == "generic"
    assert "best offer" in it["fix"].lower()


def test_the_seller_is_told_it_will_go_over_by_itself():
    it = ebay_errors.explain({"errorId": "", "message": EBAY_SAYS})
    assert "saved here" in it["fix"].lower()


@pytest.mark.parametrize("message", [
    EBAY_SAYS,
    "Item specifics cannot be changed for this listing.",
    "This attribute cannot be changed because the listing has a pending "
    "Best Offer.",
])
def test_the_lock_is_recognised_however_ebay_words_it(message):
    assert ebay_trading.specifics_locked(
        ebay_trading.TradingError(message, code="", detail=""))


@pytest.mark.parametrize("message", [
    "The item specific Unit Quantity is missing.",
    "The category is invalid.",
    "Title contains characters that are not allowed.",
])
def test_an_ordinary_error_is_not_read_as_the_lock(message):
    assert not ebay_trading.specifics_locked(
        ebay_trading.TradingError(message, code="", detail=""))

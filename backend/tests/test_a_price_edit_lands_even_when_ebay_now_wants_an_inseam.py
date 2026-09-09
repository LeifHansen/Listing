"""A revise refused over a newly required aspect still delivers the rest.

eBay re-validates a listing's whole aspect set whenever a revise carries
<ItemSpecifics>, and a jeans listing made before Inseam became required is
refused: "The item specific Inseam is missing." The specifics were dropped
and the rest resent only for the OTHER refusal in that family (a Best Offer
freezing the listing), so this one failed the whole revise — and because a
refused revise keeps its dirty marks, every later revise of the listing (a
price drop, a title fix) rebuilt the same <ItemSpecifics> and failed the same
way. The seller's price change never reached eBay for as long as Inseam was
blank, and the error feed recorded the same refusal 26 times in a day.

Now the specifics are held back exactly as they are during a freeze, the
price goes over, and the seller is told what to fill in rather than to wait.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from backend.marketplaces.ebay_provider import revise_message  # noqa: E402
from backend.models import ItemSpecific, Listing  # noqa: E402
from backend.services import ebay_trading  # noqa: E402

INSEAM = ("The item specific Inseam is missing. Add Inseam to this listing, "
          "enter a valid value, and then try again.")


def _listing() -> Listing:
    listing = Listing(title="Levi's 501 jeans", description="d", price=39.99,
                      category_id="11483", source="ebay",
                      ebay_listing_id="123456789012",
                      item_specifics=[ItemSpecific(name="Brand", value="Levi's"),
                                      ItemSpecific(name="Size", value="33")])
    listing.mark_dirty("price", "item_specifics")
    return listing


def _ebay(monkeypatch, refusals: list[str]):
    """eBay refuses each body carrying <ItemSpecifics> with the next message
    in `refusals`, and accepts anything else. Returns the bodies it saw."""
    bodies: list[str] = []

    def fake_call(call, token, body):
        bodies.append(body)
        if "<ItemSpecifics>" in body and refusals:
            raise ebay_trading.TradingError(refusals.pop(0), code="21919303",
                                            detail="")
        from xml.etree import ElementTree as ET
        return ET.fromstring("<r><ItemID>123456789012</ItemID></r>")
    monkeypatch.setattr(ebay_trading, "_call", fake_call)
    return bodies


def test_the_price_goes_over_and_the_specifics_wait(monkeypatch):
    bodies = _ebay(monkeypatch, [INSEAM])
    out = ebay_trading.revise_listing("tok", "123456789012", _listing())
    assert out["ok"]
    assert len(bodies) == 2
    assert "<ItemSpecifics>" in bodies[0] and "<ItemSpecifics>" not in bodies[1]
    assert "<StartPrice>39.99</StartPrice>" in bodies[1]
    assert out["deferred"] == ["item_specifics"]
    assert out["deferred_why"] == "missing"
    assert out["deferred_aspect"] == "Inseam"


def test_the_seller_is_told_what_to_fill_not_to_wait():
    msg = revise_message(None, False, deferred=["item_specifics"],
                         deferred_why="missing", deferred_aspect="Inseam")
    assert "Inseam" in msg
    assert "Best Offer" not in msg


def test_a_freeze_still_reads_as_a_freeze():
    msg = revise_message(None, False, deferred=["item_specifics"],
                         deferred_why="locked")
    assert "Best Offer" in msg


def test_specifics_alone_are_still_refused_outright(monkeypatch):
    """Nothing else to send: an empty revise is not a smaller one."""
    _ebay(monkeypatch, [INSEAM])
    listing = _listing()
    listing.clear_dirty()
    listing.mark_dirty("item_specifics")
    with pytest.raises(ebay_trading.TradingError):
        ebay_trading.revise_listing("tok", "123456789012", listing)


def test_a_different_refusal_is_not_retried(monkeypatch):
    bodies = _ebay(monkeypatch, ["Invalid listing duration."])
    with pytest.raises(ebay_trading.TradingError):
        ebay_trading.revise_listing("tok", "123456789012", _listing())
    assert len(bodies) == 1

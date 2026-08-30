"""A multi-variation listing is not one listing with one price.

eBay lets a fixed-price listing carry variations — a shirt in S/M/L, each with
its own SKU, price and stock. This app never looked for them. `GetItem`'s
`Variations` container was ignored, so such a listing imported as ONE flat
record: a single price (eBay reports the lowest variation's), a single item
level quantity, and no sign that four other sizes exist.

That is bad on its own — the seller sees a listing that is not theirs as
described — but the write side is worse. A revise then sends item-level
`Quantity` and `StartPrice`, and eBay's own documentation says ReviseItem
does not support revisions of multiple-variation listings at all, and that a
variation dropping to quantity 0 is REMOVED from the listing (ErrorCode
21916620), with the listing ending once none are left. So the app was one
"update stock" away from editing a structure it could not see.

Until there is a variation model, the honest thing is to know they exist and
say so: import the listing, mark it as having variations, keep it visible and
end-able, and refuse to revise it here with an explanation and a way to do it
on eBay. A listing the app cannot represent correctly is one it must not
rewrite.

Sources for the contract, checked rather than assumed:
  https://developer.ebay.com/devzone/xml/docs/reference/ebay/ReviseItem.html
  https://developer.ebay.com/devzone/xml/docs/reference/ebay/types/VariationsType.html
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import ebay_trading

_NS = "urn:ebay:apis:eBLBaseComponents"


def _item_xml(with_variations: bool) -> bytes:
    variations = (
        "<Variations>"
        "<Variation><SKU>S</SKU><StartPrice>19.99</StartPrice>"
        "<Quantity>3</Quantity></Variation>"
        "<Variation><SKU>M</SKU><StartPrice>19.99</StartPrice>"
        "<Quantity>5</Quantity></Variation>"
        "</Variations>") if with_variations else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<GetItemResponse xmlns="{_NS}"><Ack>Success</Ack><Item>'
        "<ItemID>110001</ItemID><Title>Cotton tee</Title>"
        "<Quantity>8</Quantity>"
        "<SellingStatus><QuantitySold>0</QuantitySold>"
        "<CurrentPrice>19.99</CurrentPrice></SellingStatus>"
        "<ListingType>FixedPriceItem</ListingType>"
        f"{variations}"
        "</Item></GetItemResponse>").encode()


class _Resp:
    def __init__(self, content):
        self.status_code = 200
        self.content = content
        self.headers = {}


@pytest.fixture()
def ebay(monkeypatch):
    def _serve(with_variations):
        monkeypatch.setattr(ebay_trading.httpx, "post",
                            lambda *a, **k: _Resp(_item_xml(with_variations)))
    return _serve


# --------------------------------------------------------------- detection

def test_a_variation_listing_is_recognised_on_import(ebay):
    """The finding: nothing ever looked at `Variations`."""
    ebay(True)
    assert ebay_trading.get_listing("tok", "110001")["has_variations"] is True


def test_an_ordinary_listing_is_not(ebay):
    ebay(False)
    assert ebay_trading.get_listing("tok", "110001")["has_variations"] is False


def test_it_still_imports_everything_else(ebay):
    """Quarantine, not exclusion. The seller's listing stays visible and
    end-able here; hiding it would be its own kind of wrong."""
    ebay(True)
    got = ebay_trading.get_listing("tok", "110001")

    assert got["title"] == "Cotton tee"
    assert got["ebay_listing_id"] == "110001"


def test_the_flag_survives_the_model():
    assert Listing(title="x", has_variations=True).has_variations is True
    assert Listing(title="x").has_variations is False


# ------------------------------------------------------- the revise refuses

def _revise(**over):
    base = {"title": "Cotton tee", "price": 19.99, "quantity": 8,
            "ebay_listing_id": "110001", "has_variations": True}
    base.update(over)
    listing = Listing(**base).mark_dirty("quantity")
    return ebay_trading.build_revise_item(listing, "110001")


def test_revising_a_variation_listing_is_refused(ebay):
    """It used to build an item-level Quantity revise — for a structure eBay
    says ReviseItem cannot revise, where a variation reaching 0 is removed."""
    with pytest.raises(ebay_trading.TradingError):
        _revise()


def test_the_refusal_explains_and_points_somewhere(ebay):
    with pytest.raises(ebay_trading.TradingError) as caught:
        _revise()
    text = str(caught.value).lower()
    assert "variation" in text or "size" in text
    assert "ebay" in text, "the seller is not told where they CAN change it"


def test_an_ordinary_listing_still_revises():
    """The guard has to be narrow — this is the ordinary path."""
    call, body = _revise(has_variations=False)
    assert call == "ReviseFixedPriceItem"
    assert "<Quantity>8</Quantity>" in body


def test_the_refusal_is_not_a_rate_limit_or_a_duplicate():
    """It is a permanent property of the listing, so nothing may treat it as
    something to retry."""
    with pytest.raises(ebay_trading.TradingError) as caught:
        _revise()
    assert not isinstance(caught.value, ebay_trading.RateLimited)
    assert not isinstance(caught.value, ebay_trading.AlreadyListedError)


# ------------------------------------------- the flag follows eBay both ways

def test_a_listing_that_gains_variations_is_quarantined_on_the_next_sync():
    from backend.services import listing_sync

    existing = {"title": "Cotton tee", "has_variations": False,
                "ebay_listing_id": "110001"}
    merged = listing_sync._merge(existing, {"has_variations": True})

    assert merged["has_variations"] is True


def test_a_listing_that_loses_them_is_editable_again():
    """The quarantine has to LIFT. Left to the blank-field rule it would only
    ever latch on, so a seller who removed their variations on eBay would find
    the listing read-only here forever."""
    from backend.services import listing_sync

    existing = {"title": "Cotton tee", "has_variations": True,
                "ebay_listing_id": "110001"}
    merged = listing_sync._merge(existing, {"has_variations": False})

    assert merged["has_variations"] is False


def test_the_three_way_merge_takes_ebays_answer_too():
    from backend.services import sync_merge

    local = Listing(title="Cotton tee", has_variations=True)
    out = sync_merge.three_way(local, {"title": "Cotton tee"},
                               {"title": "Cotton tee", "has_variations": False})

    assert out.listing.has_variations is False


# ---------------------------------------- the seller finds out BEFORE the form

def _preflight(mode: str, **over) -> list[dict]:
    from backend.services import preflight

    base = {"title": "Cotton tee", "price": 19.99, "quantity": 8,
            "has_variations": True, "images": ["a.jpg"], "category_id": "1",
            "condition": "USED_EXCELLENT", "package_weight_lb": 1}
    base.update(over)
    return preflight.validate(
        Listing(**base), mode, has_fulfillment=True, has_payment=True,
        has_return=True, has_location=True, connected=True)


def test_the_checklist_says_so_before_anything_is_submitted():
    """Otherwise the seller fills in the whole form and finds out from a
    rejection, which is the wrong order."""
    issues = _preflight("live")

    assert [i["field"] for i in issues] == ["Variations"]
    assert issues[0]["blocking"] is True


def test_it_is_the_only_thing_reported():
    """Listing the usual checklist beside it would read as "fix these and it
    will publish", which is not true and will not be until variations are
    modelled."""
    issues = _preflight("live", title="", images=[])

    assert len(issues) == 1, [i["field"] for i in issues]


def test_an_ordinary_listing_still_gets_the_ordinary_checklist():
    assert _preflight("live", has_variations=False, title="") != []
    assert all(i["field"] != "Variations"
               for i in _preflight("live", has_variations=False))

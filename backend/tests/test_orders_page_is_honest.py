""""No orders are waiting to ship 🎉" has to be true.

The awaiting-shipment list asks eBay for 50 orders and returns whatever comes
back. eBay's Fulfillment API answers with `total` alongside the page, and that
number was dropped — so a seller with 80 orders to pack saw 50, with nothing
saying there were more.

The shipping dialog is where a seller decides what still needs doing. Reading
a page as the whole pile is how thirty orders go unshipped, and eBay measures
late dispatch: it costs the seller's standing, not just their afternoon.

Same finding as the status sweep reporting a sample as full coverage — the
answer has to carry what it could NOT show.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_orders


def _page(count: int, total: int) -> dict:
    return {
        "total": total,
        "orders": [{"orderId": f"o{i}", "orderFulfillmentStatus": "NOT_STARTED",
                    "lineItems": [], "pricingSummary": {}}
                   for i in range(count)],
    }


@pytest.fixture()
def ebay(monkeypatch):
    def _serve(count, total):
        monkeypatch.setattr(ebay_orders, "_get",
                            lambda *a, **k: _page(count, total))
    return _serve


def test_a_short_pile_is_reported_as_complete(ebay):
    ebay(4, 4)
    page = ebay_orders.awaiting_page("tok")

    assert len(page["orders"]) == 4
    assert page["total"] == 4
    assert page["partial"] is False


def test_a_pile_bigger_than_the_page_says_so(ebay):
    """The finding: 50 of 80 came back indistinguishable from 50 of 50."""
    ebay(50, 80)
    page = ebay_orders.awaiting_page("tok")

    assert len(page["orders"]) == 50
    assert page["total"] == 80
    assert page["partial"] is True


def test_an_empty_pile_is_genuinely_empty(ebay):
    ebay(0, 0)
    page = ebay_orders.awaiting_page("tok")

    assert page["orders"] == []
    assert page["partial"] is False


def test_a_missing_total_does_not_invent_one(ebay):
    """eBay omitting `total` is not eBay saying the page is everything. The
    count falls back to what was returned and `partial` stays False, but the
    number must never be made up."""
    from backend.services import ebay_orders as mod

    monkeypatch_total = {"orders": [{"orderId": "o1", "lineItems": [],
                                     "pricingSummary": {}}]}
    mod_get = mod._get
    try:
        mod._get = lambda *a, **k: monkeypatch_total
        page = mod.awaiting_page("tok")
    finally:
        mod._get = mod_get

    assert page["total"] == 1
    assert page["partial"] is False


def test_the_plain_list_still_works(ebay):
    """awaiting_shipment is used by order_for_item and by the listing lookup;
    it keeps its shape so those are untouched."""
    ebay(3, 3)
    assert len(ebay_orders.awaiting_shipment("tok")) == 3

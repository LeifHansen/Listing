"""An auction's opening bid is not its price, and eBay knows the difference.

A seller who switches a draft to Auction is asked for one number: where the
bidding starts. The app had nothing to say about it. "Check market price"
answered the Buy It Now question — the median of comparable listings, on the
nearest .99 — and on a plain auction that number went into `price`, a field
eBay never reads for that format. So the one field an auction actually needs
was the one field the market data never reached, and the seller typed a guess.

They are different numbers, and quoting one as the other is expensive in both
directions. An opener AT the market price is a Buy It Now with extra steps: no
bids, no sale, relist. An opener at a dollar on an item nobody is hunting for
is a $120 jacket handed to the first bidder for $0.99, because a no-reserve
auction with one bidder ends at the floor.

`pricing.auction_start` reads the comps `suggest` already measured from the
other end of the sale: how DEEP the market is decides how far below it is safe
to open, because comp count is the app's only evidence that bidders will turn
up at all. The rules below are what the seller is being asked to trust.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from backend.money import MIN_CHARM_PRICE  # noqa: E402
from backend.services import pricing  # noqa: E402

DEEP = pricing.DEEP_MARKET


def _comps(**kw):
    base = {"source": "active_comps", "label": "Live asking prices on eBay",
            "sold_data": False, "estimate": 40.0, "low": 30.0, "high": 55.0,
            "count": 9, "sample": [], "search_url": "https://ebay.test/sch"}
    base.update(kw)
    return base


def test_an_opener_is_below_what_the_item_is_worth():
    """At the market price it is a Buy It Now nobody bids on."""
    for strategy in ("quick_flip", "median", "long_sale", ""):
        for count in (1, 4, DEEP, 80):
            out = pricing.auction_start(_comps(count=count), strategy)
            assert out["start_price"] < out["market"], (
                f"{strategy or 'default'} opened a {count}-comp auction at or "
                f"above the market")


def test_a_deep_market_opens_lower_than_a_thin_one():
    """The same item, the same strategy — the only difference is how many
    comparable items eBay is carrying, which is the whole question: a low
    opener needs bidders to arrive and bid it up."""
    for strategy in ("quick_flip", "median", "long_sale"):
        deep = pricing.auction_start(_comps(count=DEEP), strategy)
        thin = pricing.auction_start(_comps(count=DEEP - 1), strategy)
        assert deep["deep_market"] is True and thin["deep_market"] is False
        assert deep["start_price"] < thin["start_price"], strategy


def test_the_account_strategy_orders_the_openers():
    """The same question Quick Flip / Median / Long Sale already answers for a
    Buy It Now, asked about the other end of the sale: how much of the item's
    value is the seller willing to risk to attract bidding."""
    for count in (4, DEEP):
        quick, mid, patient = (
            pricing.auction_start(_comps(count=count), s)["start_price"]
            for s in ("quick_flip", "median", "long_sale"))
        assert quick < mid < patient, count


def test_an_unknown_strategy_opens_where_median_does():
    """An account with no strategy set, or one saved before the setting
    existed, is not a reason to refuse an opening bid."""
    assert (pricing.auction_start(_comps(), "")["start_price"]
            == pricing.auction_start(_comps(), "median")["start_price"])
    assert pricing.auction_start(_comps(), "wat")["strategy"] == "median"


def test_an_opener_lands_on_a_charm_point_and_never_rounds_up():
    """Every price this app chooses ends in .99 (money.charm_price). A
    STARTING bid rounds one way only: nearest would raise the floor under an
    auction the seller asked to open below the market."""
    out = pricing.auction_start(_comps(estimate=40.0, count=DEEP), "median")
    # 40.00 * 0.40 = 16.00 -> the charm point at or below it.
    assert out["start_price"] == 15.99
    for estimate in (7.0, 23.5, 40.0, 199.0, 1250.0):
        for count in (2, DEEP):
            start = pricing.auction_start(
                _comps(estimate=estimate, count=count))["start_price"]
            assert round(start * 100) % 100 == 99, (estimate, count, start)


def test_an_opener_never_goes_under_ebays_floor():
    """eBay will not take an auction that starts below $0.99, whatever the
    item is worth — and a market measured under a dollar is not a reason to
    send a listing eBay refuses."""
    for estimate in (0.05, 0.50, 0.80, 0.99, 1.20):
        out = pricing.auction_start(_comps(estimate=estimate, count=DEEP * 4),
                                    "quick_flip")
        assert out["start_price"] >= MIN_CHARM_PRICE, estimate


def test_the_basis_says_what_it_was_measured_from():
    """A number a seller can overrule on purpose is one whose basis is on
    screen beside it — the same rule the Buy It Now suggestion follows."""
    deep = pricing.auction_start(_comps(count=30, sold_data=True), "median")
    assert "30 comparable items sold for" in deep["basis"]
    assert deep["label"].endswith("sold for")
    assert (deep["market"], deep["low"], deep["high"]) == (40.0, 30.0, 55.0)

    thin = pricing.auction_start(_comps(count=1), "quick_flip")
    # Singular, because it is a sentence somebody reads.
    assert "only 1 comparable item is listed at" in thin["basis"]
    assert "Quick Flip" in thin["basis"]


def test_nothing_measured_is_no_recommendation():
    """The same answer `suggest` gives for a market it could not measure, so
    a caller never has to tell a missing recommendation from a confident one.
    """
    assert pricing.auction_start(None) is None
    assert pricing.auction_start({}) is None
    assert pricing.auction_start(_comps(estimate=0)) is None
    assert pricing.auction_start(_comps(estimate=-4)) is None
    assert pricing.auction_start(_comps(estimate=None)) is None
    assert pricing.auction_start(_comps(estimate="lots")) is None


def test_suggest_carries_the_opener_beside_the_price(monkeypatch):
    """One lookup answers both questions. The format is one tap away on every
    draft card, so a recommendation that needed a second round trip would
    arrive after the seller had already typed a number."""
    monkeypatch.setattr(pricing, "_SOURCES",
                        (lambda *a, **k: _comps(count=DEEP * 3),))
    out = pricing.suggest("vintage levis 501", strategy="median")
    assert out["suggestion"]["price"] == 39.99       # what to LIST at
    assert out["auction"]["start_price"] == 15.99    # where to OPEN
    assert out["auction"]["start_price"] < out["suggestion"]["price"]


def test_a_market_nobody_could_measure_recommends_no_opener(monkeypatch):
    """`checked` already keeps "we couldn't look" apart from "there's nothing
    like this"; an opening bid invented from neither would be a number with no
    basis at all."""
    def _boom(*a, **k):
        raise RuntimeError("eBay returned 429 for item_summary/search")

    monkeypatch.setattr(pricing, "_SOURCES", (_boom,))
    out = pricing.suggest("vintage levis 501")
    assert out["checked"] is False
    assert out["auction"] is None

    monkeypatch.setattr(pricing, "_SOURCES", (lambda *a, **k: None,))
    empty = pricing.suggest("obscure thing")
    assert empty["checked"] is True
    assert empty["auction"] is None

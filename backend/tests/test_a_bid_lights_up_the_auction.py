"""A bid on an auction, on the listing's own card — and at the top of the grid.

A Best Offer already reached the card (test_a_pending_offer_reaches_the_card).
A bid did not: an auction that had just taken its first bid looked exactly
like one nobody wanted, sitting wherever its last edit left it. The sweep
that carries watchers and the offer hint already carries eBay's BidCount on
the same <Item>; this reads it, and the high bid with it, so the card can
glow and the grid can lift it.

The rule is the one every other number here follows. Zero is a real answer
(the sweep reached the listing and eBay said nobody has bid); ABSENT means
the app could not ask. And the high bid is read only where there is a bid:
CurrentPrice on an auction with none is its starting price, and on a Buy It
Now listing it is the price — money nobody has put down.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading, metrics

AUCTION = "110040606450"
FIXED = "110040606451"
QUIET = "110040606452"


def _item(item_id: str, watchers: int = 0, bids: int | None = None,
          current: str = "", currency: str = "USD",
          listing_type: str = "Chinese") -> str:
    """One ActiveList item. `bids` of None omits SellingStatus/BidCount, as
    eBay does on a listing that does not take bids; `current` is
    SellingStatus/CurrentPrice, which eBay reports on every listing."""
    selling = ""
    if bids is not None or current:
        selling = "<SellingStatus>"
        if bids is not None:
            selling += f"<BidCount>{bids}</BidCount>"
        if current:
            selling += f'<CurrentPrice currencyID="{currency}">{current}</CurrentPrice>'
        selling += "</SellingStatus>"
    return (
        f"<Item><ItemID>{item_id}</ItemID>"
        f"<ListingType>{listing_type}</ListingType>"
        f"<WatchCount>{watchers}</WatchCount>{selling}</Item>"
    )


def _active_reply(items: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<Ack>Success</Ack><ActiveList><ItemArray>{items}</ItemArray>"
        "<PaginationResult><TotalNumberOfPages>1</TotalNumberOfPages>"
        "</PaginationResult></ActiveList></GetMyeBaySellingResponse>"
    ).encode()


def _offers_reply() -> bytes:
    """An account-wide GetBestOffers that answered, with nothing pending."""
    return (
        '<?xml version="1.0"?>'
        '<GetBestOffersResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<Ack>Success</Ack><ItemBestOffersArray></ItemBestOffersArray>"
        "</GetBestOffersResponse>"
    ).encode()


class _Resp:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content


@pytest.fixture
def ebay(monkeypatch):
    replies: dict[str, bytes] = {"GetBestOffers": _offers_reply()}
    sent: list[dict] = []

    def fake_post(url, headers=None, content=None, timeout=None):
        call = (headers or {}).get("X-EBAY-API-CALL-NAME", "")
        sent.append({"call": call, "body": (content or b"").decode()})
        return _Resp(replies.get(call, _active_reply("")))

    monkeypatch.setattr(ebay_trading.httpx, "post", fake_post)
    return {"replies": replies, "sent": sent}


@pytest.fixture(autouse=True)
def _no_traffic_report(monkeypatch):
    metrics._CACHE.clear()

    def no_traffic(_token, ids, covered=None):
        if covered is not None:
            covered.update(ids)
        return {}

    monkeypatch.setattr(metrics, "_traffic", no_traffic)
    yield
    metrics._CACHE.clear()


# ----------------------------------------------------- reading eBay's answer

def test_the_sweep_carries_the_bid_count_and_the_high_bid(ebay):
    """Same walk, same <Item>: no extra Trading call to learn it."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(AUCTION, watchers=4, bids=3, current="12.50"))

    counts = ebay_trading.active_listing_counts("tok")

    # "" for ends_at: this fixture's <Item> carries no ListingDetails, so
    # eBay named no deadline. When the auction ends is the same sweep's
    # fourth question -- see test_the_auction_clock_reaches_the_card.
    assert counts[AUCTION] == {"watchers": 4, "offers_received": 0, "bids": 3,
                               "high_bid": 12.5, "bid_currency": "USD",
                               "ends_at": ""}
    assert [c["call"] for c in ebay["sent"]] == ["GetMyeBaySelling"]


def test_a_price_nobody_bid_is_not_a_high_bid(ebay):
    """CurrentPrice is on every listing. On an auction with no bids it is the
    starting price; on a Buy It Now listing it is the price. Neither is a bid,
    and a card that showed either as one would be reporting money that was
    never put down."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(AUCTION, bids=0, current="0.99")
        + _item(FIXED, current="89.99", listing_type="FixedPriceItem"))

    counts = ebay_trading.active_listing_counts("tok")

    assert counts[AUCTION]["bids"] == 0 and counts[AUCTION]["high_bid"] is None
    assert counts[FIXED]["bids"] == 0 and counts[FIXED]["high_bid"] is None


def test_the_bid_keeps_the_listings_own_currency(ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(AUCTION, bids=1, current="20.00", currency="GBP"))

    assert ebay_trading.active_listing_counts("tok")[AUCTION]["bid_currency"] == "GBP"


# --------------------------------------------- what the card is allowed to say

def test_a_bid_reaches_the_card_with_its_money(ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(AUCTION, watchers=4, bids=3, current="12.50"))

    out = metrics.listing_metrics({"access_token": "tok"}, [AUCTION], {})

    assert out[AUCTION] == {"views": 0, "watchers": 4, "offers": 0, "bids": 3,
                            "high_bid": 12.5, "bid_currency": "USD"}


def test_no_bids_is_an_answer_and_reads_as_nought(ebay):
    """The sweep reached both listings and eBay said nobody has bid on
    either. That is a fact the card may state, and the grid may leave the
    listing where it was."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(AUCTION, bids=0, current="0.99")
        + _item(QUIET, listing_type="FixedPriceItem"))

    out = metrics.listing_metrics({"access_token": "tok"}, [AUCTION, QUIET], {})

    assert out[AUCTION]["bids"] == 0 and "high_bid" not in out[AUCTION]
    assert out[QUIET]["bids"] == 0


def test_a_sweep_that_could_not_run_says_nothing_about_bids(monkeypatch, ebay):
    """Absent, not zero. "Nobody has bid" on the strength of having failed to
    ask is the same wrong claim the offer badge guards against."""
    monkeypatch.setattr(metrics, "_active_counts",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down")))

    out = metrics.listing_metrics({"access_token": "tok"}, [AUCTION], {})

    assert "bids" not in out.get(AUCTION, {})
    assert "watchers" not in out.get(AUCTION, {})


def test_a_listing_past_the_page_cap_is_unknown_not_unbid(monkeypatch, ebay):
    """A walk cut short by the page cap leaves the listings it never reached
    with no number at all — same rule as watchers, from the same walk."""
    def cut_short(_token, status=None):
        if status is not None:
            status["complete"] = False
        return {AUCTION: {"watchers": 1, "offers_received": 0, "bids": 2,
                          "high_bid": 5.0, "bid_currency": "USD"}}

    monkeypatch.setattr(metrics, "_active_counts", cut_short)

    out = metrics.listing_metrics({"access_token": "tok"}, [AUCTION, QUIET], {})

    assert out[AUCTION]["bids"] == 2, "the one the walk reached"
    assert "bids" not in out.get(QUIET, {}), "the one it never got to"


def test_a_bid_that_arrives_is_on_the_next_fresh_read(ebay):
    """The lift and the glow are only as current as the read behind them. A
    deliberate refresh -- "Sync with eBay" -- must pick up a bid placed since
    the cached copy, or the seller pressing it learns nothing."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(AUCTION, bids=0, current="0.99"))
    assert metrics.listing_metrics(
        {"access_token": "tok"}, [AUCTION], {})[AUCTION]["bids"] == 0

    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(AUCTION, bids=1, current="4.25"))

    out = metrics.listing_metrics({"access_token": "tok"}, [AUCTION], {},
                                  fresh=True)

    assert out[AUCTION]["bids"] == 1 and out[AUCTION]["high_bid"] == 4.25

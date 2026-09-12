"""When an auction stops taking bids, on the listing's own card.

An auction is the one listing with a DEADLINE, and the grid never carried it:
a card with a bid on it looked the same three days out as it did four minutes
out. The sweep that already reports watchers, bids and the offer hint reads
the same <Item> eBay puts ListingDetails/EndTime on, so this costs no extra
Trading call — the same argument that put BidCount there in the first place
(test_a_bid_lights_up_the_auction).

The rule worth its own test: AUCTIONS ONLY. eBay reports an EndTime for every
active listing, and on a fixed-price one it is the Good 'Til Cancelled renewal
date — a month out, moved by eBay itself, and nothing anybody is racing. A
countdown drawn from that would be a deadline the app invented, on every Buy
It Now in the store.

And the instant, never eBay's TimeLeft duration: this answer is cached for two
minutes, then read by a browser that keeps it on screen all afternoon. "2
hours left" would still be saying two hours.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading, metrics

AUCTION = "110040606450"
AUCTION_BIN = "110040606451"
FIXED = "110040606452"

ENDS = "2026-09-14T16:00:00.000Z"


def _item(item_id: str, listing_type: str = "Chinese", ends: str = ENDS) -> str:
    """One ActiveList item. `ends` of "" omits ListingDetails entirely, as
    eBay does on a listing it reports no end time for."""
    details = f"<ListingDetails><EndTime>{ends}</EndTime></ListingDetails>" if ends else ""
    return (
        f"<Item><ItemID>{item_id}</ItemID>"
        f"<ListingType>{listing_type}</ListingType>"
        "<WatchCount>3</WatchCount>"
        "<SellingStatus><BidCount>1</BidCount>"
        '<CurrentPrice currencyID="USD">12.44</CurrentPrice></SellingStatus>'
        f"{details}</Item>"
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

    def fake_post(url, headers=None, content=None, timeout=None):
        call = (headers or {}).get("X-EBAY-API-CALL-NAME", "")
        return _Resp(replies.get(call, _active_reply("")))

    monkeypatch.setattr(ebay_trading.httpx, "post", fake_post)
    return {"replies": replies}


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

def test_the_sweep_carries_when_the_auction_ends(ebay):
    """Same walk, same <Item>: no extra Trading call to learn it."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(AUCTION))
    assert ebay_trading.active_listing_counts("tok")[AUCTION]["ends_at"] == ENDS


def test_an_auction_with_a_buy_it_now_is_still_an_auction(ebay):
    # eBay calls both of them "Chinese" — the Buy It Now is a price on top,
    # not a different kind of listing. It takes bids and it finishes at a
    # fixed second, so it counts down like any other auction.
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(AUCTION_BIN))
    assert ebay_trading.active_listing_counts("tok")[AUCTION_BIN]["ends_at"] == ENDS


def test_a_fixed_price_end_time_is_not_a_deadline(ebay):
    # eBay reports one, and it is the Good 'Til Cancelled renewal date. Left
    # unfiltered, every Buy It Now in the store would grow a countdown to a
    # moment at which nothing happens.
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(FIXED, listing_type="FixedPriceItem"))
    assert ebay_trading.active_listing_counts("tok")[FIXED]["ends_at"] == ""


def test_an_auction_ebay_gave_no_end_time_for_reports_none(ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(AUCTION, ends=""))
    assert ebay_trading.active_listing_counts("tok")[AUCTION]["ends_at"] == ""


# ------------------------------------------------------ what the card is sent

def test_the_deadline_reaches_the_metrics_overlay(ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(AUCTION) + _item(FIXED, listing_type="FixedPriceItem"))

    out = metrics.listing_metrics({"access_token": "tok"}, [AUCTION, FIXED])

    assert out[AUCTION]["ends_at"] == ENDS
    # ABSENT, not empty. A card reads a missing key as "we could not ask";
    # an empty string is a deadline that parses as 1970, which is a clock
    # saying every Buy It Now in the store ended half a century ago.
    assert "ends_at" not in out[FIXED]


def test_a_missing_end_time_does_not_take_the_rest_of_the_sweep_with_it(ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(AUCTION, ends=""))

    out = metrics.listing_metrics({"access_token": "tok"}, [AUCTION])

    assert "ends_at" not in out[AUCTION]
    assert out[AUCTION]["bids"] == 1
    assert out[AUCTION]["watchers"] == 3

"""A buyer's offer, still waiting on an answer, on the listing's own card.

eBay gives a Best Offer 48 hours. Miss it and it lapses — the seller loses the
sale without ever declining it. The app already showed views and watchers on a
live card and said nothing at all about an offer, which is the one number on
that card attached to a person waiting.

The trap is eBay's own BestOfferCount. It counts offers RECEIVED, settled ones
included, so a listing whose only offer was declined last week still reports 1.
A badge built on it would tell a seller money was on the table when nobody had
put any there. It is used here for the one thing it can answer — a listing at
zero has never had an offer at all — and GetBestOffers answers the rest.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading, metrics

ITEM = "110040606450"
OTHER = "110040606451"


def _offer(status: str, price: str, expires: str = "",
           currency: str = "USD") -> str:
    return (
        "<BestOffer>"
        f'<Price currencyID="{currency}">{price}</Price>'
        f"<Status>{status}</Status>"
        + (f"<ExpirationTime>{expires}</ExpirationTime>" if expires else "")
        + "</BestOffer>"
    )


def _offers_reply(offers: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<GetBestOffersResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<Ack>Success</Ack><BestOfferArray>{offers}</BestOfferArray>"
        "</GetBestOffersResponse>"
    ).encode()


def _item(item_id: str, watchers: int, best_offer_count) -> str:
    """One ActiveList item. `best_offer_count` of None omits the container —
    eBay leaves it off entirely for a listing that doesn't take offers."""
    return (
        f"<Item><ItemID>{item_id}</ItemID><WatchCount>{watchers}</WatchCount>"
        + ("" if best_offer_count is None else
           f"<BestOfferDetails><BestOfferCount>{best_offer_count}"
           "</BestOfferCount><BestOfferEnabled>true</BestOfferEnabled>"
           "</BestOfferDetails>")
        + "</Item>"
    )


def _active_reply(items: str) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<Ack>Success</Ack><ActiveList><ItemArray>{items}</ItemArray>"
        "<PaginationResult><TotalNumberOfPages>1</TotalNumberOfPages>"
        "</PaginationResult></ActiveList></GetMyeBaySellingResponse>"
    ).encode()


class _Resp:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content


@pytest.fixture
def ebay(monkeypatch):
    """Answer each Trading call by name, and record what was asked."""
    replies: dict[str, bytes] = {}
    sent: list[dict] = []

    def fake_post(url, headers=None, content=None, timeout=None):
        call = (headers or {}).get("X-EBAY-API-CALL-NAME", "")
        body = (content or b"").decode()
        sent.append({"call": call, "body": body})
        return _Resp(replies.get(call, _active_reply("")))

    monkeypatch.setattr(ebay_trading.httpx, "post", fake_post)
    return {"replies": replies, "sent": sent}


@pytest.fixture(autouse=True)
def _no_traffic_report(monkeypatch):
    """Views/impressions are a different eBay API and a different question.
    Stubbed empty so these tests never reach the network, and so what they
    assert about a listing's metrics is only ever about offers and watchers.
    """
    metrics._CACHE.clear()
    monkeypatch.setattr(metrics, "_traffic", lambda *_a, **_k: {})
    yield
    metrics._CACHE.clear()


# ------------------------------------------------- reading eBay's two answers

def test_one_sweep_carries_both_watchers_and_the_offer_hint(ebay):
    """WatchCount and BestOfferCount sit on the same <Item>. Reading them in
    two GetMyeBaySelling walks would spend two of the account's Trading calls
    on a response we already had in hand."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 4, 2) + _item(OTHER, 0, None))

    counts = ebay_trading.active_listing_counts("tok")

    assert counts == {ITEM: {"watchers": 4, "offers_received": 2},
                      OTHER: {"watchers": 0, "offers_received": 0}}
    assert [c["call"] for c in ebay["sent"]] == ["GetMyeBaySelling"]


def test_only_offers_still_pending_are_counted(ebay):
    """The whole point of the second call. eBay's Status is the field that
    says a buyer is waiting; Declined, Expired and Accepted are all offers
    somebody has already dealt with."""
    ebay["replies"]["GetBestOffers"] = _offers_reply(
        _offer("Pending", "45.00", "2026-09-07T10:00:00.000Z")
        + _offer("Declined", "99.00")
        + _offer("Expired", "88.00")
        + _offer("Accepted", "77.00")
        + _offer("Countered", "66.00"))

    assert ebay_trading.pending_offers("tok", ITEM) == {
        "count": 1, "top": 45.0, "currency": "USD",
        "expires_at": "2026-09-07T10:00:00.000Z"}


def test_the_best_money_and_the_soonest_deadline_lead(ebay):
    """Several buyers waiting: the seller wants the best offer on the table,
    and the deadline that runs out first — not whichever eBay listed first."""
    ebay["replies"]["GetBestOffers"] = _offers_reply(
        _offer("Pending", "30.00", "2026-09-09T10:00:00.000Z")
        + _offer("Pending", "52.00", "2026-09-08T10:00:00.000Z")
        + _offer("Pending", "41.00", "2026-09-07T09:00:00.000Z"))

    assert ebay_trading.pending_offers("tok", ITEM) == {
        "count": 3, "top": 52.0, "currency": "USD",
        "expires_at": "2026-09-07T09:00:00.000Z"}


def test_the_listing_is_named_in_the_request(ebay):
    """Always by ItemID: an unscoped GetBestOffers is a different call with a
    different cost, and this one is asked once per candidate listing."""
    ebay["replies"]["GetBestOffers"] = _offers_reply("")

    ebay_trading.pending_offers("tok", ITEM)

    assert f"<ItemID>{ITEM}</ItemID>" in ebay["sent"][0]["body"]


def test_an_offer_in_the_listings_own_currency_keeps_it(ebay):
    """A seller on eBay.co.uk is not offered dollars."""
    ebay["replies"]["GetBestOffers"] = _offers_reply(
        _offer("Pending", "45.00", currency="GBP"))

    assert ebay_trading.pending_offers("tok", ITEM)["currency"] == "GBP"


# -------------------------------------------- what the card is allowed to say

def test_a_listing_with_no_offers_is_never_asked_about(ebay):
    """BestOfferCount 0 is the one thing it answers exactly, and it covers
    most of a store. Asking GetBestOffers about those listings would be one
    Trading call each to be told what the sweep already said."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 4, 0) + _item(OTHER, 1, None))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM, OTHER], {})

    assert "GetBestOffers" not in [c["call"] for c in ebay["sent"]]
    assert out[ITEM]["offers"] == 0 and out[OTHER]["offers"] == 0


def test_a_listing_whose_offers_are_all_settled_reads_as_none(ebay):
    """The defect this exists to prevent. eBay says "1 offer received"; the
    offer was declined a week ago. The card must say nothing."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(ITEM, 4, 1))
    ebay["replies"]["GetBestOffers"] = _offers_reply(_offer("Declined", "99.00"))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM], {})

    assert out[ITEM]["offers"] == 0
    assert "top_offer" not in out[ITEM]


def test_a_pending_offer_arrives_with_its_money_and_its_deadline(ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(ITEM, 4, 1))
    ebay["replies"]["GetBestOffers"] = _offers_reply(
        _offer("Pending", "45.00", "2026-09-07T10:00:00.000Z"))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM], {})

    assert out[ITEM] == {"views": 0, "watchers": 4, "offers": 1,
                         "top_offer": 45.0, "offer_currency": "USD",
                         "offer_expires_at": "2026-09-07T10:00:00.000Z"}


def test_a_lookup_that_failed_says_nothing_rather_than_nought(monkeypatch, ebay):
    """"No offer" and "we could not ask" are different things to tell a seller
    about money on the table. An absent count draws no badge; a zero would be
    the app stating there is nobody waiting on the strength of having failed
    to find out."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 4, 1) + _item(OTHER, 2, 0))
    monkeypatch.setattr(ebay_trading, "pending_offers",
                        lambda *_a: (_ for _ in ()).throw(RuntimeError("down")))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM, OTHER], {})

    assert "offers" not in out[ITEM], "the one we could not ask about"
    assert out[ITEM]["watchers"] == 4, "and the watchers still came back"
    assert out[OTHER]["offers"] == 0, "the one the sweep answered on its own"


def test_one_listings_failure_does_not_silence_the_rest(monkeypatch, ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 4, 1) + _item(OTHER, 2, 1))

    def flaky(_token, item_id):
        if item_id == ITEM:
            raise RuntimeError("down")
        return {"count": 2, "top": 20.0, "currency": "USD", "expires_at": ""}

    monkeypatch.setattr(ebay_trading, "pending_offers", flaky)

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM, OTHER], {})

    assert "offers" not in out[ITEM]
    assert out[OTHER]["offers"] == 2


def test_the_busiest_listings_are_the_ones_asked_about(monkeypatch, ebay):
    """A store that has haggled on hundreds of listings cannot spend a Trading
    call on each of them for a badge. Past the cap the sweep answers the ones
    with the most offers, rather than whichever sorted first."""
    monkeypatch.setattr(metrics, "_OFFER_LOOKUPS", 2)
    ids = ["10", "11", "12", "13"]
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        "".join(_item(i, 0, n) for i, n in zip(ids, (1, 9, 2, 5))))
    asked: list[str] = []
    monkeypatch.setattr(ebay_trading, "pending_offers", lambda _t, i: (
        asked.append(i) or {"count": 1, "top": None, "currency": "",
                            "expires_at": ""}))

    out = metrics.listing_metrics({"access_token": "tok"}, ids, {})

    assert asked == ["11", "13"], "9 offers and 5, not 1 and 2"
    # The two that were never asked about say nothing at all — same rule as a
    # failed lookup, for the same reason.
    assert "offers" not in out["10"] and "offers" not in out["12"]


def test_offers_are_only_read_for_the_listings_asked_about(ebay):
    """The sweep returns the whole active store; the caller asked about one
    listing. Nothing is looked up for the others."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 4, 0) + _item(OTHER, 1, 3))

    metrics.listing_metrics({"access_token": "tok"}, [ITEM], {})

    assert "GetBestOffers" not in [c["call"] for c in ebay["sent"]]

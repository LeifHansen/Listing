"""The offer badge survives a store with a HISTORY of offers.

The reported bug: a live listing with a Best Offer pending on it, and the card
said nothing — no chip, no number, no hint that somebody was waiting. eBay
gives an offer 48 hours, so the silence costs the sale.

The badge was built on a shortlist. The ActiveList sweep hands over each
listing's `BestOfferCount`, the listings at zero are known to have no offers
for free, and the rest were asked about one at a time — capped, because each
one is a Trading call, and sorted by that same count so the cap spends itself
on the "busiest" listings.

That ranking is backwards for what it is looking for. `BestOfferCount` counts
offers RECEIVED, settled ones included, so a listing whose nine offers were
all declined months ago scores 9 forever, while a listing with one offer
waiting right now scores 1 and sorts LAST. On a store that haggles — which is
every store that switches Best Offer on — the budget is spent entirely on
listings whose offers are all settled, each answering "nothing pending", and
the one listing with money on the table is never asked about. Being unasked,
it reported nothing rather than a wrong nought: honest, invisible, and the
seller still loses the sale.

So the question is asked the way eBay offers to answer it — GetBestOffers with
no ItemID, which returns every listing with offers waiting, for the whole
account, in one call. No shortlist, so nothing to rank; no cap, so nothing
falls off the end.
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


def _for_item(item_id: str, offers: str) -> str:
    """One ItemBestOffers group: the listing, and the offers standing on it."""
    return (f"<ItemBestOffers><Item><ItemID>{item_id}</ItemID></Item>"
            f"<BestOfferArray>{offers}</BestOfferArray></ItemBestOffers>")


def _all_offers_reply(groups: str, total_pages: int = 1) -> bytes:
    """An unscoped GetBestOffers reply — the whole account in one answer."""
    return (
        '<?xml version="1.0"?>'
        '<GetBestOffersResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<Ack>Success</Ack><ItemBestOffersArray>{groups}</ItemBestOffersArray>"
        f"<PaginationResult><TotalNumberOfPages>{total_pages}"
        "</TotalNumberOfPages></PaginationResult>"
        "</GetBestOffersResponse>"
    ).encode()


def _item(item_id: str, watchers: int, best_offer_count) -> str:
    return (
        f"<Item><ItemID>{item_id}</ItemID><WatchCount>{watchers}</WatchCount>"
        + ("" if best_offer_count is None else
           f"<BestOfferDetails><BestOfferCount>{best_offer_count}"
           "</BestOfferCount></BestOfferDetails>")
        + "</Item>"
    )


def _active_reply(items: str, total_pages: int = 1) -> bytes:
    return (
        '<?xml version="1.0"?>'
        '<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<Ack>Success</Ack><ActiveList><ItemArray>{items}</ItemArray>"
        f"<PaginationResult><TotalNumberOfPages>{total_pages}"
        "</TotalNumberOfPages></PaginationResult>"
        "</ActiveList></GetMyeBaySellingResponse>"
    ).encode()


class _Resp:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content


@pytest.fixture
def ebay(monkeypatch):
    """Answer each Trading call by name, and record what was asked. Replies may
    be a single bytes answer or a list of them, one per successive call."""
    replies: dict[str, object] = {}
    sent: list[dict] = []

    def fake_post(url, headers=None, content=None, timeout=None):
        call = (headers or {}).get("X-EBAY-API-CALL-NAME", "")
        sent.append({"call": call, "body": (content or b"").decode()})
        reply = replies.get(call, _active_reply(""))
        if isinstance(reply, list):
            # Successive pages; the last one repeats if asked for again.
            nth = sum(1 for c in sent if c["call"] == call) - 1
            reply = reply[min(nth, len(reply) - 1)]
        return _Resp(reply)

    monkeypatch.setattr(ebay_trading.httpx, "post", fake_post)
    return {"replies": replies, "sent": sent}


@pytest.fixture(autouse=True)
def _no_traffic_report(monkeypatch):
    """Views are a different eBay API and a different question."""
    metrics._CACHE.clear()

    def no_traffic(_token, ids, covered=None):
        if covered is not None:
            covered.update(ids)
        return {}

    monkeypatch.setattr(metrics, "_traffic", no_traffic)
    yield
    metrics._CACHE.clear()


def _per_listing_lookups(sent: list[dict]) -> list[str]:
    return [c["body"] for c in sent
            if c["call"] == "GetBestOffers" and "<ItemID>" in c["body"]]


# --------------------------------------------------------- the reported bug

def test_the_one_offer_waiting_beats_a_store_full_of_settled_ones(ebay):
    """The defect this exists to prevent, at the size it actually bit.

    Thirty listings have haggled before and carry the scars in
    BestOfferCount; the thirty-first has the only buyer actually waiting, and
    the lowest count of the lot. The shortlist would rank it dead last and
    the cap would drop it."""
    haggled = "".join(_item(str(500 + n), 0, 9) for n in range(30))
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(haggled + _item(ITEM, 4, 1))
    ebay["replies"]["GetBestOffers"] = _all_offers_reply(
        _for_item(ITEM, _offer("Pending", "45.00", "2026-09-07T10:00:00.000Z")))

    ids = [str(500 + n) for n in range(30)] + [ITEM]
    out = metrics.listing_metrics({"access_token": "tok"}, ids, {})

    assert out[ITEM]["offers"] == 1
    assert out[ITEM]["top_offer"] == 45.0
    assert out[ITEM]["offer_expires_at"] == "2026-09-07T10:00:00.000Z"
    # And the thirty settled ones read as nought — asked and answered, not
    # guessed from a count that would have said nine apiece.
    assert all(out[str(500 + n)]["offers"] == 0 for n in range(30))


def test_the_answer_does_not_depend_on_the_per_listing_budget(monkeypatch, ebay):
    """With the shortlist's budget at nothing, the badge still appears — which
    it only can if the account-wide call is what answered."""
    monkeypatch.setattr(metrics, "_OFFER_LOOKUPS", 0)
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(ITEM, 4, 1))
    ebay["replies"]["GetBestOffers"] = _all_offers_reply(
        _for_item(ITEM, _offer("Pending", "45.00")))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM], {})

    assert out[ITEM]["offers"] == 1 and out[ITEM]["top_offer"] == 45.0


def test_a_listing_the_sweep_never_reached_still_gets_its_badge(ebay):
    """The offers question no longer travels through the ActiveList sweep, so
    the sweep's page cap can no longer hide an offer. eBay says there are more
    pages of active listings than the walk will read; the offer shows anyway."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(OTHER, 1, 0),
                                                       total_pages=99)
    ebay["replies"]["GetBestOffers"] = _all_offers_reply(
        _for_item(ITEM, _offer("Pending", "45.00")))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM], {})

    assert out[ITEM]["offers"] == 1


# ------------------------------------------------------- one call, not many

def test_one_unscoped_call_answers_every_listing(ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 4, 3) + _item(OTHER, 1, 7))
    ebay["replies"]["GetBestOffers"] = _all_offers_reply(
        _for_item(ITEM, _offer("Pending", "45.00"))
        + _for_item(OTHER, _offer("Pending", "12.00") + _offer("Pending", "19.00")))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM, OTHER], {})

    assert out[ITEM]["offers"] == 1
    assert out[OTHER]["offers"] == 2 and out[OTHER]["top_offer"] == 19.0
    assert _per_listing_lookups(ebay["sent"]) == [], "no call spent per listing"
    assert len([c for c in ebay["sent"] if c["call"] == "GetBestOffers"]) == 1


def test_the_request_names_no_listing_and_asks_for_active_offers(ebay):
    ebay["replies"]["GetBestOffers"] = _all_offers_reply("")

    ebay_trading.all_pending_offers("tok")

    body = ebay["sent"][0]["body"]
    assert "<ItemID>" not in body, "unscoped: the whole account in one answer"
    assert "<BestOfferStatus>Active</BestOfferStatus>" in body


def test_settled_offers_are_not_somebody_waiting(ebay):
    """Same rule as before, on the new answer: Status is the field that says a
    buyer is waiting, and the request filter is not evidence of it."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(ITEM, 4, 4))
    ebay["replies"]["GetBestOffers"] = _all_offers_reply(
        _for_item(ITEM, _offer("Declined", "99.00") + _offer("Expired", "88.00")
                  + _offer("Accepted", "77.00") + _offer("Countered", "66.00")))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM], {})

    assert out[ITEM]["offers"] == 0
    assert "top_offer" not in out[ITEM]


def test_an_empty_answer_is_a_real_nought(ebay):
    """eBay answered for the whole account and named nobody. That is a fact
    about every listing asked about, not a failure to ask."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 4, 5) + _item(OTHER, 1, 2))
    ebay["replies"]["GetBestOffers"] = _all_offers_reply("")

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM, OTHER], {})

    assert out[ITEM]["offers"] == 0 and out[OTHER]["offers"] == 0
    assert _per_listing_lookups(ebay["sent"]) == []


# ------------------------------------------- what it may not say, and when

def test_a_reply_it_cannot_read_falls_back_to_asking_per_listing(ebay):
    """The container missing means either an account eBay has nothing to say
    about or a shape this doesn't understand, and those must not be told
    apart by guessing. The old question still answers, listing by listing."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(ITEM, 4, 1))
    ebay["replies"]["GetBestOffers"] = [
        b'<?xml version="1.0"?><GetBestOffersResponse '
        b'xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack>'
        b"</GetBestOffersResponse>",
        # The per-listing form of the question, answered the old way.
        b'<?xml version="1.0"?><GetBestOffersResponse '
        b'xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack>'
        b"<BestOfferArray><BestOffer>"
        b'<Price currencyID="USD">45.00</Price><Status>Pending</Status>'
        b"</BestOffer></BestOfferArray></GetBestOffersResponse>",
    ]

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM], {})

    assert _per_listing_lookups(ebay["sent"]), "it fell back"
    assert out[ITEM]["offers"] == 1 and out[ITEM]["top_offer"] == 45.0


def test_a_failed_lookup_still_says_nothing_rather_than_nought(monkeypatch, ebay):
    """The honesty rule, unchanged: money on the table is not something to
    report on the strength of having failed to ask."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(_item(ITEM, 4, 1))
    monkeypatch.setattr(ebay_trading, "all_pending_offers",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(ebay_trading, "pending_offers",
                        lambda *_a: (_ for _ in ()).throw(RuntimeError("down")))

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM], {})

    assert "offers" not in out[ITEM]
    assert out[ITEM]["watchers"] == 4, "and the watchers still came back"


def test_a_walk_cut_short_speaks_only_for_what_it_read(ebay):
    """eBay says there are more pages of offers than the walk will read. The
    listings it did see keep their badges; the ones it never got to stay
    unknown rather than being called offer-free."""
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 4, 1) + _item(OTHER, 2, 1))
    ebay["replies"]["GetBestOffers"] = _all_offers_reply(
        _for_item(ITEM, _offer("Pending", "45.00")), total_pages=999)

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM, OTHER],
                                  {})

    assert out[ITEM]["offers"] == 1
    assert "offers" not in out.get(OTHER, {})


def test_every_page_of_offers_is_read(ebay):
    ebay["replies"]["GetMyeBaySelling"] = _active_reply(
        _item(ITEM, 0, 1) + _item(OTHER, 0, 1))
    ebay["replies"]["GetBestOffers"] = [
        _all_offers_reply(_for_item(ITEM, _offer("Pending", "45.00")),
                          total_pages=2),
        _all_offers_reply(_for_item(OTHER, _offer("Pending", "12.00")),
                          total_pages=2),
    ]

    out = metrics.listing_metrics({"access_token": "tok"}, [ITEM, OTHER], {})

    assert out[ITEM]["offers"] == 1 and out[OTHER]["offers"] == 1

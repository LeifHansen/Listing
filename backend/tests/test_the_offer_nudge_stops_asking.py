"""Who gets offered a discount, and why the group shrinks when they have been.

"Send offers" is suggested off the WATCH COUNT on a live listing, and that is
a signal sending an offer does not move: the watchers are exactly the people
who were just offered the discount, and they are still watching a second
later. So without something else, the group a seller had just cleared would
come back in the same slot, same listings, same count — which is what a button
that does nothing looks like, and which this app has already been reported for
twice on its other two groups ("Lower prices" and "Fill in details"; see
models.Listing.price_lowered_at and test_a_group_says_how_many_are_left).

The stamp is the answer, and eBay agrees with it from the other side: it
refuses a second seller offer while the first is still live (error 150019), so
a nudge that came straight back would be advice that cannot be taken.

The rest of this file is about not offering a discount to nobody. Each gate
below is a listing eBay would refuse:

  * an AUCTION has bids, not a Buy It Now price to discount;
  * a listing with a buyer's Best Offer already waiting refuses a seller
    offer (150018) — and the seller has an answer to give there instead;
  * a listing nobody is watching has nobody to offer it to.

And the last section is the run itself: eBay decides eligibility, a listing it
will not carry an offer for is a skip rather than a failure, and a sweep that
could not be READ is not a store with no interested buyers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.services import ebay_offers, recommender


def _live(**over) -> dict:
    """A live listing with three photos and enough specifics that no other
    suggestion outranks this one."""
    listing = {"title": "A brass desk lamp", "price": 48.0,
               "images": ["a.jpg", "b.jpg", "c.jpg"],
               "enriched_at": "2026-01-01T00:00:00+00:00",
               "ebay_listing_id": "110040602158"}
    listing.update(over)
    return {"id": "s1", "status": "published", "listing": listing,
            "created_at": datetime.now(timezone.utc).isoformat()}


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _types(item, metrics) -> list[str]:
    return [r["type"] for r in recommender.recommend_for(item, metrics=metrics)]


# ------------------------------------------------------------- who it offers

def test_a_watched_listing_is_offered_to_its_watchers():
    recs = recommender.recommend_for(_live(), metrics={"watchers": 3})
    assert recs[0]["type"] == "send_offers"
    assert "3 watchers" in recs[0]["reason"]


def test_one_watcher_is_a_watcher():
    recs = recommender.recommend_for(_live(), metrics={"watchers": 1})
    assert "1 watcher —" in recs[0]["reason"]


def test_nobody_watching_is_nobody_to_offer():
    assert "send_offers" not in _types(_live(), {"watchers": 0})


def test_an_unknown_watch_count_is_not_a_watcher():
    """The sweep that counts watchers is bounded, so a big store can run out
    of pages before it runs out of listings (see metrics.listing_metrics). A
    listing it never reached says nothing, and nothing is not three people."""
    assert "send_offers" not in _types(_live(), {})
    assert "send_offers" not in _types(_live(), None)


def test_an_auction_is_never_offered_a_discount():
    """There is no Buy It Now price to take a percentage off, and eBay does
    not carry seller offers on auctions. Both auction formats."""
    for fmt in ("AUCTION", "AUCTION_BIN"):
        item = _live(listing_format=fmt, auction_start_price=9.99)
        assert "send_offers" not in _types(item, {"watchers": 4}), fmt


def test_a_listing_with_a_buyers_offer_waiting_is_left_alone():
    """eBay refuses a seller offer on it (150018), and the seller has an
    answer to give on that offer rather than a discount to send."""
    assert "send_offers" not in _types(_live(), {"watchers": 4, "offers": 1})


def test_a_draft_is_not_offered_to_anybody():
    item = _live()
    item["status"] = "unlisted"
    assert "send_offers" not in _types(item, {"watchers": 4})


def test_an_ended_listing_is_not_offered_to_anybody():
    item = _live()
    item["status"] = "ended"
    assert "send_offers" not in _types(item, {"watchers": 4})


# ----------------------------------------------------- and when it stops

def test_a_listing_just_offered_is_not_offered_again():
    """The whole point. eBay's own offer is still live at this range, and the
    watch count that suggested this has not moved because of it."""
    item = _live(offer_sent_at=_days_ago(1))
    assert "send_offers" not in _types(item, {"watchers": 3})


def test_the_nudge_comes_back_once_the_offer_has_had_its_run():
    """If the discount did not work, the advice is right again — and it
    returns on its own rather than needing the seller to ask."""
    item = _live(offer_sent_at=_days_ago(recommender.OFFER_QUIET_DAYS + 1))
    assert "send_offers" in _types(item, {"watchers": 3})


def test_the_quiet_period_outlasts_ebays_own_offer():
    """eBay's seller offer stands for up to 4 days (EBAY_US, EBAY_GB) and it
    refuses a second one while the first is live. A quiet period shorter than
    that would suggest work eBay cannot carry out."""
    assert recommender.OFFER_QUIET_DAYS > 4


def test_an_unreadable_stamp_does_not_silence_the_nudge():
    """A blank, a null, whitespace: none of them is a date, and reading one as
    "offered recently" would retire the suggestion for good."""
    for value in ("", "   ", None, "not-a-date"):
        item = _live(offer_sent_at=value)
        assert "send_offers" in _types(item, {"watchers": 3}), repr(value)


def test_the_stamp_is_the_servers_own_clock():
    stamp = recommender.offer_sent_stamp()
    assert datetime.fromisoformat(stamp).tzinfo is not None
    gap = abs((datetime.now(timezone.utc)
               - datetime.fromisoformat(stamp)).total_seconds())
    assert gap < 60


# --------------------------------------------- where it sits among the rest

def test_an_offer_outranks_a_price_cut_on_the_same_listing():
    """A stale listing with watchers earns both. The offer goes first because
    it is the one that does not move the price the rest of eBay sees: only
    the people already interested learn the number."""
    item = _live(offer_sent_at="")
    item["created_at"] = _days_ago(recommender.STALE_DAYS + 5)
    recs = recommender.recommend_for(item, metrics={"watchers": 2})
    assert [r["type"] for r in recs][:2] == ["send_offers", "lower_price"]
    # And the ranking keeps the strongest per listing, so the group a seller
    # sees this listing in is the offer one.
    assert recommender.ranked([item], {item["id"]: {"watchers": 2}}
                              )[0]["type"] == "send_offers"


def test_the_traffic_price_nudge_still_owns_the_unwatched_listing():
    """The two rules barely overlap by construction — that one wants views
    with NO watchers — and this checks the seam rather than assuming it."""
    item = _live()
    assert _types(item, {"views": 40, "watchers": 0}) == ["lower_price"]


# ------------------------------------------------------------ the run itself

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main, ratelimit  # noqa: E402


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "tok"})
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "offers@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("offers@example.com")["id"]
    return client, dbmod, uid


def _stock(dbmod, uid: str, rid: str, **over) -> None:
    assert dbmod.upsert_listing(
        rid, {"title": f"Item {rid}", "price": 48.0, "source": "ebay",
              "ebay_listing_id": f"11{rid}", **over},
        status="published", user_id=uid)


class _Ebay:
    """eBay saying yes, and recording what it was asked."""

    def __init__(self, eligible=None, fail=None):
        self.eligible = eligible
        self.fail = fail or {}
        self.sent: list[tuple] = []
        # The bits of the module the route reaches for beyond the two calls.
        self.ScopeError = ebay_offers.ScopeError
        self.OfferRefused = ebay_offers.OfferRefused
        self.skippable = ebay_offers.skippable
        self.validate_discount = ebay_offers.validate_discount
        self.clean_message = ebay_offers.clean_message

    def eligible_items(self, _creds, client=None):
        self.client = client
        if isinstance(self.eligible, Exception):
            raise self.eligible
        return self.eligible

    def send_offer(self, _creds, listing_id, percent, message="", client=None):
        # One connection carries the whole run: the sweep and every offer on
        # it. A client per listing is a TLS handshake per listing inside a
        # request the gateway is already timing.
        assert client is self.client
        self.sent.append((listing_id, percent, message))
        if listing_id in self.fail:
            raise self.fail[listing_id]
        return {"offer_id": f"offer-{listing_id}", "status": "PENDING"}


def test_the_press_offers_every_listing_it_was_given(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, "a")
    _stock(dbmod, uid, "b")
    fake = _Ebay(eligible={"11a", "11b"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    res = client.post("/api/ebay/send-offers",
                      json={"percent": 10, "listing_ids": ["a", "b"]})
    assert res.status_code == 200, res.text
    assert res.json()["changed"] == 2
    assert sorted(s[0] for s in fake.sent) == ["11a", "11b"]
    assert {s[1] for s in fake.sent} == {10.0}


def test_a_successful_send_is_recorded_on_the_listing(seller, monkeypatch):
    """The half of the button that makes the group shrink. Without this the
    suggestion is rebuilt from the same watch count and comes straight back."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "a")
    monkeypatch.setattr(main, "ebay_offers", _Ebay(eligible={"11a"}))

    client.post("/api/ebay/send-offers",
                json={"percent": 10, "listing_ids": ["a"]})
    stamp = dbmod.get_listing("a")["listing"]["offer_sent_at"]
    assert stamp
    assert "send_offers" not in _types(
        {"id": "a", "status": "published", "listing": {"offer_sent_at": stamp}},
        {"watchers": 3})


def test_a_refused_listing_is_never_recorded_as_offered(seller, monkeypatch):
    """A stamp on a listing nobody was offered anything would retire the
    suggestion for a week over a send that did not happen."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "a")
    monkeypatch.setattr(main, "ebay_offers", _Ebay(
        eligible={"11a"},
        fail={"11a": ebay_offers.OfferRefused("That listing has ended.",
                                              150011)}))

    res = client.post("/api/ebay/send-offers",
                      json={"percent": 10, "listing_ids": ["a"]})
    assert res.json()["skipped"] == 1
    assert not dbmod.get_listing("a")["listing"].get("offer_sent_at")


def test_a_listing_ebay_has_no_buyers_for_is_skipped_without_a_call(
        seller, monkeypatch):
    """eBay's sweep says who is worth offering. Sending blind would spend a
    call per listing to be told 150020."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "a")
    _stock(dbmod, uid, "b")
    fake = _Ebay(eligible={"11a"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    res = client.post("/api/ebay/send-offers",
                      json={"percent": 10, "listing_ids": ["a", "b"]})
    body = res.json()
    assert (body["changed"], body["skipped"]) == (1, 1)
    assert [s[0] for s in fake.sent] == ["11a"]


def test_a_sweep_that_could_not_be_read_still_sends(seller, monkeypatch):
    """The sharp one. An unreadable sweep is not a store with no interested
    buyers — reading it that way would report every listing as skipped and
    look exactly like nobody is watching anything. The run goes ahead and
    lets eBay refuse what it will not carry."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, "a")
    fake = _Ebay(eligible=RuntimeError("eligible items failed (500)"))
    monkeypatch.setattr(main, "ebay_offers", fake)

    res = client.post("/api/ebay/send-offers",
                      json={"percent": 10, "listing_ids": ["a"]})
    assert res.json()["changed"] == 1
    assert [s[0] for s in fake.sent] == ["11a"]


def test_a_token_without_the_scope_asks_for_a_reconnect(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, "a")
    monkeypatch.setattr(main, "ebay_offers",
                        _Ebay(eligible=ebay_offers.ScopeError()))

    res = client.post("/api/ebay/send-offers",
                      json={"percent": 10, "listing_ids": ["a"]})
    assert res.status_code == 400
    assert "econnect" in res.json()["detail"]


def test_a_listing_that_is_no_longer_live_is_skipped(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, "a")
    dbmod.upsert_listing("a", {"title": "Item a", "ebay_listing_id": "11a"},
                         status="ended", user_id=uid)
    fake = _Ebay(eligible={"11a"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    res = client.post("/api/ebay/send-offers",
                      json={"percent": 10, "listing_ids": ["a"]})
    assert res.json()["skipped"] == 1
    assert fake.sent == []


def test_one_listings_refusal_does_not_strand_the_rest(seller, monkeypatch):
    client, dbmod, uid = seller
    for rid in ("a", "b", "c"):
        _stock(dbmod, uid, rid)
    fake = _Ebay(eligible={"11a", "11b", "11c"},
                 fail={"11b": ebay_offers.OfferRefused("eBay is unwell", 0)})
    monkeypatch.setattr(main, "ebay_offers", fake)

    body = client.post("/api/ebay/send-offers",
                       json={"percent": 10,
                             "listing_ids": ["a", "b", "c"]}).json()
    assert (body["changed"], body["failed"]) == (2, 1)


def test_another_sellers_listing_is_not_offered(seller, monkeypatch):
    """The lookup is by id and ownership is enforced inside it; what comes
    back to the caller is "not found", not somebody else's listing."""
    client, dbmod, uid = seller
    dbmod.upsert_listing("theirs", {"title": "Not yours",
                                    "ebay_listing_id": "11theirs"},
                         status="published", user_id="someone-else")
    fake = _Ebay(eligible={"11theirs"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    body = client.post("/api/ebay/send-offers",
                       json={"percent": 10, "listing_ids": ["theirs"]}).json()
    assert body["skipped"] == 1
    assert fake.sent == []


def test_a_run_bigger_than_one_pass_reports_what_is_left(seller, monkeypatch):
    """Each offer is its own eBay call, so a run is bounded to fit inside the
    gateway's patience and the remainder comes back as `deferred` rather than
    being dropped without saying so."""
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "BULK_OFFER_CAP", 2)
    for rid in ("a", "b", "c"):
        _stock(dbmod, uid, rid)
    fake = _Ebay(eligible={"11a", "11b", "11c"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    body = client.post("/api/ebay/send-offers",
                       json={"percent": 10,
                             "listing_ids": ["a", "b", "c"]}).json()
    assert body["deferred"] == 1
    assert len(fake.sent) == 2


def test_a_discount_below_ebays_floor_is_refused_before_any_call(
        seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, "a")
    fake = _Ebay(eligible={"11a"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    res = client.post("/api/ebay/send-offers",
                      json={"percent": 1, "listing_ids": ["a"]})
    assert res.status_code == 400
    assert fake.sent == []


def test_naming_no_listings_is_refused(seller, monkeypatch):
    client, _dbmod, _uid = seller
    monkeypatch.setattr(main, "ebay_offers", _Ebay(eligible=set()))
    assert client.post("/api/ebay/send-offers",
                       json={"percent": 10, "listing_ids": []}
                       ).status_code == 400


def test_the_scope_of_a_run_is_what_the_caller_named(seller, monkeypatch):
    """A group of two can never turn into the seller's whole store."""
    client, dbmod, uid = seller
    for rid in ("a", "b", "c"):
        _stock(dbmod, uid, rid)
    fake = _Ebay(eligible={"11a", "11b", "11c"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    client.post("/api/ebay/send-offers",
                json={"percent": 10, "listing_ids": ["a", "b"]})
    assert sorted(s[0] for s in fake.sent) == ["11a", "11b"]


def test_an_unbounded_selection_is_refused_rather_than_run(seller, monkeypatch):
    """Same bound as the bulk price drop: the lookup is BY id, so an
    unbounded body is an unbounded `IN (...)`."""
    client, _dbmod, _uid = seller
    fake = _Ebay(eligible=set())
    monkeypatch.setattr(main, "ebay_offers", fake)

    res = client.post(
        "/api/ebay/send-offers",
        json={"percent": 10,
              "listing_ids": [str(i) for i in range(main.BULK_SELECT_CAP + 1)]})
    assert res.status_code == 400
    assert fake.sent == []


def test_the_group_knows_how_big_one_press_is(seller):
    """The dashboard renders the button, so it has to be told what one tap
    reaches — or it promises the whole badge and then defers most of it."""
    assert main._bulk_caps()["send_offers"] == main.BULK_OFFER_CAP

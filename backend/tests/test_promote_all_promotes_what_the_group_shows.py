""""Promote all" has to mean what the group it sits on means.

The Dashboard's "Promote listings" group is built from two signals: this
app's own Promote flag, and eBay's live ad list -- the thing that catches an
ad the seller created in Seller Hub. A listing eBay says is already running
an ad is not in the group, and when eBay could not be asked the group is not
shown at all (test_promote_nudge_needs_an_answer): promoting costs a
percentage of the sale, and a fee is not recommended on an unanswered
question.

The button on that group read only the first signal. `POST /api/ebay/
promote-all` promoted every live listing without our flag -- the ones eBay
was already running ads on included, and during an ads outage too -- and it
promoted the seller's WHOLE store, when the group it belongs to is capped
and the confirm dialog had just named the group's count. A seller whose
store is promoted in Seller Hub, shown "Promote listings · 50", pressed the
button and paid this app's ad on top of their own, on more listings than
they were asked about.

So the route now takes the same two signals the group does, refuses when
eBay did not answer, and promotes the listings it was handed rather than the
store.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "ads@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("ads@example.com")["id"]
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "tok-abcdefghijklmnop"})
    monkeypatch.setattr(main, "_rates_by_record_id", lambda creds, items: {})
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)
    return client, dbmod, uid


@pytest.fixture()
def ebay(monkeypatch):
    """What eBay says about the seller's ads: `ads` is the live ad map keyed
    by item id, `known` whether the lookup answered at all. Returns the
    recorder of every promotion this app then tries to start."""
    started: list[str] = []

    def _serve(ads=None, known=True):
        monkeypatch.setattr(main.promotions, "active_ads_status",
                            lambda creds: (ads or {}, known))

        def _promote(rid, listing, creds, rate=None, **_kw):
            started.append(rid)
            listing.promote = True
            return {"promoted": True}
        monkeypatch.setattr(main, "_promote", _promote)
        return started
    return _serve


def _live(dbmod, uid, lid, item_id, **over):
    data = {"title": lid, "price": 20.0, "ebay_listing_id": item_id, **over}
    assert dbmod.upsert_listing(lid, data, status="published", user_id=uid)


# ---------------------------------------------- the two signals, both read

def test_a_listing_ebay_already_promotes_is_left_alone(seller, ebay):
    """The finding. eBay is running an ad on `a` (created in Seller Hub, say)
    and the group therefore did not list it; the button used to promote it
    anyway."""
    client, dbmod, uid = seller
    _live(dbmod, uid, "a", "111")
    _live(dbmod, uid, "b", "222")
    started = ebay(ads={"111": {"rate": "8.5", "status": "RUNNING"}})

    r = client.post("/api/ebay/promote-all", json={"listing_ids": ["a", "b"]})

    assert r.status_code == 200
    assert started == ["b"]
    assert r.json()["promoted"] == 1
    assert r.json()["already_promoted"] == 1


def test_our_own_flag_still_counts(seller, ebay):
    client, dbmod, uid = seller
    _live(dbmod, uid, "a", "111", promote=True, ad_rate_percent=8.0)
    _live(dbmod, uid, "b", "222")
    started = ebay(ads={})

    r = client.post("/api/ebay/promote-all", json={"listing_ids": ["a", "b"]})

    assert started == ["b"]
    assert r.json()["already_promoted"] == 1


def test_nothing_is_promoted_when_ebay_did_not_answer(seller, ebay):
    """The same rule the group keeps: an unanswered lookup is not "no ads".
    A seller who promotes in Seller Hub pressed this during an ads-API blip
    and was charged for a second ad on every listing."""
    client, dbmod, uid = seller
    _live(dbmod, uid, "a", "111")
    started = ebay(ads={}, known=False)

    r = client.post("/api/ebay/promote-all", json={"listing_ids": ["a"]})

    assert r.status_code == 503
    assert "couldn't read your eBay ads" in r.json()["detail"]
    assert started == []


# ------------------------------------------- the group, not the whole store

def test_the_listings_it_is_handed_are_the_ones_it_promotes(seller, ebay, monkeypatch):
    """"Promote 2 listings?" then promotes two, on a store of three. The store
    read is not even consulted: the group is the request."""
    client, dbmod, uid = seller
    for lid in ("a", "b", "c"):
        _live(dbmod, uid, lid, lid * 3)
    started = ebay(ads={})
    monkeypatch.setattr(dbmod, "list_listings",
                        lambda *a, **k: pytest.fail("read the whole store"))

    r = client.post("/api/ebay/promote-all", json={"listing_ids": ["a", "b"]})

    assert sorted(started) == ["a", "b"]
    assert r.json()["promoted"] == 2


def test_no_ids_still_reaches_the_live_store_with_the_same_check(seller, ebay):
    """The old contract for anything that still posts an empty body -- with
    the ad check it never had."""
    client, dbmod, uid = seller
    _live(dbmod, uid, "a", "111")
    _live(dbmod, uid, "b", "222")
    assert dbmod.upsert_listing("d", {"title": "d"}, status="draft", user_id=uid)
    started = ebay(ads={"222": {"rate": "5.0", "status": "RUNNING"}})

    r = client.post("/api/ebay/promote-all", json={})

    assert started == ["a"]
    assert r.json() == {"promoted": 1, "total": 1, "already_promoted": 1,
                        "failed": 0, "skipped": 0, "deferred": 0,
                        "needs_reconnect": False}


def test_a_listing_that_is_not_live_or_not_theirs_is_reported_not_promoted(seller, ebay):
    client, dbmod, uid = seller
    _live(dbmod, uid, "a", "111")
    assert dbmod.upsert_listing("d", {"title": "d"}, status="draft", user_id=uid)
    started = ebay(ads={})

    r = client.post("/api/ebay/promote-all",
                    json={"listing_ids": ["a", "d", "somebody-elses"]})

    assert started == ["a"]
    assert r.json()["skipped"] == 2


def test_one_run_is_capped_and_the_rest_deferred(seller, ebay, monkeypatch):
    """Serial eBay calls, so a run is bounded like every other bulk pass and
    the remainder is handed back for a second one rather than timing out."""
    client, dbmod, uid = seller
    for lid in ("a", "b", "c"):
        _live(dbmod, uid, lid, lid * 3)
    started = ebay(ads={})
    monkeypatch.setattr(main, "BULK_PROMOTE_CAP", 2)

    r = client.post("/api/ebay/promote-all", json={"listing_ids": ["a", "b", "c"]})

    assert len(started) == 2
    assert r.json()["deferred"] == 1


def test_the_cap_rides_along_with_the_suggestions():
    """The group renders the button, so it has to know what one tap reaches --
    the same way the price and enrich groups already do."""
    assert main._bulk_caps()["promote"] == main.BULK_PROMOTE_CAP


def test_too_many_ids_is_refused_not_read(seller, ebay):
    client, dbmod, uid = seller
    ebay(ads={})
    ids = [f"l{i}" for i in range(main.BULK_SELECT_CAP + 1)]
    assert client.post("/api/ebay/promote-all",
                       json={"listing_ids": ids}).status_code == 400


def test_a_failed_promotion_is_counted_not_hidden(seller, ebay, monkeypatch):
    client, dbmod, uid = seller
    _live(dbmod, uid, "a", "111")
    ebay(ads={})
    monkeypatch.setattr(main, "_promote",
                        lambda *a, **k: {"promoted": False,
                                         "message": "eBay said no"})

    r = client.post("/api/ebay/promote-all", json={"listing_ids": ["a"]})

    assert r.json()["promoted"] == 0
    assert r.json()["failed"] == 1


# --------------------------------------- what the lookup says about itself

def test_the_ads_lookup_says_what_each_campaign_came_back_with(monkeypatch, caplog):
    """A seller whose whole store is promoted in Seller Hub and who is still
    told to promote it is looking at one of two things: a campaign that
    listed no ads at all, or ads keyed by something our records do not
    carry. Nothing on the screen can tell them apart; the log can."""
    from backend.services import promotions

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self.text = ""
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def get(self, url, headers=None, params=None):
            if url.endswith("/ad"):
                return _Resp({"ads": [], "total": 0})
            return _Resp({"campaigns": [{
                "campaignId": "c1", "campaignStatus": "RUNNING",
                "fundingStrategy": {"fundingModel": "COST_PER_SALE"},
                "campaignTargetingType": "SMART",
                "campaignCriterion": {"autoSelectFutureInventory": True},
            }], "total": 1})

    promotions._ADS_CACHE.clear()
    monkeypatch.setattr(promotions.httpx, "Client", lambda *a, **k: _Client())
    with caplog.at_level("INFO", logger="thryft.promotions"):
        found, known = promotions.active_ads_status(
            {"access_token": "tok-abcdefghijklmnop"})

    assert (found, known) == ({}, True)
    assert "c1:RUNNING:COST_PER_SALE:SMART:rule+auto ads=0" in caplog.text

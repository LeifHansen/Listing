""""Send offers to watchers…" on the Manage tab offers the ticked listings.

The dashboard's group button works its own set out from the ranking; this is
the same offer over the listings the seller picked. It shares that run's
one-per-account slot — two runs at once would offer the same watchers twice,
and eBay refuses a second seller offer while the first is live — and its
worker re-reads each listing with ownership checked, because these ids came
from a request.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.services import ebay_offers


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "tok"})
    main._OFFER_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "offerticked@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("offerticked@example.com")["id"]
    return client, dbmod, uid


def _stock(dbmod, uid, ids, owner=None):
    for rid in ids:
        assert dbmod.upsert_listing(
            rid, {"title": f"Item {rid}", "price": 48.0, "source": "ebay",
                  "ebay_listing_id": f"11{rid}"},
            status="published", user_id=owner or uid)


class _Ebay:
    def __init__(self):
        self.sent: list[tuple] = []
        self.ScopeError = ebay_offers.ScopeError
        self.OfferRefused = ebay_offers.OfferRefused
        self.skippable = ebay_offers.skippable
        self.validate_discount = ebay_offers.validate_discount
        self.clean_message = ebay_offers.clean_message

    def eligible_items(self, _creds, client=None):
        return None   # the sweep could not be read: send, let eBay decide

    def send_offer(self, _creds, listing_id, percent, message="", client=None):
        self.sent.append((listing_id, percent))
        return {"offer_id": f"offer-{listing_id}"}


def _finish(client, job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError("send-offers-ticked job never finished")


def _press(client, ids, percent=15):
    return client.post("/api/ebay/send-offers-ticked",
                       json={"percent": percent, "listing_ids": ids})


def test_the_ticked_listings_get_the_offer_and_nothing_else(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["A", "B", "C"])
    ebay = _Ebay()
    monkeypatch.setattr(main, "ebay_offers", ebay)

    result = _finish(client, _press(client, ["A", "C"]).json()["job_id"])
    assert result["changed"] == 2 and result["percent"] == 15
    assert sorted(ebay.sent) == [("11A", 15), ("11C", 15)]
    assert dbmod.get_listing("A")["listing"]["offer_sent_at"]
    assert not dbmod.get_listing("B")["listing"].get("offer_sent_at")


def test_another_sellers_listing_is_never_offered(seller, monkeypatch):
    client, dbmod, uid = seller
    other = dbmod.create_user("other-offer", "them@example.com", "x" * 60)
    _stock(dbmod, uid, ["theirs"], owner=other["id"])
    ebay = _Ebay()
    monkeypatch.setattr(main, "ebay_offers", ebay)

    result = _finish(client, _press(client, ["theirs"]).json()["job_id"])
    assert result["skipped"] == 1
    assert ebay.sent == []


def test_it_shares_the_dashboards_one_run_slot(seller, monkeypatch):
    """A run already going — from either button — is joined, not doubled."""
    client, dbmod, uid = seller
    from backend.services import jobstore
    jobstore.register("running-offers", {
        "id": "running-offers", "kind": "offers", "phase": "offering",
        "done": False, "error": None, "current": 0, "total_items": 7}, uid=uid)
    main._OFFER_JOBS[uid] = "running-offers"
    _stock(dbmod, uid, ["A"])

    body = _press(client, ["A"]).json()
    assert body["job_id"] == "running-offers" and body["joined"] is True


@pytest.mark.parametrize("percent", [0, 4, 51, "lots"])
def test_a_discount_outside_ebays_range_is_refused(seller, percent):
    client, _dbmod, uid = seller
    assert _press(client, ["A"], percent).status_code == 400
    assert uid not in main._OFFER_JOBS

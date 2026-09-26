""""Send offers…" offers all of them.

The same shape as "Lower all…" (see test_lower_all_lowers_all_of_them), on
the group next to it. The button sent the rows the dashboard happened to be
holding to /api/ebay/send-offers, which offers at most BULK_OFFER_CAP of them
per request and defers the rest — so a group of 41 was reached a slice at a
time, and with the cap set low, one listing per press under a verb that says
the whole group.

The press names no ids and has no cap now. The server works the group out
from the same ranking the dashboard renders — including eBay's own
eligibility sweep, which decides who is in the group at all — and runs it as
a job the client polls.

What it must NOT do is offer the same watchers twice. A second press while
the first is running joins that run; eBay would refuse a second seller offer
while the first is live, and every one of those would come back a refusal.
"""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.services import ebay_offers, jobstore

# Somebody watching and no offer out: the rule that puts a listing in "Send
# offers".
WATCHED = {"watchers": 3, "views": 40}


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "tok"})
    main._OFFER_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "offerall@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("offerall@example.com")["id"]
    return client, dbmod, uid


def _stock(dbmod, uid: str, ids) -> None:
    for rid in ids:
        assert dbmod.upsert_listing(
            rid, {"title": f"Item {rid}", "price": 48.0, "source": "ebay",
                  "ebay_listing_id": f"11{rid}"},
            status="published", user_id=uid)


def _metrics(monkeypatch, by_id: dict) -> None:
    monkeypatch.setattr(main, "_metrics_by_record_id",
                        lambda creds, items, status=None, fresh=False: by_id)


class _Ebay:
    """eBay saying yes to every offer, and recording what it was asked.

    Stands in for the whole module: the dashboard's ranking reads the cached
    sweep (who is in the group), the run reads a fresh one (who to send to).
    """

    def __init__(self, eligible=None, fail=None):
        self.eligible = eligible
        self.fail = fail or {}
        self.sent: list[tuple] = []
        self.client = None
        self.ScopeError = ebay_offers.ScopeError
        self.OfferRefused = ebay_offers.OfferRefused
        self.skippable = ebay_offers.skippable
        self.validate_discount = ebay_offers.validate_discount
        self.clean_message = ebay_offers.clean_message

    def eligible_cached(self, _creds):
        return None if isinstance(self.eligible, Exception) else self.eligible

    def eligible_items(self, _creds, client=None):
        self.client = client
        if isinstance(self.eligible, Exception):
            raise self.eligible
        return self.eligible

    def send_offer(self, _creds, listing_id, percent, message="", client=None):
        # One connection carries the whole run: the sweep and every offer.
        assert client is self.client
        self.sent.append((listing_id, percent))
        if listing_id in self.fail:
            raise self.fail[listing_id]
        return {"offer_id": f"offer-{listing_id}", "status": "PENDING"}


class _HeldEbay(_Ebay):
    """eBay taking its time over the first offer, so a test can act while a
    run is provably in flight."""

    def __init__(self, eligible=None):
        super().__init__(eligible)
        self.entered, self.release = threading.Event(), threading.Event()
        self.holding = None

    def send_offer(self, _creds, listing_id, percent, message="", client=None):
        if not self.entered.is_set():
            self.holding = listing_id
        self.entered.set()
        assert self.release.wait(10), "the test never let the offer go"
        return super().send_offer(_creds, listing_id, percent, message, client)


def _finish(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"send-offers-all job {job_id} never finished")


def _result(client, job_id: str) -> dict:
    body = _finish(client, job_id)
    assert not body.get("error"), body["error"]
    return body["result"]


def _offer_group(client) -> int:
    return client.get("/api/insights").json()["group_totals"].get("send_offers", 0)


# ------------------------------------------------------------ the report

def test_one_press_offers_every_listing_even_with_a_cap_of_one(seller, monkeypatch):
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "BULK_OFFER_CAP", 1)
    ids = [f"O{i}" for i in range(5)]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: WATCHED for rid in ids})
    fake = _Ebay(eligible={f"11{rid}" for rid in ids})
    monkeypatch.setattr(main, "ebay_offers", fake)
    assert _offer_group(client) == 5, "the group has to be offered first"

    started = client.post("/api/ebay/send-offers-all", json={"percent": 10})
    assert started.status_code == 200, started.text
    body = started.json()
    assert (body["total"], body["deferred"]) == (5, 0)

    result = _result(client, body["job_id"])
    assert (result["changed"], result["deferred"], result["percent"]) == (5, 0, 10)
    assert sorted(s[0] for s in fake.sent) == sorted(f"11{rid}" for rid in ids)
    for rid in ids:
        assert dbmod.get_listing(rid)["listing"].get("offer_sent_at")
    assert _offer_group(client) == 0, "the group the seller pressed is gone"


def test_it_reaches_past_the_rows_the_dashboard_was_sent(seller, monkeypatch):
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "INSIGHTS_GROUP_CAP", 2)
    ids = [f"R{i}" for i in range(4)]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: WATCHED for rid in ids})
    monkeypatch.setattr(main, "ebay_offers",
                        _Ebay(eligible={f"11{rid}" for rid in ids}))

    insights = client.get("/api/insights").json()
    shipped = [r for r in insights["recommendations"] if r["type"] == "send_offers"]
    assert len(shipped) == 2 and insights["group_totals"]["send_offers"] == 4

    body = client.post("/api/ebay/send-offers-all", json={"percent": 10}).json()
    assert _result(client, body["job_id"])["changed"] == 4


def test_nothing_outside_the_group_is_offered(seller, monkeypatch):
    """Scope is worked out here instead of being sent, and it is still the
    group. A stale price with nobody watching belongs to "Lower prices"; a
    watched listing eBay's sweep leaves out is not in the group at all."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["watched", "unswept", "stale"])
    _metrics(monkeypatch, {"watched": WATCHED, "unswept": WATCHED,
                           "stale": {"views": 40, "watchers": 0}})
    fake = _Ebay(eligible={"11watched"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    body = client.post("/api/ebay/send-offers-all", json={"percent": 10}).json()
    assert body["total"] == 1
    _result(client, body["job_id"])
    assert fake.sent == [("11watched", 10.0)]


# ------------------------------------------------------------ and only once

def test_a_second_press_joins_the_run_instead_of_offering_twice(seller, monkeypatch):
    client, dbmod, uid = seller
    ids = ["D1", "D2"]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: WATCHED for rid in ids})
    fake = _HeldEbay(eligible={"11D1", "11D2"})
    monkeypatch.setattr(main, "ebay_offers", fake)

    first = client.post("/api/ebay/send-offers-all", json={"percent": 10}).json()
    assert fake.entered.wait(10), "the run never reached eBay"
    again = client.post("/api/ebay/send-offers-all", json={"percent": 10}).json()
    assert again["job_id"] == first["job_id"]

    fake.release.set()
    _result(client, first["job_id"])
    assert sorted(s[0] for s in fake.sent) == ["11D1", "11D2"], \
        "each listing's watchers are offered exactly once"


def test_a_listing_that_ends_mid_run_is_skipped(seller, monkeypatch):
    client, dbmod, uid = seller
    ids = ["S1", "S2", "S3"]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: WATCHED for rid in ids})
    fake = _HeldEbay(eligible={f"11{rid}" for rid in ids})
    monkeypatch.setattr(main, "ebay_offers", fake)

    body = client.post("/api/ebay/send-offers-all", json={"percent": 10}).json()
    assert fake.entered.wait(10)
    for rid in ids:
        if f"11{rid}" != fake.holding:
            rec = dbmod.get_listing(rid)
            dbmod.upsert_listing(rid, rec["listing"], status="ended", user_id=uid)
    fake.release.set()
    result = _result(client, body["job_id"])

    assert (result["changed"], result["skipped"]) == (1, 2)
    assert [s[0] for s in fake.sent] == [fake.holding]


# ------------------------------------------------------------ eBay's side

def test_a_sweep_that_could_not_be_read_still_sends(seller, monkeypatch):
    """An unreadable sweep is not a store with no interested buyers. The run
    goes ahead and lets eBay refuse what it will not carry."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["a"])
    _metrics(monkeypatch, {"a": WATCHED})
    fake = _Ebay(eligible=RuntimeError("eligible items failed (500)"))
    monkeypatch.setattr(main, "ebay_offers", fake)

    body = client.post("/api/ebay/send-offers-all", json={"percent": 10}).json()
    assert _result(client, body["job_id"])["changed"] == 1
    assert [s[0] for s in fake.sent] == ["11a"]


def test_a_token_without_the_scope_asks_for_a_reconnect(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["a"])
    _metrics(monkeypatch, {"a": WATCHED})
    fake = _Ebay(eligible=ebay_offers.ScopeError())
    monkeypatch.setattr(main, "ebay_offers", fake)

    body = client.post("/api/ebay/send-offers-all", json={"percent": 10}).json()
    done = _finish(client, body["job_id"])
    assert "econnect" in done["error"]
    assert fake.sent == []
    assert uid not in main._OFFER_JOBS, "a failed run must not block the next"


def test_one_listings_refusal_does_not_strand_the_rest(seller, monkeypatch):
    client, dbmod, uid = seller
    ids = ["a", "b", "c"]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: WATCHED for rid in ids})
    monkeypatch.setattr(main, "ebay_offers", _Ebay(
        eligible={"11a", "11b", "11c"},
        fail={"11b": ebay_offers.OfferRefused("eBay is unwell", 0)}))

    body = client.post("/api/ebay/send-offers-all", json={"percent": 10}).json()
    result = _result(client, body["job_id"])
    assert (result["changed"], result["failed"]) == (2, 1)


# ------------------------------------------------------------ edges

def test_asking_with_nobody_to_offer_is_refused(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["quiet"])
    _metrics(monkeypatch, {})
    monkeypatch.setattr(main, "ebay_offers", _Ebay(eligible=set()))
    assert client.post("/api/ebay/send-offers-all",
                       json={"percent": 10}).status_code == 400
    assert uid not in main._OFFER_JOBS


@pytest.mark.parametrize("percent", [1, 4, 60, "lots", None])
def test_a_discount_it_would_refuse_one_at_a_time_is_refused(seller, percent):
    client, _dbmod, _uid = seller
    assert client.post("/api/ebay/send-offers-all",
                       json={"percent": percent}).status_code == 400


def test_it_needs_ebay(seller, monkeypatch):
    client, _dbmod, _uid = seller
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: None)
    assert client.post("/api/ebay/send-offers-all",
                       json={"percent": 10}).status_code == 400


def test_it_needs_a_seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    assert TestClient(main.app).post(
        "/api/ebay/send-offers-all", json={"percent": 10}).status_code == 401


def test_the_dashboard_is_not_told_a_cap_the_button_no_longer_has():
    assert "send_offers" not in main._bulk_caps()


def test_a_run_a_restart_cut_short_says_what_it_was_doing():
    msg = jobstore.interrupted_message(
        {"kind": "offers", "total_items": 41, "current": 12})
    assert "sending your offers" in msg
    assert "12 of 41" in msg

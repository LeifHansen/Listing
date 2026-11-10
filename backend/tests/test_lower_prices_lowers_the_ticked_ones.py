""""Lower prices…" lowers the ticked ones — all of them, and only them.

The price cut lives on the Manage tab now, beside End & relist: tick the live
listings, press the button, enter a percentage. It used to be the dashboard's
"Lower prices" suggestion group with a "Lower all…" button on it, and that
group is gone on request — whether a price should come down is the seller's
call, not a chore to be nagged about (see services/recommender).

What stays from that history is the shape the button earned there. The route
takes exactly the ids the seller ticked, reads them by id with ownership
enforced inside the read, and runs them as a job the client polls — a page of
ticks is a page of serial eBay revises, and no request should hold that open.
And it must NOT cut twice: a second press while the first is running joins
that run rather than starting another over the same listings — 20% off and
then 20% off that is a price nobody asked for.
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
from backend.services import bulk_actions, jobstore


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "t"})
    main._REPRICE_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "lowerticked@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("lowerticked@example.com")["id"]
    return client, dbmod, uid


def _stock(dbmod, uid: str, ids) -> None:
    for rid in ids:
        assert dbmod.upsert_listing(
            rid, {"title": f"Item {rid}", "price": 40.0,
                  "images": ["1.jpg", "2.jpg", "3.jpg"],
                  "ebay_listing_id": f"11{rid}"},
            status="published", user_id=uid)


class _AcceptingEbay:
    """eBay saying yes, persisting the record the way the real provider does
    — which is the step that carries the price_lowered_at stamp."""

    def __init__(self, dbmod, uid):
        self.db, self.uid, self.revised = dbmod, uid, []

    def publish(self, ctx, creds):
        from backend.marketplaces.base import PublishOutcome
        self.revised.append(ctx.session_id)
        self.db.upsert_listing(ctx.session_id, ctx.listing.model_dump(),
                               status="published", user_id=self.uid)
        return PublishOutcome(ok=True, message="Revised.", status="published")


class _HeldEbay(_AcceptingEbay):
    """eBay taking its time over the first revise, so a test can act while a
    run is provably in flight."""

    def __init__(self, dbmod, uid):
        super().__init__(dbmod, uid)
        self.entered, self.release = threading.Event(), threading.Event()
        self.holding = None

    def publish(self, ctx, creds):
        if not self.entered.is_set():
            self.holding = ctx.session_id
        self.entered.set()
        assert self.release.wait(10), "the test never let the revise finish"
        return super().publish(ctx, creds)


def _finish(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError(f"lower-prices job {job_id} never finished")


def _press(client, ids, percent=10):
    return client.post("/api/ebay/lower-prices",
                       json={"percent": percent, "listing_ids": ids})


# ------------------------------------------------------------ the ticks

def test_one_press_lowers_every_ticked_price(seller, monkeypatch):
    client, dbmod, uid = seller
    ids = [f"P{i}" for i in range(5)]
    _stock(dbmod, uid, ids)
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    started = _press(client, ids, 20)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["total"] == 5

    result = _finish(client, body["job_id"])
    assert (result["changed"], result["skipped"], result["failed"]) == (5, 0, 0)
    assert result["percent"] == 20
    assert sorted(ebay.revised) == ids
    cut = bulk_actions.lower_price(40.0, 20)
    for rid in ids:
        stored = dbmod.get_listing(rid)["listing"]
        assert stored["price"] == cut
        assert stored["price_lowered_at"], "the markdown is recorded"


def test_nothing_outside_the_ticks_is_touched(seller, monkeypatch):
    """Scope is never implicit. The run is the ids it was handed — never the
    rest of the page, never the store."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["ticked", "untouched", "also-untouched"])
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    body = _press(client, ["ticked"]).json()
    assert body["total"] == 1
    _finish(client, body["job_id"])

    assert ebay.revised == ["ticked"]
    assert dbmod.get_listing("untouched")["listing"]["price"] == 40.0
    assert dbmod.get_listing("also-untouched")["listing"]["price"] == 40.0


def test_a_cheap_listing_is_cut_not_skipped(seller, monkeypatch):
    """At 10% a $4.99 listing's cut is $4.49, whose nearest .99 is the $4.99
    it started on — which used to come back as a no-op and a skip."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["cheap"])
    stored = dbmod.get_listing("cheap")["listing"]
    assert dbmod.upsert_listing("cheap", {**stored, "price": 4.99},
                                status="published", user_id=uid)
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    result = _finish(client, _press(client, ["cheap"]).json()["job_id"])
    assert (result["changed"], result["skipped"]) == (1, 0)
    assert dbmod.get_listing("cheap")["listing"]["price"] == 4.49


def test_an_id_that_is_not_the_sellers_is_reported_not_dropped(seller, monkeypatch):
    """Another seller's listing, or one that never existed, comes back as a
    skip in the report rather than vanishing from the totals — and is never
    revised."""
    client, dbmod, uid = seller
    other = dbmod.create_user("other-id", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", {"title": "t", "price": 40.0,
                                           "ebay_listing_id": "99"},
                                status="published", user_id=other["id"])
    _stock(dbmod, uid, ["mine"])
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    body = _press(client, ["mine", "theirs", "never-existed"]).json()
    assert body["total"] == 3
    result = _finish(client, body["job_id"])

    assert (result["changed"], result["skipped"]) == (1, 2)
    assert ebay.revised == ["mine"]
    assert dbmod.get_listing("theirs")["listing"]["price"] == 40.0
    skipped = {r["listing_id"]: r for r in result["results"]["skipped"]}
    assert set(skipped) == {"theirs", "never-existed"}
    for row in skipped.values():
        assert row["message"] == "Listing not found."
        assert row["needs_you"] is False


def test_the_route_does_not_read_the_whole_store_to_find_the_ticks(
        seller, monkeypatch):
    """The ids, asked for. Reading the seller's newest page and filtering it
    reported anything outside that page as "Listing not found."."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("draft", {"title": "t", "price": 40.0},
                                status="draft", user_id=uid)

    def _no(*a, **k):
        raise AssertionError("lower-prices read the whole store to find the "
                             "handful of listings it was handed")
    monkeypatch.setattr(dbmod, "list_listings", _no)
    monkeypatch.setattr(dbmod, "list_listings_best_effort", _no)

    result = _finish(client, _press(client, ["draft"]).json()["job_id"])
    # Not live, so it is skipped -- which is the honest answer and proves the
    # record was found and read rather than reported missing.
    assert result["results"]["skipped"][0]["message"].startswith("No longer live")


# ------------------------------------------------------------ and only once

def test_a_second_press_joins_the_run_instead_of_cutting_twice(seller, monkeypatch):
    client, dbmod, uid = seller
    ids = ["D1", "D2"]
    _stock(dbmod, uid, ids)
    ebay = _HeldEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    first = _press(client, ids, 20).json()
    assert ebay.entered.wait(10), "the run never reached eBay"
    again = _press(client, ids, 20).json()
    assert again["job_id"] == first["job_id"]
    assert again["joined"] is True

    ebay.release.set()
    _finish(client, first["job_id"])
    assert sorted(ebay.revised) == ids, "each listing is revised exactly once"
    cut = bulk_actions.lower_price(40.0, 20)
    for rid in ids:
        assert dbmod.get_listing(rid)["listing"]["price"] == cut


def test_a_listing_that_sells_mid_run_is_skipped(seller, monkeypatch):
    """The set is fixed when the button is pressed, and a page of ticks takes
    a while. Each listing is judged on what it is when its turn comes."""
    client, dbmod, uid = seller
    ids = ["S1", "S2", "S3"]
    _stock(dbmod, uid, ids)
    ebay = _HeldEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    body = _press(client, ids).json()
    assert ebay.entered.wait(10)
    # Everything but the one already at eBay sells while it is there.
    for rid in ids:
        if rid != ebay.holding:
            rec = dbmod.get_listing(rid)
            dbmod.upsert_listing(rid, rec["listing"], status="sold", user_id=uid)
    ebay.release.set()
    result = _finish(client, body["job_id"])

    assert (result["changed"], result["skipped"]) == (1, 2)
    assert ebay.revised == [ebay.holding]
    for rid in ids:
        if rid != ebay.holding:
            assert dbmod.get_listing(rid)["listing"]["price"] == 40.0


# ------------------------------------------------------------ edges

def test_naming_no_listings_is_refused(seller):
    client, _dbmod, uid = seller
    r = _press(client, [])
    assert r.status_code == 400
    # ...and the refusal does not leave a reservation behind it.
    assert uid not in main._REPRICE_JOBS


def test_an_unbounded_selection_is_refused_rather_than_run(seller):
    """The read is by id, so the SIZE of the ask reaches the database. An
    unbounded body is an unbounded `IN (...)`, which is a way to make one
    request expensive for everybody."""
    client, _dbmod, uid = seller
    r = _press(client, [f"id-{i}" for i in range(5000)])
    assert r.status_code == 400, r.text
    assert "too many" in r.json()["detail"].lower()
    assert uid not in main._REPRICE_JOBS


def test_a_selection_the_screen_can_make_is_accepted(seller, monkeypatch):
    """The bound sits above what a page of ticks can hold, or it refuses
    ordinary work."""
    client, dbmod, uid = seller
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda name: _AcceptingEbay(dbmod, uid))
    r = _press(client, [f"id-{i}" for i in range(main.BULK_SELECT_CAP)])
    assert r.status_code == 200, r.text
    _finish(client, r.json()["job_id"])


@pytest.mark.parametrize("percent", [0, -5, 90, "lots", None])
def test_a_percentage_the_editor_would_refuse_is_refused(seller, percent):
    client, _dbmod, _uid = seller
    assert _press(client, ["P1"], percent).status_code == 400


def test_it_needs_ebay(seller, monkeypatch):
    client, _dbmod, _uid = seller
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: None)
    assert _press(client, ["P1"]).status_code == 400


def test_it_needs_a_seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    assert TestClient(main.app).post(
        "/api/ebay/lower-prices",
        json={"percent": 10, "listing_ids": ["P1"]}).status_code == 401


def test_a_run_a_restart_cut_short_says_what_it_was_doing():
    msg = jobstore.interrupted_message(
        {"kind": "reprice", "total_items": 41, "current": 12})
    assert "lowering your prices" in msg
    assert "12 of 41" in msg
    assert "tick the rest" in msg

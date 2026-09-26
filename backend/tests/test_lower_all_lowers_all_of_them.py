""""Lower all…" lowers all of them.

Reported as: *only lowers price for one item despite clicking "lower all".*

The group read "Lower prices · 41". The panel under its button offered
"Lower 1 price by 20%", and it meant it: the button handed the server the
rows the dashboard happened to be holding, and the server repriced at most
BULK_PRICE_CAP of them per request — a cap set to 1 in the environment it was
pressed in. One price per press, under a verb that says "all".

"Enrich all" had exactly this shape and was reported for it first (see
test_the_whole_list_goes_in_one_press). The fix is the same one: the press
names no ids and has no cap. The server works the group out from the same
ranking the dashboard renders and runs it as a job the client polls, so the
number on the badge is the number that comes down.

What it must NOT do is cut twice. A second press while the first is running
joins that run rather than starting another over the same listings — 20% off
and then 20% off that is a price nobody asked for.
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

# Plenty of lookers and nobody watching: the traffic rule that puts a listing
# in "Lower prices", and one a price cut cannot move (views are cumulative).
LOOKERS = {"views": 40, "watchers": 0}


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "t"})
    # The "Send offers" rule asks eBay who it will carry an offer for once a
    # listing has watchers. Not what this is about, and never the network.
    monkeypatch.setattr(main.ebay_offers, "eligible_cached", lambda creds: None)
    main._REPRICE_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "lowerall@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("lowerall@example.com")["id"]
    return client, dbmod, uid


def _stock(dbmod, uid: str, ids) -> None:
    for rid in ids:
        assert dbmod.upsert_listing(
            rid, {"title": f"Item {rid}", "price": 40.0,
                  "images": ["1.jpg", "2.jpg", "3.jpg"],
                  "ebay_listing_id": f"11{rid}"},
            status="published", user_id=uid)


def _metrics(monkeypatch, by_id: dict) -> None:
    monkeypatch.setattr(main, "_metrics_by_record_id",
                        lambda creds, items, status=None, fresh=False: by_id)


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
    raise AssertionError(f"lower-all job {job_id} never finished")


def _price_group(client) -> int:
    return client.get("/api/insights").json()["group_totals"].get("lower_price", 0)


# ------------------------------------------------------------ the report

def test_one_press_lowers_every_price_even_with_a_cap_of_one(seller, monkeypatch):
    """The screenshot, in miniature: a cap of 1, a group of five, one press —
    and five prices down, not one."""
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "BULK_PRICE_CAP", 1)
    ids = [f"P{i}" for i in range(5)]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: LOOKERS for rid in ids})
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)
    assert _price_group(client) == 5, "the group has to be offered first"

    started = client.post("/api/ebay/lower-all", json={"percent": 20})
    assert started.status_code == 200, started.text
    body = started.json()
    assert (body["total"], body["deferred"]) == (5, 0)

    result = _finish(client, body["job_id"])
    assert result["changed"] == 5
    assert result["deferred"] == 0
    assert result["percent"] == 20
    assert sorted(ebay.revised) == ids
    cut = bulk_actions.lower_price(40.0, 20)
    for rid in ids:
        assert dbmod.get_listing(rid)["listing"]["price"] == cut
    assert _price_group(client) == 0, "the group the seller pressed is gone"


def test_it_reaches_past_the_rows_the_dashboard_was_sent(seller, monkeypatch):
    """/api/insights ships a capped slice of each group. The press is for the
    badge, so a group bigger than that slice comes down whole."""
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "INSIGHTS_GROUP_CAP", 2)
    ids = [f"R{i}" for i in range(4)]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: LOOKERS for rid in ids})
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    insights = client.get("/api/insights").json()
    shipped = [r for r in insights["recommendations"] if r["type"] == "lower_price"]
    assert len(shipped) == 2 and insights["group_totals"]["lower_price"] == 4

    body = client.post("/api/ebay/lower-all", json={"percent": 10}).json()
    assert _finish(client, body["job_id"])["changed"] == 4


def test_nothing_outside_the_group_is_touched(seller, monkeypatch):
    """Scope is still never implicit — it is just worked out here instead of
    being sent. A listing somebody is watching belongs to "Send offers", and
    one nobody has looked at is not stale yet; neither price moves."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["stale", "watched", "quiet"])
    _metrics(monkeypatch, {"stale": LOOKERS,
                           "watched": {"views": 40, "watchers": 3}})
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    body = client.post("/api/ebay/lower-all", json={"percent": 10}).json()
    assert body["total"] == 1
    _finish(client, body["job_id"])

    assert ebay.revised == ["stale"]
    assert dbmod.get_listing("watched")["listing"]["price"] == 40.0
    assert dbmod.get_listing("quiet")["listing"]["price"] == 40.0


# ------------------------------------------------------------ and only once

def test_a_second_press_joins_the_run_instead_of_cutting_twice(seller, monkeypatch):
    client, dbmod, uid = seller
    ids = ["D1", "D2"]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: LOOKERS for rid in ids})
    ebay = _HeldEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    first = client.post("/api/ebay/lower-all", json={"percent": 20}).json()
    assert ebay.entered.wait(10), "the run never reached eBay"
    again = client.post("/api/ebay/lower-all", json={"percent": 20}).json()
    assert again["job_id"] == first["job_id"]

    ebay.release.set()
    _finish(client, first["job_id"])
    assert sorted(ebay.revised) == ids, "each listing is revised exactly once"
    cut = bulk_actions.lower_price(40.0, 20)
    for rid in ids:
        assert dbmod.get_listing(rid)["listing"]["price"] == cut


def test_a_listing_that_sells_mid_run_is_skipped(seller, monkeypatch):
    """The set is fixed when the button is pressed, and a whole group takes
    minutes. Each listing is judged on what it is when its turn comes."""
    client, dbmod, uid = seller
    ids = ["S1", "S2", "S3"]
    _stock(dbmod, uid, ids)
    _metrics(monkeypatch, {rid: LOOKERS for rid in ids})
    ebay = _HeldEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    body = client.post("/api/ebay/lower-all", json={"percent": 10}).json()
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

def test_asking_with_nothing_to_lower_is_refused(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["new"])
    _metrics(monkeypatch, {})
    r = client.post("/api/ebay/lower-all", json={"percent": 10})
    assert r.status_code == 400
    # ...and the refusal does not leave a reservation behind it.
    assert uid not in main._REPRICE_JOBS


@pytest.mark.parametrize("percent", [0, -5, 90, "lots", None])
def test_a_percentage_it_would_refuse_one_at_a_time_is_refused(seller, percent):
    client, _dbmod, _uid = seller
    assert client.post("/api/ebay/lower-all",
                       json={"percent": percent}).status_code == 400


def test_it_needs_ebay(seller, monkeypatch):
    client, _dbmod, _uid = seller
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: None)
    assert client.post("/api/ebay/lower-all",
                       json={"percent": 10}).status_code == 400


def test_it_needs_a_seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    assert TestClient(main.app).post(
        "/api/ebay/lower-all", json={"percent": 10}).status_code == 401


def test_the_dashboard_is_not_told_a_cap_the_button_no_longer_has(seller, monkeypatch):
    """The cap travels to the dashboard so the panel can say what one press
    reaches. This press reaches the whole group, so there is no cap to send —
    and an absent one reads there as "all of it"."""
    client, _dbmod, _uid = seller
    _metrics(monkeypatch, {})
    assert "lower_price" not in client.get("/api/insights").json()["bulk_caps"]


def test_a_run_a_restart_cut_short_says_what_it_was_doing():
    msg = jobstore.interrupted_message(
        {"kind": "reprice", "total_items": 41, "current": 12})
    assert "lowering your prices" in msg
    assert "12 of 41" in msg

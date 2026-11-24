""""End listings" ends the ticked ones — all of them, only them, once.

The Manage tab could end one listing from its card, or end-and-relist a
ticked batch, but not simply take a batch down: a seller clearing out a
season's stock had to open the menu on every card. The bulk ending is the
card's End, run over the ticks: every listing is settled exactly as the
single End settles it (`main._settle_ending`) — kept under Inactive for the
grace period, removed at once if it was a mirror the store sync made, or
archived as a sale if ending discovers it had already sold.

And it is a job like the other bulk runs: ids read with ownership enforced,
never widened, one run per account, a second press joining the first.
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
from backend.services import jobstore


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "t",
                                         "ebay_username": "seller1"})
    monkeypatch.setattr(main.inventory_mirror, "on_ebay_finished",
                        lambda *a, **k: None)
    main._END_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "endticked@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("endticked@example.com")["id"]
    return client, dbmod, uid


def _stock(dbmod, uid: str, ids) -> None:
    for rid in ids:
        assert dbmod.upsert_listing(
            rid, {"title": f"Item {rid}", "price": 40.0,
                  "images": ["1.jpg"], "ebay_listing_id": f"11{rid}"},
            status="published", user_id=uid)


class _Ending:
    """EndItem, recorded. `sold` names item ids eBay says already sold."""

    def __init__(self, sold=(), hold=False):
        self.ended: list[str] = []
        self.sold = set(sold)
        self.entered, self.release = threading.Event(), threading.Event()
        self.hold = hold

    def __call__(self, token, listing):
        if self.hold:
            self.entered.set()
            assert self.release.wait(10), "the test never let the ending go"
        if listing.ebay_listing_id in self.sold:
            return {"ended": False, "not_live": True, "status": "sold",
                    "message": "This one already sold on eBay."}
        self.ended.append(listing.ebay_listing_id)
        return {"ended": True, "listing_id": listing.ebay_listing_id}


def _finish(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError(f"end-listings job {job_id} never finished")


def _press(client, ids):
    return client.post("/api/ebay/end-listings", json={"listing_ids": ids})


def test_one_press_ends_every_ticked_listing(seller, monkeypatch):
    client, dbmod, uid = seller
    ids = ["E1", "E2", "E3"]
    _stock(dbmod, uid, ids)
    ending = _Ending()
    monkeypatch.setattr(main.listing_sync, "end", ending)

    body = _press(client, ids).json()
    assert body["total"] == 3
    result = _finish(client, body["job_id"])

    assert (result["changed"], result["skipped"], result["failed"]) == (3, 0, 0)
    assert sorted(ending.ended) == ["11E1", "11E2", "11E3"]
    for rid in ids:
        # The seller's own listing is kept under Inactive, the way the card's
        # End keeps it.
        assert dbmod.get_listing(rid)["status"] == "ended"


def test_nothing_outside_the_ticks_is_ended(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["ticked", "untouched"])
    ending = _Ending()
    monkeypatch.setattr(main.listing_sync, "end", ending)

    _finish(client, _press(client, ["ticked"]).json()["job_id"])
    assert ending.ended == ["11ticked"]
    assert dbmod.get_listing("untouched")["status"] == "published"


def test_a_listing_that_had_already_sold_is_archived_not_failed(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["gone", "here"])
    monkeypatch.setattr(main.listing_sync, "end", _Ending(sold={"11gone"}))
    monkeypatch.setattr(main.listing_sync, "recent_sales", lambda token: {})
    monkeypatch.setattr(main.notifications, "notify_sold", lambda *a, **k: None)

    result = _finish(client, _press(client, ["gone", "here"]).json()["job_id"])
    assert (result["changed"], result["skipped"], result["failed"]) == (1, 1, 0)
    skipped = result["results"]["skipped"][0]
    assert skipped["listing_id"] == "gone"
    assert "already sold" in skipped["message"]
    assert skipped["needs_you"] is False
    assert dbmod.get_listing("gone")["status"] == "sold"


def test_a_listing_that_is_not_live_is_skipped(seller, monkeypatch):
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("d", {"title": "t", "price": 1.0},
                                status="draft", user_id=uid)
    ending = _Ending()
    monkeypatch.setattr(main.listing_sync, "end", ending)

    result = _finish(client, _press(client, ["d"]).json()["job_id"])
    assert result["skipped"] == 1
    assert ending.ended == []


def test_another_sellers_listing_is_reported_and_never_ended(seller, monkeypatch):
    client, dbmod, uid = seller
    other = dbmod.create_user("other-end", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", {"title": "t", "price": 40.0,
                                           "ebay_listing_id": "99"},
                                status="published", user_id=other["id"])
    _stock(dbmod, uid, ["mine"])
    ending = _Ending()
    monkeypatch.setattr(main.listing_sync, "end", ending)

    result = _finish(client, _press(client, ["mine", "theirs"]).json()["job_id"])
    assert ending.ended == ["11mine"]
    skipped = result["results"]["skipped"]
    assert [r["listing_id"] for r in skipped] == ["theirs"]
    assert skipped[0]["message"] == "Listing not found."
    assert dbmod.get_listing("theirs")["status"] == "published"


def test_an_ebay_refusal_fails_that_one_and_the_run_goes_on(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["bad", "good"])

    def _end(token, listing):
        if listing.ebay_listing_id == "11bad":
            raise ValueError("eBay said no.")
        return {"ended": True}
    monkeypatch.setattr(main.listing_sync, "end", _end)

    result = _finish(client, _press(client, ["bad", "good"]).json()["job_id"])
    assert (result["changed"], result["failed"]) == (1, 1)
    assert result["results"]["failed"][0]["message"] == "eBay said no."
    assert dbmod.get_listing("bad")["status"] == "published"


def test_a_second_press_joins_the_run(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["J1", "J2"])
    ending = _Ending(hold=True)
    monkeypatch.setattr(main.listing_sync, "end", ending)

    first = _press(client, ["J1", "J2"]).json()
    assert ending.entered.wait(10)
    again = _press(client, ["J1", "J2"]).json()
    assert again["job_id"] == first["job_id"] and again["joined"] is True
    ending.release.set()
    _finish(client, first["job_id"])
    assert sorted(ending.ended) == ["11J1", "11J2"], "each is ended exactly once"


def test_naming_nothing_is_refused_and_holds_no_slot(seller):
    client, _dbmod, uid = seller
    assert _press(client, []).status_code == 400
    assert uid not in main._END_JOBS


def test_it_needs_ebay(seller, monkeypatch):
    client, _dbmod, _uid = seller
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: None)
    assert _press(client, ["E1"]).status_code == 400


def test_a_run_a_restart_cut_short_says_what_it_was_doing():
    msg = jobstore.interrupted_message(
        {"kind": "end-listings", "total_items": 9, "current": 4})
    assert "ending your listings" in msg and "4 of 9" in msg


def test_an_end_and_relist_a_restart_cut_short_has_its_own_words():
    """It used to fall through to the photo batch's "check Drafts for what it
    finished" — wrong about where the work is."""
    msg = jobstore.interrupted_message(
        {"kind": "end-and-relist", "total_items": 6, "current": 2})
    assert "ending and relisting" in msg and "2 of 6" in msg
    assert "Active" in msg

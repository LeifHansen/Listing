"""`price_lowered_at`: when a listing was last marked down, recorded on it.

It used to be the fact that silenced the dashboard's "Lower prices" nudge,
which was computed from signals a price cut does not move (the listing's age,
eBay's cumulative view count) and so came straight back after the seller took
its advice. That nudge is gone — removed on request, see services/recommender
— and the stamp stays as what it always also was: the record of the last
markdown, kept by every write path.

It is derived on every write from the price already stored, never taken from
the payload, so no tab and no forged body can either erase it or mint one.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.marketplaces import state as marketplace_state
from backend.services import recommender


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=days)).isoformat(timespec="seconds")


# ------------------------------------------------------------------- the stamp

def test_a_cut_is_stamped_and_a_rise_is_not():
    """A seller who puts a price up has not marked the listing down."""
    stored = {"price": 40.0, "price_lowered_at": ""}
    assert recommender.price_drop_stamp(stored, 29.99)
    assert recommender.price_drop_stamp(stored, 49.99) == ""
    assert recommender.price_drop_stamp(stored, 40.0) == ""


def test_a_save_that_leaves_the_price_alone_keeps_the_stamp():
    """Most saves are a title fix or a photo swap. None of them is a fresh cut
    and none of them may lose the one already recorded."""
    held = _ago(3)
    stored = {"price": 40.0, "price_lowered_at": held}
    assert recommender.price_drop_stamp(stored, 40.0) == held


def test_the_stamp_is_read_off_the_stored_record_not_the_payload():
    """It is not a client's to send. A second tab holding a copy from before
    this morning's markdown would otherwise blank it, and a body could mint
    one. Neither is even consulted: the only inputs are the stored record and
    the new price."""
    held = _ago(3)
    stored = {"price": 40.0, "price_lowered_at": held}
    assert recommender.price_drop_stamp(stored, 40.0) == held      # no erasing
    stamped = recommender.price_drop_stamp({"price": 40.0}, 29.99)  # no minting
    assert stamped and stamped != _ago(-500)


def test_a_record_with_no_usable_price_is_not_a_cut():
    """A draft that has never been priced, or a blank where a number should
    be. "Lower than nothing" is not a markdown."""
    assert recommender.price_drop_stamp({}, 29.99) == ""
    assert recommender.price_drop_stamp({"price": None}, 29.99) == ""
    assert recommender.price_drop_stamp({"price": "forty"}, 29.99) == ""
    assert recommender.price_drop_stamp({"price": 40.0}, None) == ""


def test_the_stamp_is_not_restored_from_the_stored_copy_on_publish():
    """state.SERVER_OWNED_FIELDS means "the STORED value wins over whatever
    arrived" — which is right for an item id and fatal here. Every path that
    lowers a price does it by loading the stored record, changing the price
    and publishing; under that rule the publish would hand the listing back
    the OLD stamp it was just moved past, and the second markdown on any
    listing would go unrecorded.

    The field is protected a stronger way instead: it is derived from the
    stored price on every write (price_drop_stamp) and the payload's copy is
    never read, so there is nothing for this list to defend.
    """
    assert "price_lowered_at" not in marketplace_state.SERVER_OWNED_FIELDS


# -------------------------------------------------------- every way a price falls

@pytest.fixture()
def seller(dbmod, monkeypatch):
    """A signed-in seller with one live listing and eBay saying yes."""
    pytest.importorskip("fastapi")
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from fastapi.testclient import TestClient

    from backend import main, ratelimit

    monkeypatch.setattr(main, "db", dbmod)
    main._REPRICE_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "prices@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("prices@example.com")["id"]
    assert dbmod.upsert_listing(
        "L1", {"title": "Vintage bowling trophy", "price": 40.0,
               "images": ["1.jpg", "2.jpg", "3.jpg"],
               "ebay_listing_id": "1234567890"},
        status="published", user_id=uid)

    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "t"})

    class _AcceptingEbay:
        """eBay accepting the revise, and storing the result the way the real
        provider does — which is the step that has to carry the stamp."""

        def publish(self, ctx, creds):
            from backend.marketplaces.base import PublishOutcome
            dbmod.upsert_listing(ctx.session_id, ctx.listing.model_dump(),
                                 status="published", user_id=uid)
            return PublishOutcome(ok=True, message="Revised.", status="published")

    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())
    return client, dbmod


def _finish(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError("the price drop never finished")


def test_the_manage_tabs_bulk_cut_records_the_markdown(seller):
    client, dbmod = seller
    r = client.post("/api/ebay/lower-prices",
                    json={"percent": 10, "listing_ids": ["L1"]})
    assert r.status_code == 200, r.text
    assert _finish(client, r.json()["job_id"])["changed"] == 1

    stored = dbmod.get_listing("L1")["listing"]
    assert stored["price"] < 40.0, "the cut has to have landed"
    assert stored["price_lowered_at"], "the cut has to have been recorded"


def test_a_price_edit_from_a_card_counts_too(seller):
    client, dbmod = seller
    r = client.patch("/api/listings/L1", json={"price": 29.99})
    assert r.status_code == 200, r.text
    assert dbmod.get_listing("L1")["listing"]["price_lowered_at"]


def test_a_price_edit_saved_from_the_editor_counts_too(seller):
    client, dbmod = seller
    listing = dict(dbmod.get_listing("L1")["listing"])
    listing["price"] = 24.99
    r = client.post("/api/save/L1", json=listing)
    assert r.status_code == 200, r.text
    assert dbmod.get_listing("L1")["listing"]["price_lowered_at"]


def test_a_later_save_does_not_lose_the_stamp(seller):
    """The save path derives the stamp from the STORED record, so a save that
    does not touch the price carries the existing one forward — including one
    sent by a tab that has never heard of it."""
    client, dbmod = seller
    assert client.patch("/api/listings/L1", json={"price": 29.99}).status_code == 200
    stamped = dbmod.get_listing("L1")["listing"]["price_lowered_at"]
    assert stamped

    stale = dict(dbmod.get_listing("L1")["listing"])
    stale["title"] = "Vintage bowling trophy, 1962"
    stale["price_lowered_at"] = ""          # the tab that loaded this morning
    assert client.post("/api/save/L1", json=stale).status_code == 200

    assert dbmod.get_listing("L1")["listing"]["price_lowered_at"] == stamped

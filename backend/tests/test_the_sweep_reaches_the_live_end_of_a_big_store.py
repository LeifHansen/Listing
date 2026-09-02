"""Reads that only ever want live listings ask for live listings.

Three routes read the seller's newest `LIST_CAP` records and immediately throw
away everything that is not live: the store sweep, the duplicate advisory and
the promote-all pass. On a store bigger than that page the arithmetic is
unkind. A seller with 10,000 records whose newest 3,000 happen to be mostly
drafts has their OLDER live listings fall off the end -- and they are the ones
a sweep is for. Those listings are never checked, so a sale or an ending on
eBay is never noticed here, for as long as the store stays that shape.

This is the last of the things `242b914` left open under P1-10: "the sync and
bulk routes read the same capped list server-side, so a store past the cap has
listings that are never swept." The bulk half is closed (`30e7c18`,
`67cc3a3`). This is the sweep half, and it needs no data model -- a `WHERE
status IN (...)` moves the boundary from "the newest 3,000 records" to "the
first 3,000 LIVE ones", which for any real store is all of them.

`capped` keeps its meaning and gets a truer one: it now says the seller has
more LIVE listings than one pass can hold, rather than more records of any
kind.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "sweep@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("sweep@example.com")["id"]
    return client, dbmod, uid


def _put(dbmod, uid, lid, status, **data):
    assert dbmod.upsert_listing(lid, {"title": lid, **data}, status=status,
                                user_id=uid)


def test_a_status_filter_reaches_past_a_page_of_drafts(seller):
    """The listing that matters is the OLDEST one, behind a page of drafts."""
    _client, dbmod, uid = seller
    _put(dbmod, uid, "old-live", "published", ebay_listing_id="1")
    for i in range(6):
        _put(dbmod, uid, f"draft-{i}", "draft")

    # The unfiltered page of three sees only drafts -- the shape that hid a
    # seller's live listings from every sweep in the app.
    page = dbmod.list_listings(limit=3, user_id=uid)
    assert not [r for r in page if r["status"] == "published"]

    live = dbmod.list_listings(limit=3, user_id=uid,
                               statuses=("published", "live"))
    assert [r["id"] for r in live] == ["old-live"]


def test_the_filter_takes_every_live_name(seller):
    """'published' and 'live' are the same state under two names, and a filter
    that knew only one of them would silently halve the sweep."""
    _client, dbmod, uid = seller
    _put(dbmod, uid, "a", "published")
    _put(dbmod, uid, "b", "live")
    _put(dbmod, uid, "c", "sold")
    got = dbmod.list_listings(limit=50, user_id=uid,
                              statuses=("published", "live"))
    assert sorted(r["id"] for r in got) == ["a", "b"]


def test_a_filtered_read_that_failed_still_raises(seller, monkeypatch):
    """Narrowing the read must not quietly reintroduce the empty-store lie."""
    _client, dbmod, uid = seller

    class Broken:
        def __enter__(self):
            raise RuntimeError("connection reset")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dbmod, "Session", lambda *a, **k: Broken())
    with pytest.raises(errors.StorageUnavailable):
        dbmod.list_listings(limit=5, user_id=uid, statuses=("published",))


def test_the_sweep_asks_for_live_listings(seller, monkeypatch):
    """The route, not just the read: a sweep over a store of drafts has to
    still find the live listing hiding behind them."""
    client, dbmod, uid = seller
    _put(dbmod, uid, "old-live", "published", ebay_listing_id="1")
    for i in range(6):
        _put(dbmod, uid, f"draft-{i}", "draft")
    monkeypatch.setattr(main, "LIST_CAP", 3, raising=False)
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "t",
                                         "ebay_username": "seller"})

    seen: dict = {}

    def _reconcile(token, user_id, records, account=None):
        seen["ids"] = [r["id"] for r in records]
        return 0, set()

    monkeypatch.setattr(main.listing_sync, "reconcile_recent", _reconcile)
    monkeypatch.setattr(main.sync_guard, "sweep_due", lambda uid_, force: False)

    r = client.post("/api/ebay/sync-listings", json={})
    assert r.status_code == 200, r.text
    assert seen.get("ids") == ["old-live"], (
        "the sweep never saw the seller's live listing")


def test_the_duplicate_advisory_asks_for_live_listings_too(seller, monkeypatch):
    """Same read, same page, same throw-away: `duplicates.find` skips anything
    not live on its first line.

    Recorded rather than asserted inside the double. That route wraps its whole
    body in `except Exception` -- advisory features must never take the
    Dashboard down -- so a failing assertion in there is swallowed and the test
    passes on the bug. Found by writing it the obvious way first.
    """
    client, dbmod, uid = seller
    asked: list = []

    def _spy(limit=50, user_id=None, statuses=None):
        asked.append(statuses)
        return []

    monkeypatch.setattr(dbmod, "list_listings", _spy)
    assert client.get("/api/ebay/duplicates").json()["groups"] == []
    assert asked == [("published", "live")], (
        "the duplicate scan read the whole store to keep the live rows")


def test_the_metrics_panel_asks_for_live_listings_too(seller, monkeypatch):
    """The fourth: `_live_ebay_id_map` drops everything not live, so the whole
    page was read to keep the live rows -- and on a big store the live rows
    past the cap got no numbers at all."""
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "t"})
    asked: list = []

    def _spy(limit=50, user_id=None, statuses=None):
        asked.append(statuses)
        return []

    monkeypatch.setattr(dbmod, "list_listings_best_effort", _spy)
    assert client.get("/api/ebay/listing-metrics").status_code == 200
    assert asked == [("published", "live")]


def test_promote_all_asks_for_live_listings_too(seller, monkeypatch):
    """And the third: it filters to live-and-unpromoted on the next line."""
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "t"})
    asked: list = []

    def _spy(limit=50, user_id=None, statuses=None):
        asked.append(statuses)
        return []

    monkeypatch.setattr(dbmod, "list_listings", _spy)
    assert client.post("/api/ebay/promote-all", json={}).status_code == 200
    assert asked == [("published", "live")]

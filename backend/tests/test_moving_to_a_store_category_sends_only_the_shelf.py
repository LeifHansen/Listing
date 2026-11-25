""""Move to store category…" puts the ticked listings on one shelf — and the
revise carries the shelf and nothing else.

A seller tidying their eBay Store's left-hand nav used to open every listing
in the editor to change one dropdown. The bulk move is one revise per listing,
built the way the bulk price cut is (`main._lower_one`): only the shelf is
marked, so item specifics an earlier run left pending are not dragged along
(and refused, and the move with them); other pending edits stay marked for
the save that is about them; a refused revise puts the record back as it
was. The shelf must be one the account's Store actually has, and it can never
be "the top level": a revise only sends <Storefront> for a real shelf.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.marketplaces.base import PublishOutcome
from backend.services import ebay_trading, jobstore

SHELVES = [{"id": "11", "name": "Vintage Tees", "path": "Vintage Tees", "level": 1},
           {"id": "12", "name": "Beanies", "path": "Beanies", "level": 1}]


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "t"})
    monkeypatch.setattr(main, "_store_categories",
                        lambda uid, token, refresh=False: SHELVES)
    main._STORE_CATEGORY_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "shelfmove@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("shelfmove@example.com")["id"]
    return client, dbmod, uid


def _stock(dbmod, uid, ids, **extra):
    for rid in ids:
        assert dbmod.upsert_listing(
            rid, {"title": f"Item {rid}", "price": 40.0, "images": ["1.jpg"],
                  "ebay_listing_id": f"11{rid}", "store_category_id": "12",
                  "store_category_name": "Beanies", **extra},
            status="published", user_id=uid)


class _Ebay:
    def __init__(self, dbmod, uid, refuse=()):
        self.db, self.uid, self.refuse = dbmod, uid, set(refuse)
        self.sent: list[tuple[str, list]] = []

    def publish(self, ctx, creds):
        self.sent.append((ctx.session_id, sorted(ctx.listing.dirty_fields)))
        if ctx.session_id in self.refuse:
            # A refused revise writes its attempt, as the real provider does.
            self.db.upsert_listing(ctx.session_id, ctx.listing.model_dump(),
                                   status="published", user_id=self.uid)
            return PublishOutcome(ok=False, message="eBay said no.", status="published")
        listing = ctx.listing.model_copy()
        listing.clear_dirty()
        self.db.upsert_listing(ctx.session_id, listing.model_dump(),
                               status="published", user_id=self.uid)
        return PublishOutcome(ok=True, message="Revised.", status="published")


def _finish(client, job_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError("store-category job never finished")


def _press(client, ids, cat="11"):
    return client.post("/api/ebay/move-store-category",
                       json={"store_category_id": cat, "listing_ids": ids})


def test_the_ticked_listings_land_on_the_shelf(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["A", "B"])
    ebay = _Ebay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    result = _finish(client, _press(client, ["A", "B"]).json()["job_id"])
    assert (result["changed"], result["failed"]) == (2, 0)
    assert result["store_category_name"] == "Vintage Tees"
    for rid in ("A", "B"):
        stored = dbmod.get_listing(rid)["listing"]
        assert stored["store_category_id"] == "11"
        assert stored["store_category_name"] == "Vintage Tees"


def test_the_revise_is_marked_for_the_shelf_alone(seller, monkeypatch):
    """Item specifics an "Enrich all" left pending must not ride along on a
    shelf move — eBay refusing them would refuse the move too. They stay
    marked for the save that is about them."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["A"], dirty_fields=["item_specifics"])
    ebay = _Ebay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    _finish(client, _press(client, ["A"]).json()["job_id"])
    assert ebay.sent == [("A", ["store_category_id"])]
    assert dbmod.get_listing("A")["listing"]["dirty_fields"] == ["item_specifics"]


def test_a_refused_move_leaves_the_listing_where_it_was(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["A", "B"])
    ebay = _Ebay(dbmod, uid, refuse={"A"})
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    result = _finish(client, _press(client, ["A", "B"]).json()["job_id"])
    assert (result["changed"], result["failed"]) == (1, 1)
    assert dbmod.get_listing("A")["listing"]["store_category_id"] == "12"
    assert dbmod.get_listing("B")["listing"]["store_category_id"] == "11"


def test_a_listing_already_on_the_shelf_is_left_alone(seller, monkeypatch):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["A"])
    ebay = _Ebay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    result = _finish(client, _press(client, ["A"], cat="12").json()["job_id"])
    assert result["skipped"] == 1
    assert ebay.sent == [], "no eBay call for a move to where it already is"


def test_a_shelf_the_store_does_not_have_is_refused(seller):
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["A"])
    r = _press(client, ["A"], cat="999")
    assert r.status_code == 400
    assert "isn't in your eBay Store" in r.json()["detail"]
    assert uid not in main._STORE_CATEGORY_JOBS


def test_the_top_level_is_not_a_shelf_to_move_to(seller):
    client, _dbmod, _uid = seller
    assert _press(client, ["A"], cat="").status_code == 400
    assert _press(client, ["A"], cat="0").status_code == 400


def test_an_account_without_a_store_is_told_so(seller, monkeypatch):
    client, _dbmod, _uid = seller

    def _no_store(uid, token, refresh=False):
        raise ebay_trading.NoStore("no store")
    monkeypatch.setattr(main, "_store_categories", _no_store)
    r = _press(client, ["A"])
    assert r.status_code == 400
    assert "doesn't have a Store" in r.json()["detail"]


def test_another_sellers_listing_is_never_moved(seller, monkeypatch):
    client, dbmod, uid = seller
    other = dbmod.create_user("other-shelf", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", {"title": "t", "store_category_id": "12",
                                           "ebay_listing_id": "99"},
                                status="published", user_id=other["id"])
    ebay = _Ebay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    result = _finish(client, _press(client, ["theirs"]).json()["job_id"])
    assert result["skipped"] == 1 and ebay.sent == []
    assert dbmod.get_listing("theirs")["listing"]["store_category_id"] == "12"


def test_a_run_a_restart_cut_short_says_what_it_was_doing():
    msg = jobstore.interrupted_message(
        {"kind": "store-category", "total_items": 5, "current": 2})
    assert "moving your listings" in msg and "2 of 5" in msg

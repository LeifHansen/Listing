"""A saved listing view is a question the seller named, not a list of ids.

The Sell screen can now be cut down — format, condition, price, brand,
category, photos, age, unfinished — and the cut kept under a name. The name
has to follow the seller to the phone they list from, so the views ride the
account (the same per-user `prefs` JSON the new-listing defaults live in)
rather than the browser that made them.

Two things this endpoint is responsible for, and both are about what it
*stores*, not what it renders.

It stores the QUESTION. Persisting the listings a view matched would make
"Needs photos" go stale the first time it was used — the whole point is that
it empties itself as the photos get taken. Nothing here writes a listing id,
and there is a test that reads the stored blob to say so.

And it stores something BOUNDED. This writes into the users row, from a
client, so a view is capped: a name, a tab, a flat filter object, and a
ceiling on how many. What it deliberately does NOT do is police the filter
vocabulary — that lives in the frontend (lib/listingFilters), which validates
on the way in and again on the way out, and a second copy of the same list in
Python is a copy that goes out of step the first time a filter is added.

The reserved key is unreachable from POST /api/prefs, whose whitelist is
scalars only. That matters: without it, a seller saving a package weight in
Settings could overwrite every view they had.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main
from backend import ratelimit


VIEW = {
    "id": "v1",
    "name": "Nike under 20",
    "tab": "active",
    "filters": {"brand": "Nike", "priceMax": "20", "format": ["FIXED_PRICE"],
                "needsWork": False},
    "created_at": "2026-09-19T12:00:00Z",
}


@pytest.fixture()
def seller(dbmod):
    """One signed-in seller, through the real app and a real schema."""
    db = dbmod
    # Signups are rate limited per client IP; run after enough of the suite in
    # one process and the fixture 429s instead of the assertions running.
    ratelimit.reset()
    client = TestClient(main.app)
    r = client.post("/api/auth/signup",
                    json={"email": "views@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    client.uid = db.get_user_by_email("views@example.com")["id"]
    client.db = db
    return client


def _put(client, views):
    r = client.put("/api/listing-views", json={"views": views})
    assert r.status_code == 200, r.text
    return r.json()["views"]


def test_a_saved_view_comes_back_as_it_went_in(seller):
    saved = _put(seller, [VIEW])
    assert saved == seller.get("/api/listing-views").json()["views"]
    assert saved[0]["name"] == "Nike under 20"
    assert saved[0]["tab"] == "active"
    assert saved[0]["filters"]["brand"] == "Nike"


def test_a_view_stores_the_question_and_not_the_listings(seller):
    """The one thing a saved list must never do is go stale."""
    _put(seller, [VIEW])
    stored = seller.db.saved_listing_views(seller.uid)
    assert set(stored[0]) == {"id", "name", "tab", "filters", "created_at"}
    assert "listings" not in json.dumps(stored)


def test_a_filter_this_release_has_never_heard_of_still_round_trips(seller):
    """The vocabulary is the frontend's, and it is checked there.

    A server that kept only the filters it recognised would silently drop a
    new one from every view a seller saved — from an older client, or from a
    deploy where the two halves are a minute apart — and the view would come
    back meaning something narrower than what they saved.
    """
    view = {**VIEW, "filters": {"somethingNew": ["a", "b"], "brand": "Nike"}}
    saved = _put(seller, [view])
    assert saved[0]["filters"]["somethingNew"] == ["a", "b"]


def test_a_view_cannot_carry_an_unbounded_payload(seller):
    """It is written to the users row by a client, so it is capped."""
    saved = _put(seller, [{**VIEW, "name": "n" * 500,
                           "filters": {"brand": "b" * 4000}}])
    assert len(saved[0]["name"]) <= 40
    assert len(saved[0]["filters"]["brand"]) <= 80


def test_a_nested_filter_is_dropped_rather_than_stored(seller):
    """A filter set is flat. Somewhere to store arbitrary nesting on the
    users row is somewhere for a payload to live."""
    saved = _put(seller, [{**VIEW, "filters": {"brand": "Nike",
                                               "nested": {"deep": [1, 2, 3]}}}])
    assert saved[0]["filters"] == {"brand": "Nike"}


def test_the_strip_is_capped(seller):
    many = [{**VIEW, "id": f"v{i}", "name": f"View {i}"} for i in range(60)]
    assert len(_put(seller, many)) == main._MAX_LISTING_VIEWS


def test_a_view_with_no_name_is_dropped(seller):
    """A pill nobody can read is one nobody can delete."""
    saved = _put(seller, [{**VIEW, "name": "   "}, {**VIEW, "id": "v2",
                                                    "name": "Real one"}])
    assert [v["name"] for v in saved] == ["Real one"]


def test_two_views_cannot_share_an_id(seller):
    """A delete would remove the wrong one."""
    saved = _put(seller, [VIEW, {**VIEW, "name": "Impostor"}])
    assert [v["name"] for v in saved] == ["Nike under 20"]


def test_a_put_replaces_the_whole_strip(seller):
    """Saving and deleting are both edits to a short list the client holds."""
    _put(seller, [VIEW, {**VIEW, "id": "v2", "name": "Second"}])
    assert [v["id"] for v in _put(seller, [VIEW])] == ["v1"]
    assert len(seller.get("/api/listing-views").json()["views"]) == 1


def test_saving_new_listing_defaults_cannot_wipe_the_views(seller):
    """The reserved key is unreachable from the Settings screen's endpoint.

    POST /api/prefs merges into the same JSON column and stores only its own
    scalar whitelist, so a seller saving a package weight must not take their
    saved views with it.
    """
    _put(seller, [VIEW])
    r = seller.post("/api/prefs", json={"package_weight_lb": 2})
    assert r.status_code == 200, r.text
    assert r.json()["prefs"]["package_weight_lb"] == 2
    assert seller.get("/api/listing-views").json()["views"][0]["name"] \
        == "Nike under 20"


def test_the_views_are_the_sellers_own(seller, dbmod):
    """Another account's strip is not this one's."""
    ratelimit.reset()
    other = TestClient(main.app)
    r = other.post("/api/auth/signup",
                   json={"email": "other@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    _put(seller, [VIEW])
    assert other.get("/api/listing-views").json()["views"] == []


def test_a_signed_out_visitor_is_asked_to_log_in(dbmod):
    anon = TestClient(main.app)
    assert anon.get("/api/listing-views").status_code == 401
    assert anon.put("/api/listing-views", json={"views": []}).status_code == 401


def test_junk_where_the_views_should_be_is_an_empty_strip(seller):
    """Not a 500. The client sends the list it holds, and the list it holds
    can only ever be a list."""
    for junk in (None, "nope", 7, [None, "x", {"no": "name"}]):
        r = seller.put("/api/listing-views", json={"views": junk})
        assert r.status_code == 200, r.text
        assert r.json()["views"] == []

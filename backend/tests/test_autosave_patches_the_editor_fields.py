"""The editor saves one field at a time through the same door the cards use.

Autosave (LISTING_REDESIGN.md, Phase 0/1) sends the keys the seller changed
and nothing else, so `PATCH /api/listings/{id}` has to take the editor's
fields -- title, description, item specifics, the package, the cost basis --
on a listing eBay does not have yet. Three rules hold it together:

1. The list is still explicit. The photo set, the video set, the eBay id, the
   publish state and the sync's bookkeeping are never patchable; a client
   echoing a stale copy of those is how a live listing gets duplicated.
2. A listing eBay is showing takes the CARD fields only. An editor edit to a
   live listing has to reach eBay in the same request (quick-edit, Update
   Live Listing), and a background save that only wrote the row would put
   the card and the live copy out of step with nothing saying so.
3. The write happens under the row lock, from the row as it is at that
   moment. The plain read-then-upsert this replaced raced publish: a listing
   that went live between the read and the write landed back as the
   pre-publish copy -- no eBay id, status reset to draft -- and the next
   publish listed it twice.
"""
from __future__ import annotations

import copy

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

STORED = {
    "id": "lst1", "user_id": "u1", "status": "draft",
    "listing": {"title": "Old title", "description": "Old body",
                "price": 30.0, "quantity": 2, "category_id": "111",
                "brand": "Acme",
                "item_specifics": [{"name": "Brand", "value": "Acme"}],
                "etsy": {"taxonomy_id": 5, "who_made": "someone_else"}},
}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from backend import config, db, main

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    state = {"rec": copy.deepcopy(STORED), "row": None, "saved": {},
             "status_arg": "unset"}
    monkeypatch.setattr(db, "get_listing", lambda lid:
                        copy.deepcopy(state["rec"]) if lid == "lst1" else None)
    monkeypatch.setattr(db, "enabled", lambda: True)

    # The locked write. `row` is what the ROW holds at write time, which the
    # race test makes different from what get_listing answered a moment ago.
    def mutate(lid, fn, status=None, user_id=None):
        state["status_arg"] = status
        base = state["row"] if state["row"] is not None else state["rec"]["listing"]
        data = fn(copy.deepcopy(base))
        state["saved"].update(data)
        return data
    monkeypatch.setattr(db, "mutate_listing_data", mutate)
    monkeypatch.setattr(main.deps, "uid", lambda _r: "u1")
    monkeypatch.setattr(main.deps, "assert_session_owner", lambda *a, **k: None)
    return TestClient(main.app), state


def test_a_title_patch_changes_the_title_and_nothing_else(api):
    client, state = api
    resp = client.patch("/api/listings/lst1", json={"title": "New title"})

    assert resp.status_code == 200, resp.text
    saved = state["saved"]
    assert saved["title"] == "New title"
    assert saved["description"] == "Old body"
    assert saved["price"] == 30.0
    assert resp.json()["listing"]["title"] == "New title"


def test_item_specifics_and_etsy_replace_their_whole_value(api):
    """A list and a sub-object are one field each: the client sends the
    whole new value, the way the editor holds it."""
    client, state = api
    resp = client.patch("/api/listings/lst1", json={
        "item_specifics": [{"name": "Brand", "value": "Acme"},
                           {"name": "Color", "value": "Blue"}],
        "etsy": {"taxonomy_id": 9, "who_made": "i_did"},
    })

    assert resp.status_code == 200, resp.text
    names = [s["name"] for s in state["saved"]["item_specifics"]]
    assert names == ["Brand", "Color"]
    assert state["saved"]["etsy"]["taxonomy_id"] == 9
    assert state["saved"]["etsy"]["who_made"] == "i_did"


@pytest.mark.parametrize("field,value", [
    ("images", ["a.jpg"]),
    ("image_urls", ["https://i.ebayimg.com/x.jpg"]),
    ("videos", []),
    ("ebay_listing_id", "999"),
    ("sku", "mine"),
    ("source", "ebay"),
    ("marketplaces", {"ebay": {"listing_id": "999"}}),
    ("missing_info", []),
    ("ai_confidence", "high"),
    ("dirty_fields", []),
    ("remote_shadow", {}),
    ("conflicts", {}),
    ("sold_price", 1.0),
    ("status", "published"),
])
def test_what_the_server_owns_is_never_patchable(api, field, value):
    client, state = api
    resp = client.patch("/api/listings/lst1", json={field: value})

    assert resp.status_code == 400, resp.text
    assert state["saved"] == {}


def test_every_patched_field_is_queued_for_the_next_revise(api):
    client, state = api
    client.patch("/api/listings/lst1",
                 json={"title": "New", "description": "New body", "price": 25})

    assert {"title", "description", "price"} <= set(state["saved"]["dirty_fields"])


# --- a listing eBay already has ---------------------------------------------


@pytest.mark.parametrize("status", ["published", "live", "sold"])
def test_an_editor_field_is_refused_on_a_live_listing(api, status):
    """Not saved quietly: a title the seller believes is saved, on a listing
    whose live copy never heard about it, is the worse outcome."""
    client, state = api
    state["rec"]["status"] = status
    resp = client.patch("/api/listings/lst1", json={"title": "New title"})

    assert resp.status_code == 409, resp.text
    assert "Update Live Listing" in resp.json()["detail"]
    assert state["saved"] == {}


def test_a_card_field_still_saves_on_a_live_listing(api):
    """What the grid has always been allowed to change on a live listing --
    the revise picks it up from dirty_fields."""
    client, state = api
    state["rec"]["status"] = "published"
    resp = client.patch("/api/listings/lst1",
                        json={"fulfillment_policy_id": "FP-new"})

    assert resp.status_code == 200, resp.text
    assert state["saved"]["fulfillment_policy_id"] == "FP-new"


@pytest.mark.parametrize("status", ["draft", "dry_run", "ended", "unlisted"])
def test_an_editor_field_saves_on_a_listing_ebay_does_not_have(api, status):
    client, state = api
    state["rec"]["status"] = status
    resp = client.patch("/api/listings/lst1", json={"title": "New title"})

    assert resp.status_code == 200, resp.text
    assert state["saved"]["title"] == "New title"


# --- the race with publish ----------------------------------------------------


def test_a_publish_that_lands_mid_save_keeps_its_ebay_id(api):
    """get_listing answered "draft, no id" a moment ago; by the time the row
    is locked, publish has written the id and flipped the status. The patch
    must lay its one change over THAT row, and must not touch the status
    column at all."""
    client, state = api
    state["row"] = {**STORED["listing"], "ebay_listing_id": "123",
                    "source": "ebay",
                    "marketplaces": {"ebay": {"listing_id": "123",
                                              "status": "published"}}}
    resp = client.patch("/api/listings/lst1", json={"title": "New title"})

    assert resp.status_code == 200, resp.text
    saved = state["saved"]
    assert saved["title"] == "New title"
    assert saved["ebay_listing_id"] == "123"
    assert saved["source"] == "ebay"
    assert saved["marketplaces"]["ebay"]["listing_id"] == "123"
    assert state["status_arg"] is None, \
        "a patch must never set the status column -- that is how a " \
        "just-published listing got demoted to draft"


def test_a_write_that_did_not_happen_is_a_503_not_a_save(api, monkeypatch):
    from backend import db

    client, state = api
    monkeypatch.setattr(db, "mutate_listing_data", lambda *a, **k: None)
    resp = client.patch("/api/listings/lst1", json={"title": "New title"})

    assert resp.status_code == 503, resp.text
    assert state["saved"] == {}


def test_a_bad_value_is_refused_before_anything_is_locked(api, monkeypatch):
    """The locked helper turns any exception into "not saved", which would
    read as a 503 for what is a 400: the model's reason has to be found on
    the copy already in hand."""
    from backend import db

    client, state = api
    calls = []
    monkeypatch.setattr(db, "mutate_listing_data",
                        lambda *a, **k: calls.append(a) or None)
    resp = client.patch("/api/listings/lst1",
                        json={"package_weight_lb": "heavy"})

    assert resp.status_code == 400, resp.text
    assert calls == []

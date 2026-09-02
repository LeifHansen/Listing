"""Changing one field must not ship a stale copy of all the others.

`POST /api/save/{id}` is a full REPLACE, and it has to be — clearing a
subtitle is done by sending the listing without one. The problem is what got
built on top of it: the drafts strip changes a shipping policy or a category
by spreading the whole listing it happens to be holding and sending that.

The listing it is holding came from the last `/api/listings` load. So a title
fixed in the editor in another tab, a price corrected on a phone, or anything
a background store sync pulled in since is overwritten by the strip's older
copy the moment somebody picks a shipping policy from a card. The strip
refreshes after each of its own saves, which narrows the window to whatever
happened elsewhere — not to nothing.

The same reasoning already produced `PATCH /api/listings/{id}/images/order`:
"a reorder could overwrite a title edit made in another tab with a stale
copy". This is that endpoint for ordinary fields. A patch says what changed
and nothing else, so there is no stale copy to send.

The allowed set is deliberately small: the fields the card controls actually
offer. A patch route that accepts anything is a full replace with extra steps
— the caller can just send every field and reintroduce the bug.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

STORED = {
    "id": "lst1", "user_id": "u1", "status": "draft",
    "listing": {"title": "The newer title someone just fixed",
                "price": 30.0, "quantity": 2,
                "category_id": "111", "fulfillment_policy_id": "FP-old"},
}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from backend import config, db, main

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    saved: dict = {}
    monkeypatch.setattr(db, "get_listing", lambda lid:
                        {**STORED, "listing": dict(STORED["listing"])}
                        if lid == "lst1" else None)
    monkeypatch.setattr(db, "enabled", lambda: True)
    monkeypatch.setattr(db, "upsert_listing",
                        lambda lid, data, **k: saved.update(data) or True)
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    return TestClient(main.app), saved


def test_a_patch_changes_only_what_it_names(api):
    """The finding: this used to arrive as the whole listing, title included,
    from whenever the strip last loaded."""
    client, saved = api
    resp = client.patch("/api/listings/lst1",
                        json={"fulfillment_policy_id": "FP-new"})

    assert resp.status_code == 200, resp.text
    assert saved["fulfillment_policy_id"] == "FP-new"
    assert saved["title"] == "The newer title someone just fixed", \
        "a shipping-policy change overwrote the stored title"
    assert saved["price"] == 30.0


def test_the_patched_field_is_queued_for_ebay(api):
    """A live listing's shipping policy changed here has to actually reach
    eBay on the next revise, and the revise only sends dirty fields."""
    client, saved = api
    client.patch("/api/listings/lst1", json={"category_id": "222"})

    assert "category_id" in saved["dirty_fields"]


def test_the_merged_listing_comes_back(api):
    """So the caller can update its cache from the answer instead of from the
    copy it already had — which is the copy that was stale."""
    client, _ = api
    body = client.patch("/api/listings/lst1",
                        json={"category_id": "222"}).json()

    assert body["listing"]["category_id"] == "222"
    assert body["listing"]["title"] == "The newer title someone just fixed"


def test_a_field_outside_the_allowed_set_is_refused(api):
    """A patch route that accepts anything is a full replace with extra steps:
    the caller sends every field and the lost update is back."""
    client, saved = api
    resp = client.patch("/api/listings/lst1",
                        json={"title": "from a stale tab"})

    assert resp.status_code == 400, resp.text
    assert saved == {}


def test_an_empty_patch_is_refused(api):
    client, saved = api
    assert client.patch("/api/listings/lst1", json={}).status_code == 400
    assert saved == {}


def test_a_stranger_cannot_patch_someone_elses_listing(api, monkeypatch):
    from backend import main

    client, saved = api
    monkeypatch.setattr(main, "_uid", lambda _r: "someone-else")

    resp = client.patch("/api/listings/lst1",
                        json={"category_id": "222"})
    assert resp.status_code == 404, resp.text
    assert saved == {}


def test_a_patch_that_did_not_commit_is_not_reported_as_saved(api, monkeypatch):
    from backend import db

    client, _ = api
    monkeypatch.setattr(db, "upsert_listing", lambda *a, **k: False)

    resp = client.patch("/api/listings/lst1", json={"category_id": "222"})
    assert resp.status_code == 503, resp.text


def test_a_value_the_model_rejects_is_a_client_error(api):
    """Not a 500. The patch still goes through the Listing model, so a
    malformed value is caught before it reaches storage."""
    client, saved = api
    resp = client.patch("/api/listings/lst1",
                        json={"category_id": {"not": "a category"}})

    assert 400 <= resp.status_code < 500, resp.text
    assert saved == {}

"""Answering a conflict, over HTTP.

The merge records conflicts and sends neither value. This is the route that
lets the seller say which one wins — without it a conflicted field is an edit
that never reaches eBay and never explains itself.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

RECORD = {
    "id": "lst1", "user_id": "u1", "status": "published",
    "listing": {
        "title": "Blue ceramic lamp", "price": 25.0, "quantity": 1,
        "remote_shadow": {"title": "Blue lamp"},
        "conflicts": {"title": {"local": "Blue ceramic lamp",
                                "remote": "Blue lamp, mid-century"}},
    },
}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from backend import config, db, main

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    saved: dict = {}
    monkeypatch.setattr(db, "get_listing", lambda lid:
                        dict(RECORD) if lid == "lst1" else None)
    monkeypatch.setattr(db, "enabled", lambda: True)
    monkeypatch.setattr(db, "upsert_listing",
                        lambda lid, data, **k: saved.update(data) or True)
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    return TestClient(main.app), saved


def test_the_seller_can_keep_their_own_version(api):
    client, saved = api
    resp = client.post("/api/listings/lst1/resolve-conflict",
                       json={"field": "title", "choice": "mine"})

    assert resp.status_code == 200, resp.text
    assert saved["title"] == "Blue ceramic lamp"
    assert "title" in saved["dirty_fields"], \
        "keeping the local version did not queue it to go to eBay"
    assert saved["conflicts"] == {}
    assert resp.json()["conflicts"] == []


def test_the_seller_can_take_ebays_version(api):
    client, saved = api
    resp = client.post("/api/listings/lst1/resolve-conflict",
                       json={"field": "title", "choice": "ebay"})

    assert resp.status_code == 200, resp.text
    assert saved["title"] == "Blue lamp, mid-century"
    assert "title" not in saved["dirty_fields"]


def test_a_stranger_cannot_answer_for_someone_else(api, monkeypatch):
    from backend import main

    client, saved = api
    monkeypatch.setattr(main, "_uid", lambda _r: "someone-else")

    resp = client.post("/api/listings/lst1/resolve-conflict",
                       json={"field": "title", "choice": "ebay"})
    assert resp.status_code == 404, resp.text
    assert saved == {}


def test_a_field_nobody_asked_about_is_refused(api):
    """The route exists to settle a question. Without this check it is a
    general "set any field to any value" endpoint."""
    client, saved = api
    resp = client.post("/api/listings/lst1/resolve-conflict",
                       json={"field": "price", "choice": "ebay"})

    assert resp.status_code == 400, resp.text
    assert saved == {}


@pytest.mark.parametrize("choice", ["", "local", "yes", "MINE"])
def test_an_answer_that_is_not_one_of_the_two_is_refused(api, choice):
    client, saved = api
    resp = client.post("/api/listings/lst1/resolve-conflict",
                       json={"field": "title", "choice": choice})
    assert resp.status_code == 400, resp.text
    assert saved == {}


def test_a_choice_that_did_not_commit_is_not_reported_as_saved(api, monkeypatch):
    """Otherwise the seller moves on believing the question closed, and finds
    the same one waiting after the next sync — with their value still not on
    eBay."""
    from backend import db

    client, _ = api
    monkeypatch.setattr(db, "upsert_listing", lambda *a, **k: False)

    resp = client.post("/api/listings/lst1/resolve-conflict",
                       json={"field": "title", "choice": "mine"})
    assert resp.status_code == 503, resp.text

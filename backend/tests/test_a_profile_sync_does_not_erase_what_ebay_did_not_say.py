"""Sync from eBay writes only what eBay said, never a blank over a name.

The connect callback already learned this the hard way: the Identity API's
answer is best-effort, identity_display turns anything it left out into "",
and "" is "we don't know" rather than a name. POST /api/profile/sync-ebay
wrote the username and email straight through, so pressing "Sync from eBay"
on an account whose token lacks the email scope — the usual case — erased the
email the app had, and a response without a username erased that too. A blank
username makes listing_sync.belongs_to scope nothing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402


@pytest.fixture
def sync(monkeypatch):
    saved: dict = {}
    monkeypatch.setattr(main.deps, "uid", lambda request: "u1")
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "t", "_uid": "u1"})
    monkeypatch.setattr(main.ebay_auth, "fetch_policies_and_location",
                        lambda _t: {})
    monkeypatch.setattr(main.db, "get_ebay_account", lambda uid: {
        "ebay_username": "known_seller", "ebay_email": "seller@example.com"})
    monkeypatch.setattr(main.db, "save_ebay_account",
                        lambda uid, **kw: saved.update(kw))
    monkeypatch.setattr(main.db, "update_user", lambda *a, **k: None)
    monkeypatch.setattr(main.auth, "current_user",
                        lambda request: {"id": "u1", "display_name": "Me"})
    monkeypatch.setattr(main, "get_profile", lambda request: {"ok": True})

    def run(identity: dict) -> dict:
        saved.clear()
        monkeypatch.setattr(main.ebay_auth, "fetch_user_identity",
                            lambda _t: identity)
        res = TestClient(main.app).post("/api/profile/sync-ebay")
        assert res.status_code == 200, res.text
        return saved
    return run


def test_an_answer_without_the_email_leaves_the_email_alone(sync):
    saved = sync({"userId": "E1", "username": "known_seller"})
    assert "ebay_email" not in saved, "a blank was written over the stored email"
    assert saved["ebay_username"] == "known_seller"
    assert saved["ebay_user_id"] == "E1"


def test_an_answer_without_a_username_leaves_the_username_alone(sync):
    saved = sync({"userId": "E1"})
    assert "ebay_username" not in saved


def test_what_ebay_did_say_is_still_written(sync):
    saved = sync({"userId": "E1", "username": "renamed_seller",
                  "individualAccount": {"email": "new@example.com"}})
    assert saved["ebay_username"] == "renamed_seller"
    assert saved["ebay_email"] == "new@example.com"

"""`{"ok": true}` and "connected" are claims about a write that landed.

P0-06 removed this from the eBay paths: repository commands stopped swallowing
failures, the eBay OAuth callback stopped redirecting to "connected" on a save
that never committed, and Settings stopped answering `{"ok": true}` for a
write that did not happen. `db.save_marketplace_account` was given a return
value for the same reason, and its docstring says exactly what ignoring it
costs:

    "a caller that cannot tell a swallowed failure from a success will happily
    carry on with an access token whose refresh token was never stored, and
    the connection dies silently an hour later."

Three callers ignored it anyway — the generic (Etsy/Depop) OAuth callback, the
Etsy settings route, and Depop's own token refresh. The Etsy provider is the
one that reads it, and its comment is the model for all of them.

The Depop one is the harm the docstring names, verbatim: Depop rotates its
refresh token, so a save that fails leaves the database holding a token the
provider has already invalidated. Serving that one request and carrying on
makes the connection permanently unrecoverable, with the failure landing
hours later on a publish, far from anything the seller can connect it to.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def app(monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "_uid", lambda _r: "u1")

    def _saves(landed: bool):
        calls: list[dict] = []

        def _save(user_id, marketplace, **fields):
            calls.append({"marketplace": marketplace, **fields})
            return landed

        monkeypatch.setattr(main.db, "save_marketplace_account", _save)
        return TestClient(main.app), calls
    return _saves


# ------------------------------------------------- the Etsy settings route

def test_a_failed_settings_write_is_not_reported_as_saved(app):
    api, calls = app(landed=False)
    resp = api.post("/api/etsy/settings-options",
                    json={"shipping_profile_id": "sp-1"})

    assert calls, "it should still have tried"
    assert resp.status_code != 200, "a write that did not land answered ok"
    # A storage outage, not the seller's mistake: 503, not 4xx.
    assert resp.status_code == 503
    assert "ok" not in resp.json(), resp.text


def test_a_settings_write_that_landed_still_says_so(app):
    api, _ = app(landed=True)
    resp = api.post("/api/etsy/settings-options",
                    json={"shipping_profile_id": "sp-1"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["selected"] == {"shipping_profile_id": "sp-1"}


# ------------------------------------------------- Depop's rotating token

def test_depop_treats_an_unstored_rotated_token_as_a_failed_refresh():
    """Same rule the Etsy provider already applies, and the same reason: the
    refresh already invalidated the stored token, so carrying on serves one
    request and breaks the connection for good."""
    from unittest.mock import patch

    from backend import db, depop_auth
    from backend.marketplaces import depop_provider

    provider = depop_provider.DepopProvider()
    depop_provider._ACCESS_CACHE.clear()

    account = {"refresh_token": "old-token", "external_id": "shop-1"}
    refreshed = {"refresh_token": "new-token", "access_token": "acc",
                 "expires_at": 9_999_999_999}

    with patch.object(db, "get_marketplace_account", lambda *_a: account), \
         patch.object(depop_auth, "refresh_access_token", lambda _t: refreshed), \
         patch.object(db, "save_marketplace_account", lambda *_a, **_k: False):
        assert provider.creds_for("u1") is None, \
            "carried on with a rotated token it could not store"

    depop_provider._ACCESS_CACHE.clear()
    with patch.object(db, "get_marketplace_account", lambda *_a: account), \
         patch.object(depop_auth, "refresh_access_token", lambda _t: refreshed), \
         patch.object(db, "save_marketplace_account", lambda *_a, **_k: True):
        creds = provider.creds_for("u1")
        assert creds and creds["access_token"] == "acc"


# --------------------------------------------- and what a no-database means

def test_no_database_reports_a_failed_write_rather_than_nothing():
    """`-> bool` and a bare `return`. Both are falsy so callers reading it as
    a failure were right, but a function that says it returns whether the
    write landed should say False rather than None — the next caller to write
    `is False` would be quietly wrong."""
    from backend import config, db

    saved = config.DATABASE_URL
    try:
        config.DATABASE_URL = ""
        assert db.save_marketplace_account("u1", "etsy", external_id="x") is False
    finally:
        config.DATABASE_URL = saved

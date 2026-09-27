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
def app(monkeypatch, every_marketplace):
    from backend import main

    monkeypatch.setattr(main.deps, "uid", lambda _r: "u1")

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
                    json={"shipping_profile_id": "101"})

    assert calls, "it should still have tried"
    assert resp.status_code != 200, "a write that did not land answered ok"
    # A storage outage, not the seller's mistake: 503, not 4xx.
    assert resp.status_code == 503
    assert "ok" not in resp.json(), resp.text


def test_a_settings_write_that_landed_still_says_so(app):
    api, _ = app(landed=True)
    resp = api.post("/api/etsy/settings-options",
                    json={"shipping_profile_id": "101"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["selected"] == {"shipping_profile_id": "101"}


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


def test_a_settings_id_that_is_not_a_number_is_refused_before_the_write(app):
    """Etsy ids are numbers. A stray value used to be stored as typed and
    then crash the publish with int()'s own sentence; now it is a 400 at
    the moment the seller can still pick from the list."""
    api, calls = app(landed=True)
    resp = api.post("/api/etsy/settings-options",
                    json={"readiness_state_id": "fast"})
    assert resp.status_code == 400
    assert "readiness_state_id" in resp.json()["detail"]
    assert not calls, "nothing should have been written"


# ------------------------------------------- and the other direction, later
# The same rule, unapplied to disconnect until a seller reported that reset
# did nothing. `db.disconnect_*` swallowed every failure and returned None,
# all four providers declared `-> None`, and all three routes answered
# `{"ok": true}` unconditionally — so a disconnect that never touched the
# row was indistinguishable from one that did, at every layer. The seller
# got a success toast and a card that still said connected.
#
# Worse than the save case in one way: the seller reaching for disconnect is
# usually already stuck, so the reset they are told worked is the thing they
# keep retrying instead of reporting.


@pytest.fixture()
def disconnects(monkeypatch, every_marketplace):
    from backend import main

    monkeypatch.setattr(main.deps, "uid", lambda _r: "u1")

    def _with(landed: bool):
        calls: list[str] = []

        def _drop(user_id, marketplace):
            calls.append(marketplace)
            return landed

        def _drop_ebay(user_id):
            calls.append("ebay")
            return landed

        monkeypatch.setattr(main.db, "disconnect_marketplace_account", _drop)
        monkeypatch.setattr(main.db, "disconnect_ebay_account", _drop_ebay)
        return TestClient(main.app), calls
    return _with


@pytest.mark.parametrize("path,marketplace", [
    ("/api/etsy/disconnect", "etsy"),
    ("/api/ebay/disconnect", "ebay"),
    ("/api/easypost/disconnect", "easypost"),
])
def test_a_disconnect_that_did_not_land_is_not_reported_as_done(
        disconnects, path, marketplace):
    api, calls = disconnects(landed=False)
    resp = api.post(path)

    assert calls, "it should still have tried"
    assert resp.status_code != 200, "a disconnect that did not land answered ok"
    assert resp.status_code == 503          # a storage outage, not a mistake
    assert "ok" not in resp.json(), resp.text
    # The seller has to know the state is unchanged, not half-dropped.
    assert "nothing changed" in resp.json()["detail"]


@pytest.mark.parametrize("path", [
    "/api/etsy/disconnect",
    "/api/ebay/disconnect",
    "/api/easypost/disconnect",
])
def test_a_disconnect_that_landed_still_says_so(disconnects, path):
    api, calls = disconnects(landed=True)
    resp = api.post(path)

    assert calls
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_the_provider_reports_the_row_not_its_own_cache():
    """Etsy drops its cached token and refresh lock either way — they are
    this process's copy of a link the seller asked to drop, and keeping them
    because the write failed would publish to a shop they believe is gone.
    What it RETURNS is the row, which is what `connected` is read from."""
    from unittest.mock import patch

    from backend import db
    from backend.marketplaces import etsy_provider

    provider = etsy_provider.EtsyProvider()
    etsy_provider._ACCESS_CACHE["u1"] = (9_999_999_999, "token")

    with patch.object(db, "disconnect_marketplace_account", lambda *_a: False):
        assert provider.disconnect("u1") is False
    assert "u1" not in etsy_provider._ACCESS_CACHE, \
        "kept a cached token for a connection the seller asked to drop"

    with patch.object(db, "disconnect_marketplace_account", lambda *_a: True):
        assert provider.disconnect("u1") is True


def test_nothing_to_disconnect_is_not_a_failed_disconnect():
    """No row means nothing is connected, which is the state asked for.
    Reporting that as a failure would 503 the seller for succeeding."""
    from backend import config, db

    saved = config.DATABASE_URL
    try:
        config.DATABASE_URL = ""
        assert db.disconnect_marketplace_account("u1", "etsy") is True
        assert db.disconnect_ebay_account("u1") is True
    finally:
        config.DATABASE_URL = saved

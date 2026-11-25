"""Etsy and Depop rotate refresh tokens; two edges of that are covered here.

* A refresh that is already on its way to Etsy when the seller presses
  Disconnect used to come back and save its rotated token -- writing a token
  onto the row the disconnect had just cleared, so the shop was connected
  again with nothing on screen saying so. The same write after a RECONNECT
  replaced the new connection's token with the old one's.
* The access-token cache was capped by emptying it: the 51st seller's
  refresh threw away every other seller's live token, and all of them paid a
  refresh on their next request. eBay's provider stopped doing that a while
  ago; these two still did.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import depop_provider, etsy_provider

PROVIDERS = [
    (etsy_provider, etsy_provider.EtsyProvider, "etsy_auth", "etsy"),
    (depop_provider, depop_provider.DepopProvider, "depop_auth", "depop"),
]


@pytest.fixture(params=PROVIDERS, ids=["etsy", "depop"])
def setup(request, monkeypatch):
    module, cls, auth_name, key = request.param
    row = {"refresh_token": "rt-old", "external_id": "42",
           "settings": {"shop_id": "42"}}
    saved = []

    def _get(uid, which):
        assert which == key
        return dict(row) if row.get("refresh_token") is not None else None

    def _save(uid, which, **fields):
        saved.append(fields)
        row.update(fields)
        return True

    monkeypatch.setattr(module.db, "get_marketplace_account", _get)
    monkeypatch.setattr(module.db, "save_marketplace_account", _save)
    module._ACCESS_CACHE.clear()
    yield module, cls(), getattr(module, auth_name), row, saved
    module._ACCESS_CACHE.clear()


def test_a_refresh_that_lands_after_a_disconnect_does_not_reconnect(setup,
                                                                   monkeypatch):
    module, provider, auth, row, saved = setup

    def _refresh(_token):
        row["refresh_token"] = ""          # the seller disconnected meanwhile
        return {"access_token": "at", "refresh_token": "rt-new",
                "expires_at": 9e12}

    monkeypatch.setattr(auth, "refresh_access_token", _refresh)
    assert provider.creds_for("u1") is None
    assert row["refresh_token"] == ""
    assert not [s for s in saved if s.get("refresh_token")]


def test_a_refresh_that_lands_after_a_reconnect_keeps_the_new_token(setup,
                                                                    monkeypatch):
    module, provider, auth, row, saved = setup

    def _refresh(_token):
        row["refresh_token"] = "rt-reconnected"
        return {"access_token": "at", "refresh_token": "rt-new",
                "expires_at": 9e12}

    monkeypatch.setattr(auth, "refresh_access_token", _refresh)
    assert provider.creds_for("u1") is None
    assert row["refresh_token"] == "rt-reconnected"


def test_an_ordinary_refresh_still_stores_the_rotation(setup, monkeypatch):
    module, provider, auth, row, saved = setup
    monkeypatch.setattr(auth, "refresh_access_token",
                        lambda _t: {"access_token": "at", "refresh_token": "rt-new",
                                    "expires_at": 9e12})
    creds = provider.creds_for("u1")
    assert creds and creds["access_token"] == "at"
    assert row["refresh_token"] == "rt-new"


def test_a_full_cache_keeps_the_other_sellers_live_tokens(setup, monkeypatch):
    module, provider, auth, row, saved = setup
    for n in range(60):
        module._ACCESS_CACHE[f"seller-{n}"] = (9e12 - n, f"tok-{n}")
    module._ACCESS_CACHE["gone"] = (1.0, "expired")
    monkeypatch.setattr(auth, "refresh_access_token",
                        lambda _t: {"access_token": "at", "refresh_token": "rt-new",
                                    "expires_at": 9e12})
    assert provider.creds_for("u1")
    assert "gone" not in module._ACCESS_CACHE
    assert "u1" in module._ACCESS_CACHE
    kept = [k for k in module._ACCESS_CACHE if k.startswith("seller-")]
    assert len(kept) >= 40, "a full cache was emptied rather than trimmed"
    assert len(module._ACCESS_CACHE) <= 51

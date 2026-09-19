"""A connection whose shop lookup failed is not broken for good.

exchange_code looks the shop up best-effort, and when that failed the
account was stored with a token and no shop: Settings said "connected",
every publish said "connect Etsy first", and nothing ever tried again. The
lookup now runs again the next time the connection is used, and the answer
is stored; until one lands, the status says so.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import etsy_provider


@pytest.fixture
def account(monkeypatch):
    store = {"refresh_token": "rt", "external_id": "", "external_username": "",
             "settings": {}}
    saved = []
    monkeypatch.setattr(etsy_provider.db, "get_marketplace_account",
                        lambda uid, key: dict(store, settings=dict(store["settings"])))

    def _save(uid, key, **fields):
        saved.append(dict(fields))
        settings = fields.pop("settings", None)
        store.update(fields)
        if settings:
            store["settings"].update(settings)
        return True

    monkeypatch.setattr(etsy_provider.db, "save_marketplace_account", _save)
    monkeypatch.setattr(etsy_provider.etsy_auth, "refresh_access_token",
                        lambda rt: {"access_token": "at", "refresh_token": "rt2",
                                    "expires_at": 9e12})
    etsy_provider._ACCESS_CACHE.clear()
    return store, saved


def test_the_status_says_reconnect_while_no_shop_is_known(account):
    status = etsy_provider.EtsyProvider().account_status("u1")
    assert status["connected"] is True
    assert status["needs_reconnect"] is True
    assert status["shop_id"] == ""


def test_the_next_use_looks_the_shop_up_and_keeps_it(account, monkeypatch):
    store, saved = account
    monkeypatch.setattr(etsy_provider.etsy_auth, "fetch_me", lambda tok: {"shop_id": 42})
    monkeypatch.setattr(etsy_provider.etsy_auth, "fetch_shop",
                        lambda tok, sid: {"shop_name": "MugsByLeif", "currency_code": "usd"})
    creds = etsy_provider.EtsyProvider().creds_for("u1")
    assert creds and creds["shop_id"] == "42"
    healed = [s for s in saved if s.get("external_id") == "42"]
    assert healed and healed[0]["settings"] == {"shop_id": "42", "currency_code": "USD"}
    assert healed[0]["external_username"] == "MugsByLeif"
    assert etsy_provider.EtsyProvider().account_status("u1")["needs_reconnect"] is False


def test_a_lookup_that_still_fails_is_still_not_a_connection(account, monkeypatch):
    def _down(tok):
        raise RuntimeError("etsy is down")
    monkeypatch.setattr(etsy_provider.etsy_auth, "fetch_me", _down)
    assert etsy_provider.EtsyProvider().creds_for("u1") is None
    assert etsy_provider.EtsyProvider().account_status("u1")["needs_reconnect"] is True

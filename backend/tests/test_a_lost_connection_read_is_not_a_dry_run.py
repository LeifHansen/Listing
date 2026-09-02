"""A database blip must not turn a live publish into a dry run.

`creds_for` answers None for two different reasons, and the publish path
cannot tell them apart:

  * the seller has not connected this marketplace — a dry run is exactly
    right, and the message says to connect it in Settings;
  * `db.get_marketplace_account` (or `get_ebay_account`) hit an exception and
    swallowed it into None — the seller IS connected, pressed Publish, and
    gets `ok: True` with a dry-run body. Nothing was listed.

The Etsy provider's own comment already names this outcome — a failed token
save leaves "the next publish quietly falling through to a dry run" — but the
READ underneath it had the same hole and nothing said so.

The rule is the one this branch has applied to every other read: a failure is
not an answer. These two raise now, with `*_best_effort` variants for the
advisory callers where an unknown connection state genuinely cannot mislead
(the same split `list_listings` / `list_listings_best_effort` already uses).
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from backend.errors import StorageUnavailable  # noqa: E402


@pytest.fixture
def connected(dbmod):
    """A user with a live Etsy and eBay connection in a scratch database."""
    from backend import auth
    rec = dbmod.create_user("u-conn", "conn@example.com",
                            auth.hash_password("hunter2hunter2"))
    assert rec not in (None, dbmod.EMAIL_TAKEN)
    uid = rec["id"]
    assert dbmod.save_marketplace_account(
        uid, "etsy", refresh_token="rt-etsy", account_id="shop-1")
    dbmod.save_ebay_account(uid, refresh_token="rt-ebay", ebay_user_id="ebayer")
    return uid


def _break(dbmod, monkeypatch):
    """The fixture RELOADS backend.db, so patch the module it handed back."""
    def _boom():
        raise RuntimeError("connection to Neon reset by peer")
    monkeypatch.setattr(dbmod, "_get_engine", _boom)


def test_an_unreadable_marketplace_account_is_not_a_missing_one(
        dbmod, connected, monkeypatch):
    assert dbmod.get_marketplace_account(connected, "etsy")["refresh_token"] == "rt-etsy"
    _break(dbmod, monkeypatch)
    with pytest.raises(StorageUnavailable):
        dbmod.get_marketplace_account(connected, "etsy")


def test_an_unreadable_ebay_account_is_not_a_missing_one(
        dbmod, connected, monkeypatch):
    assert dbmod.get_ebay_account(connected)["refresh_token"] == "rt-ebay"
    _break(dbmod, monkeypatch)
    with pytest.raises(StorageUnavailable):
        dbmod.get_ebay_account(connected)


def test_a_genuinely_absent_account_is_still_none(dbmod, connected):
    """Not connected is a real answer and must stay cheap and quiet."""
    assert dbmod.get_marketplace_account(connected, "depop") is None
    assert dbmod.get_ebay_account("nobody") is None


def test_no_database_configured_is_still_none(dbmod, connected, monkeypatch):
    """A deployment without a database has no connections and never will —
    a configuration, not a failure."""
    monkeypatch.setattr(dbmod, "_get_engine", lambda: None)
    assert dbmod.get_marketplace_account(connected, "etsy") is None
    assert dbmod.get_ebay_account(connected) is None


def test_the_best_effort_readers_absorb_it_for_advisory_callers(
        dbmod, connected, monkeypatch):
    """Some callers only decorate a screen with the connection state. They opt
    IN to None-on-failure by name, so the choice is visible at the call site
    instead of being everyone's default."""
    _break(dbmod, monkeypatch)
    assert dbmod.get_marketplace_account_best_effort(connected, "etsy") is None
    assert dbmod.get_ebay_account_best_effort(connected) is None


def test_creds_for_does_not_answer_not_connected_on_a_failed_read(
        dbmod, connected, monkeypatch):
    """The provider is where None becomes a dry run, so the failure has to
    still be a failure by the time it gets there."""
    from backend.marketplaces import etsy_provider
    _break(dbmod, monkeypatch)
    monkeypatch.setattr(etsy_provider, "db", dbmod)
    etsy_provider._ACCESS_CACHE.pop(connected, None)
    with pytest.raises(StorageUnavailable):
        etsy_provider.EtsyProvider().creds_for(connected)

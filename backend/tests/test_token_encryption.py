"""Marketplace refresh tokens are encrypted at rest.

A refresh token is a long-lived key to a seller's whole store — it mints
access tokens for as long as the connection lasts (eBay's for ~18 months), and
those can list, revise, end, and read orders. Held in plaintext, one leaked
database dump hands over every connected account, and nothing about that leak
would look like an eBay event to the seller.

The requirement is narrow: what lands in the column is unreadable, what
callers get back is the token, and rows written before any of this existed
keep working.
"""
from __future__ import annotations

import importlib

import pytest

from backend import config, crypto, db

TOKEN = "v^1.1#i^1#f^0#r^1#I^3#p^3#t^Ul4xMF8xMDo" + "A" * 400


@pytest.fixture(autouse=True)
def _fresh_cipher():
    crypto.reset_cache()
    yield
    crypto.reset_cache()


# --- the scheme itself ------------------------------------------------------

def test_a_token_survives_the_round_trip():
    assert crypto.decrypt(crypto.encrypt(TOKEN)) == TOKEN


def test_the_stored_form_does_not_contain_the_token():
    """The actual requirement. Anything else here is in service of it."""
    stored = crypto.encrypt(TOKEN)
    assert TOKEN not in stored
    assert crypto.is_encrypted(stored)


def test_two_encryptions_of_one_token_differ():
    """Fernet carries a random IV. Identical ciphertext for identical input
    would tell a reader which sellers reconnected with the same token."""
    assert crypto.encrypt(TOKEN) != crypto.encrypt(TOKEN)


def test_a_tampered_value_does_not_decrypt_to_something_else():
    """Authenticated encryption: a flipped byte fails, it does not silently
    yield a different token."""
    stored = crypto.encrypt(TOKEN)
    flipped = stored[:-4] + ("aaaa" if not stored.endswith("aaaa") else "bbbb")
    assert crypto.decrypt(flipped) == ""


def test_a_disconnect_is_still_recorded_as_empty():
    """"" is how every connected-check spells "not connected". Encrypting it
    would make a disconnected account look connected."""
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("") == ""


def test_encrypting_twice_does_not_double_wrap():
    once = crypto.encrypt(TOKEN)
    assert crypto.encrypt(once) == once


# --- rows that predate this -------------------------------------------------

def test_a_plaintext_row_is_still_readable():
    """No flag day: a value with no marker is returned as-is, so sellers
    connected before this shipped keep working and re-encrypt on the next
    save."""
    assert crypto.decrypt("legacy-plaintext-token") == "legacy-plaintext-token"
    assert not crypto.is_encrypted("legacy-plaintext-token")


# --- a changed key costs a reconnect, not an error page ---------------------

def test_a_token_from_another_key_reads_as_disconnected(monkeypatch):
    """Every caller reads a refresh token to decide whether the account is
    connected, so "" routes the seller to reconnect. Raising here would take
    down whatever page asked instead."""
    from cryptography.fernet import Fernet

    stored = crypto.encrypt(TOKEN)
    monkeypatch.setattr(crypto.config, "TOKEN_ENCRYPTION_KEY",
                        Fernet.generate_key().decode())
    crypto.reset_cache()
    assert crypto.decrypt(stored) == ""


def test_an_explicit_key_is_preferred_over_the_derived_one(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto.config, "TOKEN_ENCRYPTION_KEY", key)
    crypto.reset_cache()
    stored = crypto.encrypt(TOKEN)
    # Readable with that key alone — so the key really is what protects it.
    assert Fernet(key.encode()).decrypt(
        stored[len("enc:v1:"):].encode()).decode() == TOKEN


def test_the_derived_key_follows_the_secret(monkeypatch):
    """Documented consequence: rotating SECRET_KEY with no explicit key set
    makes stored tokens unreadable. Pinned so it can't become a surprise."""
    monkeypatch.setattr(crypto.config, "TOKEN_ENCRYPTION_KEY", "")
    crypto.reset_cache()
    stored = crypto.encrypt(TOKEN)
    monkeypatch.setattr(crypto.config, "SECRET_KEY", "a-different-secret")
    crypto.reset_cache()
    assert crypto.decrypt(stored) == ""


# --- the column has to hold it ---------------------------------------------

def test_the_ciphertext_fits_the_column():
    """A silently truncated token disconnects a seller with no error at all.
    eBay's refresh tokens run ~450 chars; this pins headroom well past that
    against the widened 4096 column."""
    assert len(crypto.encrypt("x" * 2000)) < 4096


# --- what actually lands in the column -------------------------------------

@pytest.fixture
def accounts_db(monkeypatch, tmp_path):
    """The db module against a throwaway SQLite file, with its memoized
    engine and schema-init state reset (same shape as test_tokens.py's)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    importlib.reload(config)
    db._engine = None
    db._initialized = False
    crypto.reset_cache()
    yield db
    db._engine = None
    db._initialized = False
    crypto.reset_cache()
    importlib.reload(config)


def _raw_column(database, user_id: str) -> str:
    """Straight out of the table, bypassing get_ebay_account's decrypt."""
    with database.Session(database._get_engine()) as s:
        return s.get(database.EbayAccount, user_id).refresh_token


def test_the_token_in_the_database_is_not_the_token(accounts_db):
    """End to end: this is the whole point of the change."""
    accounts_db.save_ebay_account("u1", refresh_token=TOKEN,
                                  ebay_username="seller")
    assert TOKEN not in _raw_column(accounts_db, "u1")


def test_callers_still_get_the_real_token_back(accounts_db):
    """Everything downstream — creds_for, the token cache, every eBay call —
    reads this and must not know anything changed."""
    accounts_db.save_ebay_account("u1", refresh_token=TOKEN)
    assert accounts_db.get_ebay_account("u1")["refresh_token"] == TOKEN


def test_a_plaintext_row_written_before_this_still_works(accounts_db):
    """Simulates an existing production row: written straight into the column
    with no encryption, exactly as the old save did."""
    accounts_db.save_ebay_account("u1", refresh_token="x")
    with accounts_db.Session(accounts_db._get_engine()) as s:
        s.get(accounts_db.EbayAccount, "u1").refresh_token = TOKEN  # plaintext
        s.commit()
    assert accounts_db.get_ebay_account("u1")["refresh_token"] == TOKEN


def test_a_legacy_row_re_encrypts_when_it_is_next_saved(accounts_db):
    """How the migration completes: no flag day, no backfill script."""
    accounts_db.save_ebay_account("u1", refresh_token="x")
    with accounts_db.Session(accounts_db._get_engine()) as s:
        s.get(accounts_db.EbayAccount, "u1").refresh_token = TOKEN
        s.commit()
    accounts_db.save_ebay_account("u1", refresh_token=TOKEN)
    assert TOKEN not in _raw_column(accounts_db, "u1")


def test_disconnecting_still_reads_as_disconnected(accounts_db):
    """`connected` is `bool(refresh_token)` in half a dozen places."""
    accounts_db.save_ebay_account("u1", refresh_token=TOKEN)
    accounts_db.disconnect_ebay_account("u1")
    assert accounts_db.get_ebay_account("u1")["refresh_token"] == ""


def test_the_other_marketplaces_are_covered_too(accounts_db):
    """Etsy and Depop hold the same kind of secret in a different table.
    Encrypting one store and not the other would be a half-done posture."""
    accounts_db.save_marketplace_account("u1", "etsy", refresh_token=TOKEN,
                                         external_username="shop")
    with accounts_db.Session(accounts_db._get_engine()) as s:
        raw = s.get(accounts_db.MarketplaceAccount, ("u1", "etsy")).refresh_token
    assert TOKEN not in raw
    got = accounts_db.get_marketplace_account("u1", "etsy")
    assert got["refresh_token"] == TOKEN and got["external_username"] == "shop"

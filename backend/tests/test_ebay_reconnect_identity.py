"""Reconnecting must not erase the eBay username we already knew.

`fetch_user_identity` is best-effort and documented as such: it 403s on any
connection made before the identity scope was granted, and it fails outright
whenever eBay's identity service is having a bad day. The callback catches
that and carries on with an empty username — correct, because connecting is
too important to block on a nicety.

What was not correct was writing the empty string down. `""` is not a name; it
is "we couldn't find out". Stored, it takes out the two things that depend on
knowing which eBay account a record belongs to:

  - `listing_sync.belongs_to` scopes nothing, so every previous account's
    listings read as the connected seller's again — the exact regression #176
    was written to fix.
  - `db.count_foreign_listings` counts every labelled record as foreign, so a
    seller whose identity call merely timed out is shown a banner offering to
    release their entire store.

`db.save_ebay_account` skips `None` and writes `""`, so `None` is how "leave
what's already there" is spelled.
"""
from __future__ import annotations

import pytest

# Importing backend.main pulls the whole app in. `checks` has neither of these,
# so it skips the file; the smoke job's "API tests" step is where it runs, and
# that step fails on a skip so this can never quietly stop running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import auth, main  # noqa: E402


@pytest.fixture
def connect(monkeypatch):
    """Drive /api/ebay/callback with a valid signed state + nonce cookie.

    Returns the kwargs the handler passed to db.save_ebay_account.
    """
    def _run(*, identity_works: bool, stored_username: str = "alice"):
        saved: dict = {}
        monkeypatch.setattr(main.ebay_auth, "exchange_code",
                            lambda code: {"access_token": "a", "refresh_token": "r"})
        monkeypatch.setattr(main.ebay_auth, "fetch_policies_and_location",
                            lambda _t: {})

        def _identity(_t):
            if not identity_works:
                raise RuntimeError("eBay identity service returned 403")
            return {"userId": "alice", "username": "alice"}
        monkeypatch.setattr(main.ebay_auth, "fetch_user_identity", _identity)
        monkeypatch.setattr(main.ebay_auth, "identity_display",
                            lambda raw: {"username": raw.get("username", ""),
                                         "email": ""})
        monkeypatch.setattr(main.db, "get_ebay_account",
                            lambda uid: {"ebay_username": stored_username})
        monkeypatch.setattr(main.db, "save_ebay_account",
                            lambda uid, **kw: saved.update(kw))
        monkeypatch.setattr(main.ebay_account, "reconcile_account_settings",
                            lambda *a, **k: ({}, True))
        monkeypatch.setattr(main.db, "stamp_ebay_account", lambda uid, name: 0)

        nonce = "test-nonce"
        client = TestClient(main.app)
        client.cookies.set(main.EBAY_NONCE_COOKIE, nonce)
        client.get("/api/ebay/callback",
                   params={"code": "c", "state": auth.make_state("u1", nonce)},
                   follow_redirects=False)
        return saved
    return _run


def test_a_failed_identity_fetch_leaves_the_stored_username_alone(connect):
    """Fails against the old code, which wrote ebay_username="" here."""
    saved = connect(identity_works=False)
    assert saved.get("ebay_username") is None, (
        "an unreadable identity must not overwrite a known account name")


def test_a_successful_identity_fetch_still_records_the_name(connect):
    saved = connect(identity_works=True, stored_username="")
    assert saved["ebay_username"] == "alice"


def test_the_refresh_token_is_always_written(connect):
    """The one thing a reconnect definitely learned."""
    assert connect(identity_works=False)["refresh_token"] == "r"

"""What a gated seller is told depends on which tier Etsy has us on.

The card in Settings is the only place a seller learns why Connect Etsy is
held back, and there are two different waits behind it. On the seller app Etsy
registers by default, nobody but the keystring's owner has ever been cleared.
On an approved personal app, Etsy HAS cleared a handful of shops — theirs just
isn't one of them, and what they are waiting for is the Commercial Access
grant above it.

Telling the second seller we are "under review" is a small lie that outlives
the wait it describes: Etsy finished reviewing, and the next thing to happen
is a grant nobody has applied for yet. So the note follows the tier, and the
gate itself does not move — the roster and the pre-redirect refusal behave
exactly as they did.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic")

from backend import marketplaces  # noqa: E402
from backend.marketplaces import etsy_provider  # noqa: E402

ETSY_CREDS = {"ETSY_CLIENT_ID": "key123",
              "ETSY_REDIRECT_URI": "https://app.example/api/etsy/callback"}


@pytest.fixture
def etsy(monkeypatch):
    """The registered provider, with the user lookup answering a fixed email
    so these tests stay about the tier rather than about the database."""
    provider = etsy_provider.EtsyProvider()

    def _as(email: str):
        monkeypatch.setattr(etsy_provider.db, "get_user_by_id",
                            lambda uid: {"id": uid, "email": email})
        return provider
    return _as


def test_an_unapproved_app_says_nobody_else_is_cleared(fresh_config, etsy):
    fresh_config(ETSY_OWNER_EMAILS="owner@example.com", **ETSY_CREDS)
    pending, note = marketplaces.access_pending(etsy("seller@example.com"), "u1")
    assert pending is True
    assert "approve our app for other sellers" in note
    assert "reviewing" not in note


def test_an_approved_personal_app_says_the_seats_are_the_wait(fresh_config, etsy):
    fresh_config(ETSY_ACCESS_TIER="personal",
                 ETSY_OWNER_EMAILS="owner@example.com", **ETSY_CREDS)
    pending, note = marketplaces.access_pending(etsy("seller@example.com"), "u1")
    assert pending is True
    assert "approved us for a limited number of shops" in note
    assert "Commercial Access" in note


def test_a_seated_seller_is_not_pending_at_either_tier(fresh_config, etsy):
    """The point of the roster: an approval that seats sellers has to let
    them through, and the owner never stopped being able to connect."""
    for tier in ("seller", "personal"):
        fresh_config(ETSY_ACCESS_TIER=tier,
                     ETSY_OWNER_EMAILS="owner@example.com,beta@example.com",
                     **ETSY_CREDS)
        assert marketplaces.access_pending(
            etsy("beta@example.com"), "u1") == (False, "")


def test_commercial_access_asks_the_database_nothing(fresh_config, monkeypatch):
    """With the gate retired the answer is no for everyone, and this runs on
    every roster build — so it must not cost a user lookup per marketplace."""
    fresh_config(ETSY_ACCESS_TIER="commercial",
                 ETSY_OWNER_EMAILS="owner@example.com", **ETSY_CREDS)

    def _boom(uid):
        raise AssertionError("looked the seller up with nothing left to gate")

    monkeypatch.setattr(etsy_provider.db, "get_user_by_id", _boom)
    assert marketplaces.access_pending(
        etsy_provider.EtsyProvider(), "u1") == (False, "")


def test_an_unconfigured_etsy_is_not_pending_at_any_tier(fresh_config, etsy):
    """"Not set up on the server" is a different story, and the operator's
    missing-credentials explainer is the more useful one to show."""
    fresh_config(ETSY_ACCESS_TIER="personal",
                 ETSY_OWNER_EMAILS="owner@example.com")
    assert marketplaces.access_pending(
        etsy("seller@example.com"), "u1") == (False, "")

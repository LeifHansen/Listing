"""The editor asks "is this ready?" with mode="revise", and Etsy used to say
"nothing to fix" for any mode but "live" — then refuse the publish that
followed. And a draft save with Etsy ticked went out unchecked, to be
refused by createDraftListing in Etsy's own words. Both now run the same
checklist the live publish runs, so a refusal is named here first.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import etsy_provider
from backend.marketplaces.base import PublishContext
from backend.models import Listing


@pytest.fixture
def account(monkeypatch):
    settings = {"shop_id": "1", "shipping_profile_id": "7",
                "return_policy_id": "8", "readiness_state_id": "9"}
    monkeypatch.setattr(etsy_provider.db, "get_marketplace_account",
                        lambda uid, key: {"refresh_token": "rt", "settings": settings})
    return settings


def _listing(**etsy) -> Listing:
    return Listing(title="Vintage mug", description="Nice.", price=12.0,
                   quantity=1, images=["a.jpg"], etsy=etsy)


def test_a_revise_gets_the_same_checklist_as_a_live_publish(account):
    provider = etsy_provider.EtsyProvider()
    incomplete = _listing()          # no taxonomy, no attribution
    revise = {i["target"] for i in provider.preflight("u1", incomplete, "revise")}
    live = {i["target"] for i in provider.preflight("u1", incomplete, "live")}
    assert revise == live
    assert {"etsy_taxonomy", "etsy_attribution"} <= revise


def test_a_draft_with_etsy_ticked_is_checked_before_it_is_sent(account, monkeypatch):
    provider = etsy_provider.EtsyProvider()

    def _never(*args, **kwargs):
        raise AssertionError("nothing may reach Etsy for a listing it would refuse")

    monkeypatch.setattr(etsy_provider.etsy, "create_draft_listing", _never)
    ctx = PublishContext(session_id="s1", listing=_listing(), mode="draft",
                         base_url="https://app.test", uid="u1", prev_record={})
    outcome = provider.publish(ctx, {"access_token": "t", "shop_id": "1",
                                     "settings": account})
    assert outcome.ok is False
    assert outcome.status == ""
    targets = {i["target"] for i in outcome.issues}
    assert "etsy_taxonomy" in targets and "etsy_attribution" in targets
    assert "Not quite ready for Etsy" in outcome.message


def test_a_draft_may_still_leave_the_return_policy_for_later(account, monkeypatch):
    """What Etsy checks at activation is a warning on a draft, not a stop."""
    provider = etsy_provider.EtsyProvider()
    account.pop("return_policy_id")
    complete = _listing(taxonomy_id=1, who_made="someone_else", when_made="1990s")
    issues = provider.preflight("u1", complete, "draft")
    assert not [i for i in issues if i["level"] == "error"]
    assert [i for i in issues if i["target"] == "etsy_return_policy"
            and i["level"] == "warn"]
    assert [i for i in provider.preflight("u1", complete, "live")
            if i["target"] == "etsy_return_policy" and i["level"] == "error"]

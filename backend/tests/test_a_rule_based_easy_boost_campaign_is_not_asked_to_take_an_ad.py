"""A one-campaign account whose single campaign is rule-based (eBay's Easy
boost, or a Seller Hub campaign that picks listings by rule) is told so in a
sentence, not shown eBay's refusal.

Such a campaign chooses its own listings; ads cannot be added to it one
listing at a time, and eBay refuses with errorId 35064, "This operation is
only supported for key based campaigns." Production put that envelope on the
seller's screen, verbatim, after promoting into "Easy boost 09/18/2026
12:40:09" -- and asked again, and was refused again, on every promoted
publish. getCampaigns marks a rule-based campaign with a campaignCriterion
container, which is checked before any ad is attempted; 35064 at ad time
(a campaign cached before that check existed) is the backstop. Either way the
verdict is remembered, so the next publish does not ask eBay again.
"""
from __future__ import annotations

import httpx
import pytest

from backend.models import Listing
from backend.services import promotions

BODY_35304 = ('{"errors":[{"errorId":35304,"domain":"API_MARKETING",'
              '"category":"BUSINESS","message":"Another campaign cannot be '
              'created at this time, as the user is currently using a feature '
              'that only supports one campaign."}]}')
BODY_35064 = ('{"errors":[{"errorId":35064,"domain":"API_MARKETING",'
              '"category":"BUSINESS","message":"This operation is only '
              'supported for key based campaigns.","parameters":[{"name":'
              '"campaignName","value":"Easy boost 09/18/2026 12:40:09"}]}]}')

EASY_BOOST = {
    "campaignId": "777", "campaignName": "Easy boost 09/18/2026 12:40:09",
    "campaignStatus": "RUNNING", "marketplaceId": "EBAY_US",
    "fundingStrategy": {"fundingModel": "COST_PER_SALE", "bidPercentage": "8.0"},
    "campaignCriterion": {"criterionType": "INVENTORY_PARTITION",
                          "autoSelectFutureInventory": True,
                          "selectionRules": [{}]},
}
KEY_BASED = {
    "campaignId": "902", "campaignName": "My promoted listings",
    "campaignStatus": "PAUSED", "marketplaceId": "EBAY_US",
    "fundingStrategy": {"fundingModel": "COST_PER_SALE"},
}


@pytest.fixture(autouse=True)
def _clean():
    for d in (promotions._CAMPAIGN_CACHE, promotions._CREATE_FAILED,
              promotions._RULE_BASED):
        d.clear()
    yield
    for d in (promotions._CAMPAIGN_CACHE, promotions._CREATE_FAILED,
              promotions._RULE_BASED):
        d.clear()


class _Client:
    """httpx.Client stand-in for a one-campaign account: creating our
    campaign is refused with 35304; adding an ad to the rule-based campaign
    is refused with 35064; the key-based one takes it."""
    calls: list = []
    campaigns: list = [EASY_BOOST]

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, **kw):
        _Client.calls.append(("GET", url, dict(params or {})))
        req = httpx.Request("GET", url)
        if (params or {}).get("campaign_name"):
            return httpx.Response(200, request=req, json={"campaigns": []})
        return httpx.Response(200, request=req, json={"campaigns": _Client.campaigns})

    def post(self, url, json=None, **kw):
        _Client.calls.append(("POST", url, json))
        req = httpx.Request("POST", url)
        if url.endswith("/ad_campaign"):
            return httpx.Response(409, request=req, text=BODY_35304)
        if "/ad_campaign/777/" in url:
            return httpx.Response(400, request=req, text=BODY_35064)
        if url.endswith("/ad_campaign/902/bulk_create_ads_by_listing_id"):
            return httpx.Response(200, request=req, json={"responses": [
                {"listingId": "110001", "statusCode": 201, "adId": "ad-1"}]})
        raise AssertionError(f"unexpected POST {url}")


def _promote():
    listing = Listing(title="Denim jacket", promote=True, ad_rate_percent=5.0,
                      ebay_listing_id="110001", source="ebay")
    return promotions.promote_listing(
        "s1", listing, {"access_token": "tok-aaaaaaaaaaaa", "_uid": "u1"})


def _ad_posts():
    return [c for c in _Client.calls if c[0] == "POST" and "/ad_campaign/" in c[1]]


def test_the_rule_based_campaign_is_recognised_before_any_ad_is_attempted(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls, _Client.campaigns = [], [EASY_BOOST]
    out = _promote()
    assert out["promoted"] is False
    assert _ad_posts() == [], _ad_posts()
    msg = out["message"]
    assert "{" not in msg and "35064" not in msg and "key based" not in msg
    assert "Easy boost 09/18/2026 12:40:09" in msg
    assert "Seller Hub" in msg


def test_the_verdict_is_remembered_so_the_next_publish_does_not_ask_ebay(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls, _Client.campaigns = [], [EASY_BOOST]
    first = _promote()
    _Client.calls = []
    again = _promote()
    assert again == first
    assert _Client.calls == []


def test_after_the_wait_the_account_is_looked_at_again(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls, _Client.campaigns = [], [EASY_BOOST]
    _promote()
    # The seller turned Easy boost off and started a Standard campaign.
    _Client.campaigns = [KEY_BASED]
    key = next(iter(promotions._RULE_BASED))
    when, name = promotions._RULE_BASED[key]
    promotions._RULE_BASED[key] = (when - promotions._RULE_BASED_TTL - 1, name)
    _Client.calls = []
    out = _promote()
    assert out["promoted"] is True, out
    assert "/ad_campaign/902/" in _ad_posts()[0][1]


def test_a_key_based_campaign_beside_the_rule_based_one_takes_the_ad(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls, _Client.campaigns = [], [EASY_BOOST, KEY_BASED]
    out = _promote()
    assert out["promoted"] is True, out
    assert all("/ad_campaign/902/" in c[1] for c in _ad_posts()), _ad_posts()


def test_ebays_35064_at_ad_time_is_the_backstop_for_a_campaign_cached_earlier(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls, _Client.campaigns = [], [EASY_BOOST]
    # Cached before campaignCriterion was looked at: the id is trusted as-is.
    promotions._CAMPAIGN_CACHE[promotions._cache_key("EBAY_US", "tok-aaaaaaaaaaaa")] = "777"
    out = _promote()
    assert out["promoted"] is False
    msg = out["message"]
    assert "{" not in msg and "35064" not in msg
    assert "Easy boost 09/18/2026 12:40:09" in msg
    # One refused create; no bid-update attempt on a campaign that takes no ads.
    assert [c[1].rsplit("/", 1)[-1] for c in _ad_posts()] == ["bulk_create_ads_by_listing_id"]
    _Client.calls = []
    assert _promote() == out
    assert _Client.calls == []

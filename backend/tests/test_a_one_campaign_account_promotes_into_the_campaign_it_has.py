"""A seller whose eBay account allows only one Promoted Listings campaign is
promoted into the campaign they already have, not refused.

Sellers who run Promoted Listings through Seller Hub's simplified set-up get
exactly one campaign, and eBay refuses to create another: 409, errorId 35304,
"Another campaign cannot be created at this time, as the user is currently
using a feature that only supports one campaign." Our own named campaign can
never exist there, so every promoted publish on such an account looked up our
campaign, failed to create it, and showed the seller "Couldn't start the
promotion: campaign create failed (409): {"errors":[...]}" -- eBay's JSON
envelope, verbatim -- while the listing went unpromoted.
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

SELLERS_CAMPAIGNS = {"campaigns": [
    {"campaignId": "900", "campaignName": "Old sale", "campaignStatus": "ENDED",
     "fundingStrategy": {"fundingModel": "COST_PER_SALE"}, "marketplaceId": "EBAY_US"},
    {"campaignId": "901", "campaignName": "Clicks", "campaignStatus": "RUNNING",
     "fundingStrategy": {"fundingModel": "COST_PER_CLICK"}, "marketplaceId": "EBAY_US"},
    {"campaignId": "902", "campaignName": "My promoted listings",
     "campaignStatus": "RUNNING",
     "fundingStrategy": {"fundingModel": "COST_PER_SALE"}, "marketplaceId": "EBAY_US"},
]}


@pytest.fixture(autouse=True)
def _clean():
    promotions._CAMPAIGN_CACHE.clear()
    promotions._CREATE_FAILED.clear()
    yield
    promotions._CAMPAIGN_CACHE.clear()
    promotions._CREATE_FAILED.clear()


class _Client:
    """httpx.Client stand-in for a one-campaign account: our named campaign
    is not there, creating one is refused with 35304, and the account's own
    campaigns are what they are."""
    calls: list = []
    campaigns: dict = SELLERS_CAMPAIGNS

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
        return httpx.Response(200, request=req, json=_Client.campaigns)

    def post(self, url, json=None, **kw):
        _Client.calls.append(("POST", url, json))
        req = httpx.Request("POST", url)
        if url.endswith("/ad_campaign"):
            return httpx.Response(409, request=req, text=BODY_35304)
        if url.endswith("/bulk_create_ads_by_listing_id"):
            return httpx.Response(200, request=req, json={"responses": [
                {"listingId": "110001", "statusCode": 201, "adId": "ad-1"}]})
        raise AssertionError(f"unexpected POST {url}")


def _promote():
    listing = Listing(title="Denim jacket", promote=True, ad_rate_percent=5.0,
                      ebay_listing_id="110001", source="ebay")
    return promotions.promote_listing(
        "s1", listing, {"access_token": "tok-aaaaaaaaaaaa", "_uid": "u1"})


def test_the_ad_goes_into_the_running_cost_per_sale_campaign(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls = []
    _Client.campaigns = SELLERS_CAMPAIGNS
    out = _promote()
    assert out["promoted"] is True, out
    ad_posts = [c for c in _Client.calls if c[1].endswith("/bulk_create_ads_by_listing_id")]
    assert len(ad_posts) == 1
    assert "/ad_campaign/902/" in ad_posts[0][1]


def test_the_campaign_is_remembered_so_the_next_publish_does_not_ask_again(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls = []
    _Client.campaigns = SELLERS_CAMPAIGNS
    _promote()
    _Client.calls = []
    out = _promote()
    assert out["promoted"] is True
    assert [c[0] for c in _Client.calls] == ["POST"], _Client.calls
    assert _Client.calls[0][1].endswith("/ad_campaign/902/bulk_create_ads_by_listing_id")


def test_with_no_usable_campaign_the_seller_reads_a_sentence(monkeypatch):
    monkeypatch.setattr(promotions.httpx, "Client", _Client)
    _Client.calls = []
    _Client.campaigns = {"campaigns": SELLERS_CAMPAIGNS["campaigns"][:2]}
    out = _promote()
    assert out["promoted"] is False
    assert "{" not in out["message"] and "35304" not in out["message"]
    assert "Seller Hub" in out["message"]
    assert not any(c[1].endswith("/bulk_create_ads_by_listing_id") for c in _Client.calls)

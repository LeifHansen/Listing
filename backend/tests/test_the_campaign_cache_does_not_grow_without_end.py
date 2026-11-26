"""The promotions campaign cache stays bounded on a machine that never stops.

It is keyed by the tail of the access token, and access tokens are replaced
about every two hours -- so each promoting seller added a fresh entry every
couple of hours, and nothing ever took one out. The entries are small, but a
machine that stays up for weeks only ever grew it.
"""
from __future__ import annotations

from backend.services import promotions


class _Resp:
    status_code = 200

    def json(self):
        return {"campaigns": [{"campaignName": promotions.CAMPAIGN_NAME,
                               "campaignId": "555"}]}


class _Client:
    def get(self, *_a, **_k):
        return _Resp()


def test_a_new_token_every_two_hours_does_not_grow_the_cache_forever():
    promotions._CAMPAIGN_CACHE.clear()
    try:
        for n in range(3 * promotions._CAMPAIGN_CACHE_MAX):
            token = f"token-{n:012d}"
            assert promotions._ensure_campaign(
                _Client(), "https://api", token, "EBAY_US") == "555"
        assert len(promotions._CAMPAIGN_CACHE) <= promotions._CAMPAIGN_CACHE_MAX
        # ...and the newest answer is still the one that is remembered.
        newest = promotions._cache_key("EBAY_US", token)
        assert promotions._CAMPAIGN_CACHE[newest] == "555"
    finally:
        promotions._CAMPAIGN_CACHE.clear()

"""Every request to Etsy identifies the app as `keystring:sharedSecret`.

Since 2026-02-09 Etsy refuses an x-api-key header that carries the keystring
alone (etsy/open-api discussion #1529). The header used to be built in two
places, so the point of this file is less "the value is right" than "there is
one place, and every call goes through it": the token exchange, the seller
lookups, the listing writes, the image upload and the unauthenticated
taxonomy read all appear here, and a new Etsy call that spells its own
header would be the one this test does not see.
"""
import httpx
import pytest

from backend import config, etsy_auth
from backend.services import etsy as etsy_service


class _Resp:
    status_code = 200
    text = "{}"

    def __init__(self, body: dict):
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._body


@pytest.fixture
def sent(monkeypatch):
    """Every outbound Etsy call, as (method, url, headers), with a canned
    answer that satisfies whichever caller made it."""
    monkeypatch.setattr(config, "ETSY_CLIENT_ID", "key123")
    monkeypatch.setattr(config, "ETSY_SHARED_SECRET", "s3cret")
    calls: list[tuple[str, str, dict]] = []
    body = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600,
            "listing_id": 7, "url": "", "state": "draft",
            "shop_id": 1, "user_id": 2, "results": []}

    def _fake(method):
        def _call(url, *args, **kwargs):
            calls.append((method, url, dict(kwargs.get("headers") or {})))
            return _Resp(body)
        return _call

    for method in ("get", "post", "patch", "put", "delete"):
        monkeypatch.setattr(httpx, method, _fake(method))
    etsy_service._TAXONOMY_CACHE.update(at=0.0, nodes=None)
    return calls


def test_every_etsy_call_carries_the_keystring_and_secret(sent):
    etsy_auth.exchange_code("code", "verifier")
    etsy_auth.refresh_access_token("rt")
    etsy_auth.fetch_me("tok")
    etsy_auth.fetch_shop("tok", "1")
    etsy_auth.list_shipping_profiles("tok", "1")
    etsy_auth.list_return_policies("tok", "1")
    etsy_service.create_draft_listing("tok", "1", {"title": "x"})
    etsy_service.update_listing("tok", "1", "7", {"state": "active"})
    etsy_service.get_listing("tok", "7")
    etsy_service.upload_listing_image("tok", "1", "7", b"jpg", "a.jpg", 1)
    etsy_service.taxonomy_nodes()

    assert len(sent) == 11
    for method, url, headers in sent:
        assert headers.get("x-api-key") == "key123:s3cret", (method, url, headers)
    # The token endpoint and the taxonomy read are the app's own calls; every
    # other one is made for a seller and carries their bearer token too.
    bearers = [h.get("Authorization") for _, url, h in sent
               if "oauth/token" not in url and "seller-taxonomy" not in url]
    assert bearers and all(b == "Bearer tok" for b in bearers)


def test_without_the_secret_etsy_is_not_offered_and_says_which_variable(monkeypatch):
    monkeypatch.setattr(config, "ETSY_CLIENT_ID", "key123")
    monkeypatch.setattr(config, "ETSY_SHARED_SECRET", "")
    monkeypatch.setattr(config, "ETSY_REDIRECT_URI", "https://app.example/api/etsy/callback")
    # Imported here, not at the top: importing the provider module registers
    # it, and doing that before the registry's own load would put Etsy ahead
    # of eBay for every test that runs after this file.
    from backend.marketplaces.etsy_provider import EtsyProvider
    provider = EtsyProvider()
    assert provider.oauth_ready() is False
    assert provider.oauth_missing() == ["ETSY_SHARED_SECRET"]
    # A developer's dry run still gets a header, never "key123:".
    assert config.etsy_api_key() == "key123"

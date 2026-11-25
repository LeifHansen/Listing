"""The account-deletion endpoint does not fetch whatever key id it is sent.

That endpoint is public and unauthenticated, and the key id (`kid`) comes out
of the very header being checked. It went into the path of a request to
eBay's API, made with the app's own token, before anything was verified --
unescaped, so "../" or "?" in it steered that request elsewhere on eBay's
API, and every forged notice cost an outbound call against the app's quota.
A kid that is not a plain token is now refused as unsigned, and one that is
goes into the path escaped.
"""
from __future__ import annotations

import base64
import json

import pytest

from backend.services import ebay_notify


def _header(kid: str) -> str:
    body = {"alg": "ecdsa", "kid": kid, "digest": "SHA1",
            "signature": base64.b64encode(b"not-a-signature").decode()}
    return base64.b64encode(json.dumps(body).encode()).decode()


@pytest.mark.parametrize("kid", [
    "../../sell/inventory/v1/location",
    "abc?x=1",
    "abc#frag",
    "a/b",
    "%2e%2e",
    "k" * 200,
])
def test_a_kid_that_is_not_a_plain_token_is_refused_without_a_call(monkeypatch, kid):
    fetched = []
    monkeypatch.setattr(ebay_notify, "_fetch_public_key",
                        lambda k: fetched.append(k))
    assert ebay_notify.verify(b'{"notification":{}}', _header(kid)) is False
    assert fetched == []


def test_a_plain_kid_is_fetched_escaped(monkeypatch):
    seen = {}

    class _Resp:
        is_success = False
        status_code = 404

    def _get(url, **_kw):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(ebay_notify, "_app_token", lambda: "tok")
    monkeypatch.setattr(ebay_notify.httpx, "get", _get)
    ebay_notify._KEY_CACHE.clear()
    kid = "99b6f7d1-2c4f-4f3a-9d6e-0a1b2c3d4e5f"
    with pytest.raises(ebay_notify.KeyUnavailable):
        ebay_notify.verify(b'{"notification":{}}', _header(kid))
    assert seen["url"].endswith(f"/commerce/notification/v1/public_key/{kid}")

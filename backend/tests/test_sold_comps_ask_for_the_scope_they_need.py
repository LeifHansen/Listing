"""Sold comps ask eBay for the token scope they need -- on a token of their own.

Marketplace Insights only answers a client-credentials token that was MINTED
with its scope (buy.marketplace.insights). The app had one application token,
asked for the base api_scope alone, and used it for everything -- so even an
application eBay had approved got a 403, latched sold prices off for the
process, and logged that it was "not approved".

The fix is a SEPARATE token for that scope, cached apart from the base one.
The base token must not change: asking for a scope the application has not
been granted fails the whole token request, which would take taxonomy,
active comps and the notification check down with it.
"""
from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

from backend import config  # noqa: E402
from backend.services import pricing, taxonomy  # noqa: E402

BASE = "https://api.ebay.com/oauth/api_scope"
INSIGHTS = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.request = httpx.Request("POST", "https://api.ebay.test/token")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "refused", request=self.request,
                response=httpx.Response(self.status_code, request=self.request))


@pytest.fixture()
def ebay(monkeypatch):
    """A token endpoint that records which scope each request asked for and
    mints a token named after it."""
    monkeypatch.setattr(taxonomy, "_token_cache",
                        {"token": None, "expires_at": 0.0})
    monkeypatch.setattr(taxonomy, "_scoped_tokens", {}, raising=False)
    monkeypatch.setattr(config, "taxonomy_ready", lambda: True)
    pricing.reset_insights_latch()
    asked: list[str] = []
    refuse: set[str] = set()

    def _post(url, data=None, **kw):
        scope = (data or {}).get("scope", "")
        asked.append(scope)
        if scope in refuse:
            return _Resp(400, {"error": "invalid_scope"})
        name = "insights-token" if scope == INSIGHTS else "base-token"
        return _Resp(200, {"access_token": name, "expires_in": 7200})

    monkeypatch.setattr(taxonomy.httpx, "post", _post)
    yield asked, refuse
    pricing.reset_insights_latch()


def test_the_base_token_still_asks_for_the_base_scope_only(ebay):
    asked, _ = ebay
    assert taxonomy._app_token() == "base-token"
    assert asked == [BASE]


def test_the_insights_token_is_its_own_and_is_cached_apart(ebay):
    asked, _ = ebay
    assert taxonomy._app_token(INSIGHTS) == "insights-token"
    assert taxonomy._app_token() == "base-token"
    assert taxonomy._app_token(INSIGHTS) == "insights-token"
    assert taxonomy._app_token() == "base-token"
    assert asked == [INSIGHTS, BASE]


def test_sold_comps_are_asked_for_with_the_scoped_token(ebay, monkeypatch):
    sent = {}

    def _get(url, headers=None, **kw):
        sent["auth"] = (headers or {}).get("Authorization")
        return _Resp(200, {"itemSales": []})

    monkeypatch.setattr(pricing.httpx, "get", _get)
    assert pricing.sold_comps("chagall lithograph") is None
    assert sent["auth"] == "Bearer insights-token"


def test_an_ungranted_scope_is_not_approved_and_spares_the_base_token(
        ebay, monkeypatch):
    """eBay refuses the token request itself for a scope it has not granted.
    That is the same answer as the 403 -- not switched on -- and it latches
    the same way, while the base token goes on working."""
    asked, refuse = ebay
    refuse.add(INSIGHTS)
    monkeypatch.setattr(pricing.httpx, "get",
                        lambda *a, **k: pytest.fail("no search without a token"))
    with pytest.raises(pricing.InsightsNotApproved):
        pricing.sold_comps("chagall lithograph")
    assert pricing.insights_enabled() is False
    assert pricing.sold_comps("again") is None
    assert asked.count(INSIGHTS) == 1
    assert taxonomy._app_token() == "base-token"

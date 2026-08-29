"""A failed eBay connect has to say something.

Connecting is the one flow a seller cannot debug from the UI: it leaves the
app, happens on eBay's domain, and comes back as a redirect. Until now every
way it could fail — a rotated SECRET_KEY, a stale link, wrong app credentials,
an app pointed at sandbox while the seller signs in to production eBay, an
eBay outage — arrived as the identical sentence "eBay connection failed.
Please try again.", which is advice that cannot work for most of them.

eBay says which it was, in the body of the token response. `raise_for_status()`
threw that body away before anyone could read it.
"""
from __future__ import annotations

import httpx
import pytest

from backend import ebay_auth


def _response(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body,
                          request=httpx.Request("POST", "https://api.ebay.com/x"))


# --- eBay's reason survives -------------------------------------------------

def test_the_refusal_carries_ebays_own_words():
    """Fails against the old code, which raised HTTPStatusError and kept only
    the status number."""
    err = ebay_auth._oauth_error(_response(400, {
        "error": "invalid_grant",
        "error_description": "the provided authorization grant code is invalid"}))
    assert err.code == "invalid_grant"
    assert "authorization grant code is invalid" in err.description
    assert err.status == 400


def test_a_body_that_is_not_json_still_produces_an_error():
    """eBay's gateway can answer HTML. That must not turn a refusal into a
    JSONDecodeError from inside the handler."""
    resp = httpx.Response(502, text="<html>bad gateway</html>",
                          request=httpx.Request("POST", "https://api.ebay.com/x"))
    err = ebay_auth._oauth_error(resp)
    assert err.status == 502 and err.code == "" and err.description == ""


def test_a_long_description_is_bounded():
    """It reaches a log line; eBay is not the one deciding how long that is."""
    err = ebay_auth._oauth_error(_response(400, {
        "error": "invalid_grant", "error_description": "x" * 5000}))
    assert len(err.description) <= 300


# --- the bucket the seller is shown ----------------------------------------

@pytest.mark.parametrize("code", ["invalid_client", "unauthorized_client"])
def test_bad_app_credentials_are_not_the_sellers_to_fix(code):
    """Retrying cannot help. This is also what a sandbox/production mismatch
    looks like, which is the most common cause of a connect that never sticks."""
    assert ebay_auth.OAuthError("x", code=code).reason == "config"


def test_a_used_or_expired_code_tells_them_to_start_again():
    assert ebay_auth.OAuthError("x", code="invalid_grant").reason == "expired"


@pytest.mark.parametrize("code", ["", "server_error", "something_new"])
def test_anything_unrecognised_falls_back_rather_than_guessing(code):
    assert ebay_auth.OAuthError("x", code=code).reason == "unknown"


# --- the request path raises it --------------------------------------------

def test_a_refused_token_request_raises_the_typed_error(monkeypatch):
    monkeypatch.setattr(ebay_auth.httpx, "post",
                        lambda *a, **k: _response(400, {
                            "error": "invalid_client",
                            "error_description": "client authentication failed"}))
    with pytest.raises(ebay_auth.OAuthError) as caught:
        ebay_auth._token_request({"grant_type": "authorization_code"})
    assert caught.value.reason == "config"


def test_a_successful_token_request_is_unchanged(monkeypatch):
    monkeypatch.setattr(ebay_auth.httpx, "post",
                        lambda *a, **k: _response(200, {
                            "access_token": "tok", "refresh_token": "r",
                            "expires_in": 7200}))
    assert ebay_auth._token_request({})["access_token"] == "tok"


def test_exchange_code_surfaces_the_reason(monkeypatch):
    """The call the callback actually makes."""
    monkeypatch.setattr(ebay_auth.httpx, "post",
                        lambda *a, **k: _response(400, {"error": "invalid_grant"}))
    with pytest.raises(ebay_auth.OAuthError) as caught:
        ebay_auth.exchange_code("a-code")
    assert caught.value.reason == "expired"


# --- eBay must ask WHICH account ------------------------------------------

def test_the_authorize_url_forces_a_login():
    """Without prompt=login, eBay reuses whatever session the browser already
    has and silently reconnects THAT account.

    Reported from production: a seller unlinked one store, pressed Connect for
    a second one, "was not walked through the eBay approval steps" at all, and
    ended up reconnected to the first account — whose listings then filled the
    app and whose account-level block failed every publish. There is no screen
    anywhere in that sequence that names the account, so nothing contradicts
    the seller's belief that they switched.

    eBay documents `prompt=login` for exactly this. It costs a returning seller
    one login screen.
    """
    from urllib.parse import parse_qs, urlparse

    url = ebay_auth.authorize_url(state="s")
    assert parse_qs(urlparse(url).query)["prompt"] == ["login"]


def test_the_authorize_url_still_carries_the_rest():
    """Guarding the addition: scopes, RuName, state and response_type are what
    make the callback work at all."""
    from urllib.parse import parse_qs, urlparse

    # keep_blank_values: EBAY_RUNAME is empty in the test environment, and
    # parse_qs drops blank values by default — which would read as "the
    # parameter is missing" rather than "it is unset here".
    q = parse_qs(urlparse(ebay_auth.authorize_url(state="abc")).query,
                 keep_blank_values=True)
    assert q["state"] == ["abc"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == [ebay_auth.config.EBAY_RUNAME]
    assert "sell.account" in q["scope"][0]

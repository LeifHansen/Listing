"""A connect started by a ticket stops and names the account it will join.

A connect ticket names the account that MINTED it, and anyone signed in can
mint one. Opened in a browser that is not signed in here, a link carrying an
attacker's ticket went straight to eBay's consent screen -- and if the person
holding it approved, their eBay store was attached to the ATTACKER's Thryft
account, which could then list, revise and end items on it. The nonce cookie
the callback checks did not help: it was set in the victim's own browser.

So a ticket-started flow now stops at a page that names the destination
account, and only that page's own "Continue" link goes on. The link carries a
confirm token bound to a nonce in a SameSite=Strict cookie the page sets:
anyone can mint a confirm token for their own account, but only a browser that
was shown this page holds the cookie that makes it work, and a Strict cookie
never rides a navigation another site starts.

A session is unchanged: it can only name its own account, so it goes straight
to eBay as before.
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import auth, main, ratelimit  # noqa: E402

EBAY_CONSENT = "https://auth.ebay.example/oauth2/authorize"


@pytest.fixture
def app(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.deps, "db", dbmod)
    monkeypatch.setattr(main.config, "ebay_oauth_ready", lambda: True)
    monkeypatch.setattr(main.config, "OAUTH_ORIGIN", "")
    monkeypatch.setattr(main.ebay_auth, "authorize_url",
                        lambda state: f"{EBAY_CONSENT}?state={state}")
    ratelimit.reset()
    signup = TestClient(main.app)
    assert signup.post("/api/auth/signup", json={
        "email": "connector@example.com",
        "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("connector@example.com")["id"]
    # A browser with no session here: the native shell's own navigation, or
    # someone who was sent the link.
    return uid, (lambda: TestClient(main.app, follow_redirects=False))


def _continue_link(page: str) -> str:
    (href,) = re.findall(r'<a href="([^"]+)"[^>]*>Continue to', page)
    return unescape(href)


def test_a_ticket_stops_at_a_page_naming_the_account(app):
    uid, browser = app
    b = browser()

    res = b.get("/api/ebay/connect",
                params={"ticket": auth.make_ticket(uid, "connect")})

    assert res.status_code == 200
    assert not res.headers.get("location"), "went to eBay without asking"
    page = res.text
    assert "co•••@example.com" in page
    assert "connector@example.com" not in page, "the whole address is shown"
    assert "Continue to eBay" in page
    assert res.headers["cache-control"] == "no-store"
    cookie = res.headers["set-cookie"].lower()
    assert main.CONNECT_CONFIRM_COOKIE in cookie and "samesite=strict" in cookie
    assert "httponly" in cookie


def test_continuing_from_that_page_goes_on_to_ebay(app):
    uid, browser = app
    b = browser()
    page = b.get("/api/ebay/connect",
                 params={"ticket": auth.make_ticket(uid, "connect")}).text

    res = b.get(_continue_link(page))

    assert res.status_code in (302, 307)
    assert res.headers["location"].startswith(EBAY_CONSENT)
    # ...for THIS account, with the callback's own nonce bound to the browser.
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
    assert auth.verify_state(state)[0] == uid
    assert main.EBAY_NONCE_COOKIE in res.headers["set-cookie"]


def test_the_continue_link_does_nothing_in_another_browser(app):
    """Linking past the page: the confirm URL opened by a browser that was
    never shown it (or a navigation from another site, which does not carry
    the Strict cookie) is refused."""
    uid, browser = app
    page = browser().get("/api/ebay/connect",
                         params={"ticket": auth.make_ticket(uid, "connect")}).text

    res = browser().get(_continue_link(page))

    assert res.status_code == 403
    assert not res.headers.get("location")


def test_an_attackers_own_confirm_link_does_not_match_the_victims_page(app):
    """The attacker can mint a confirm token for their own account, and even
    get the victim's browser to load a confirmation page -- but the nonce in
    that browser's cookie is one the attacker cannot know."""
    uid, browser = app
    victim = browser()
    victim.get("/api/ebay/connect",
               params={"ticket": auth.make_ticket(uid, "connect")})
    forged = auth.make_ticket(uid, "connect-confirm:attackers-own-nonce",
                              ttl_seconds=600)

    res = victim.get("/api/ebay/connect", params={"confirm": forged})

    assert res.status_code == 403
    assert not res.headers.get("location")


def test_an_expired_confirmation_is_refused(app):
    """The token's own clock: the browser still holds the page's cookie, and
    the token carries that very nonce -- but it is past its ten minutes."""
    uid, browser = app
    b = browser()
    b.get("/api/ebay/connect", params={"ticket": auth.make_ticket(uid, "connect")})
    nonce = b.cookies.get(main.CONNECT_CONFIRM_COOKIE)
    assert nonce
    stale = auth.make_ticket(uid, f"connect-confirm:{nonce}", ttl_seconds=-5)

    res = b.get("/api/ebay/connect", params={"confirm": stale})

    assert res.status_code == 403
    assert not res.headers.get("location")


def test_a_session_still_goes_straight_to_ebay(app):
    """A session can only name its own account, so there is nothing to ask."""
    uid, browser = app
    b = browser()
    assert b.post("/api/auth/login", json={
        "email": "connector@example.com",
        "password": "password123"}).status_code == 200

    res = b.get("/api/ebay/connect")

    assert res.status_code in (302, 307)
    assert res.headers["location"].startswith(EBAY_CONSENT)


def test_the_native_flag_rides_through_and_cancel_goes_back_to_the_app(app):
    uid, browser = app
    b = browser()
    page = b.get("/api/ebay/connect", params={
        "ticket": auth.make_ticket(uid, "connect"), "native": "1",
        "return_to": "https://evil.example"}).text

    link = _continue_link(page)
    assert parse_qs(urlparse(link).query)["native"] == ["1"]
    assert "return_to" not in parse_qs(urlparse(link).query), \
        "an origin this app does not serve is not carried forward"
    assert f'href="{main.config.NATIVE_APP_ORIGIN}/"' in page


def test_a_confirm_token_is_neither_a_session_nor_a_connect_ticket(app):
    uid, _browser = app
    confirm = auth.make_ticket(uid, "connect-confirm:n", ttl_seconds=600)
    assert auth.verify_ticket(confirm, "connect") is None
    from starlette.requests import Request
    req = Request({"type": "http", "method": "GET", "path": "/",
                   "query_string": b"",
                   "headers": [(b"authorization", f"Bearer {confirm}".encode())]})
    assert auth.current_user(req) is None


def test_the_other_marketplaces_ask_too(app, every_marketplace, monkeypatch):
    """Etsy and Depop share the generic route, and the same hole."""
    from backend.marketplaces.depop_provider import DepopProvider
    monkeypatch.setattr(DepopProvider, "oauth_ready", lambda self: True)
    monkeypatch.setattr(DepopProvider, "authorize_url",
                        lambda self, state: ("https://depop.example/auth", {}))
    uid, browser = app
    b = browser()

    res = b.get("/api/depop/connect",
                params={"ticket": auth.make_ticket(uid, "connect")})

    assert res.status_code == 200 and "Continue to Depop" in res.text
    onward = b.get(_continue_link(res.text))
    assert onward.headers["location"].startswith("https://depop.example/auth")

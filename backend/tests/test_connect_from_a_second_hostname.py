"""A connect flow started on one of the app's hostnames finishes on it.

The app answers on app.thryftshop.com and on its .fly.dev host, but every
marketplace sends the seller back to ONE registered callback URL: eBay
resolves a RuName to a single accepted URL, Etsy and Depop match redirect_uri
exactly. The CSRF nonce cookie set when a flow starts is host-only, so a
connect begun on the hostname that is NOT registered used to arrive at the
callback with no cookie, get rejected as "expired", and strand the seller on a
hostname where their session cookie did not exist either — logged out, on a
domain they never chose, told it was their fault.

So the flow is moved to the registered origin before it starts, and the seller
is put back where they came from at the end. The return trip is the dangerous
half: it builds a redirect out of a value that arrived in a URL, so every test
here that matters is about refusing an origin this app does not serve.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from starlette.requests import Request

from backend import auth, main

APP = "https://app.thryftshop.com"
FLY = "https://listing-lfwjrg.fly.dev"
BOTH = (APP, FLY)


def req(origin: str, **cookies: str) -> Request:
    scheme, host = origin.split("://", 1)
    headers = [(b"host", host.encode())]
    if cookies:
        jar = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append((b"cookie", jar.encode()))
    return Request({"type": "http", "method": "GET", "path": "/",
                    "root_path": "", "scheme": scheme, "query_string": b"",
                    "headers": headers, "server": (host, 443)})


@pytest.fixture()
def two_origins(monkeypatch):
    monkeypatch.setattr(main.config, "OAUTH_ORIGIN", FLY)
    monkeypatch.setattr(main.config, "APP_ORIGINS", BOTH)


# --- starting the flow ------------------------------------------------------

def test_a_connect_on_the_unregistered_host_moves_to_the_registered_one(two_origins):
    out = main._offsite_connect(req(APP), "user-1", "/api/ebay/connect", "")
    assert out is not None
    assert out.headers["location"].startswith(f"{FLY}/api/ebay/connect?")
    # The session cookie is host-only and cannot make the hop, so the bounce
    # has to carry its own proof of who this is.
    assert "ticket=" in out.headers["location"]
    assert "return_to=https%3A%2F%2Fapp.thryftshop.com" in out.headers["location"]


def test_the_ticket_on_the_hop_is_a_connect_ticket_and_nothing_more():
    ticket = auth.make_ticket("user-1", "connect")
    assert auth.verify_ticket(ticket, "connect") == "user-1"
    assert auth.verify_ticket(ticket, "delete-account") is None


def test_a_flow_already_on_the_registered_host_is_left_alone(two_origins):
    assert main._offsite_connect(req(FLY), "user-1", "/api/ebay/connect", "") is None


def test_an_unrecognised_host_is_never_bounced(two_origins):
    """Fly forwards whatever Host it is handed, so a hostname this app does not
    claim gets today's behaviour — not a redirect built from it."""
    assert main._offsite_connect(
        req("https://evil.example"), "user-1", "/api/ebay/connect", "") is None


def test_with_no_oauth_origin_configured_nothing_changes(monkeypatch):
    """The single-origin setup every self-hoster and local dev runs."""
    monkeypatch.setattr(main.config, "OAUTH_ORIGIN", "")
    monkeypatch.setattr(main.config, "APP_ORIGINS", ())
    assert main._offsite_connect(req(APP), "user-1", "/api/ebay/connect", "") is None


def test_the_native_flag_survives_the_hop(two_origins):
    out = main._offsite_connect(req(APP), "user-1", "/api/ebay/connect", "1")
    assert "native=1" in out.headers["location"]


# --- remembering where to go back to ----------------------------------------

class _Resp:
    def __init__(self):
        self.cookies = {}

    def set_cookie(self, key, value, **kw):
        self.cookies[key] = value


def test_the_origin_is_recorded_only_if_this_app_serves_it(two_origins):
    resp = _Resp()
    main._mark_return_origin(resp, req(FLY), APP)
    assert resp.cookies[main.RETURN_ORIGIN_COOKIE] == APP


def test_an_origin_we_do_not_serve_is_refused(two_origins):
    """The open-redirect guard. return_to arrives in a URL anyone can type."""
    for hostile in ("https://evil.example",
                    "https://app.thryftshop.com.evil.example",
                    "//evil.example",
                    "javascript:alert(1)"):
        resp = _Resp()
        main._mark_return_origin(resp, req(FLY), hostile)
        assert resp.cookies == {}, hostile


# --- finishing the flow -----------------------------------------------------

def test_the_seller_lands_back_on_the_host_they_started_on(two_origins):
    out = main._finish_connect(
        req(FLY, **{main.RETURN_ORIGIN_COOKIE: APP}), "/?ebay=connected")
    assert out.headers["location"] == f"{APP}/?ebay=connected"


def test_a_forged_return_cookie_is_ignored(two_origins):
    """Checked again on the way out: a cookie is the one value here the browser
    could have been handed somewhere else."""
    out = main._finish_connect(
        req(FLY, **{main.RETURN_ORIGIN_COOKIE: "https://evil.example"}),
        "/?ebay=connected")
    assert out.headers["location"] == "/?ebay=connected"


def test_without_the_cookie_the_redirect_stays_relative(two_origins):
    out = main._finish_connect(req(FLY), "/?ebay=connected")
    assert out.headers["location"] == "/?ebay=connected"


def test_the_native_shell_still_wins(two_origins):
    """A flow from the iOS/Android shell goes back into the app, not to a
    website origin, however it was routed on the way in."""
    out = main._finish_connect(
        req(FLY, **{main.NATIVE_RETURN_COOKIE: "app",
                    main.RETURN_ORIGIN_COOKIE: APP}), "/?ebay=connected")
    assert out.status_code == 200
    assert main.config.NATIVE_APP_ORIGIN in out.body.decode()

"""A seller should not be shown eBay's HTTP response.

`GET /api/ebay/payments-status` answered with the deployment's eBay
environment, a raw HTTP status code, and eBay's ENTIRE response body — and
Settings put all three straight into a toast:

    Couldn't verify payments setup (production): eBay API error: 500
    {"errors":[{"errorId":20403,"domain":"ACCESS", ...}]}

Three separate problems in one line. `production` is deployment configuration,
the same class of leak that was just taken out of /api/health. The status code
and the JSON are not something a seller can act on — they cannot retry a 500
into working, and nothing tells them whether the answer is "wait", "finish
your bank setup", or "reconnect". And the body is unbounded: whatever eBay
chooses to put in it goes to the browser.

What replaces it is a small set of states the product can actually mean:

    ready               payouts are set up; publishing will work
    action_required     eBay answered, and the seller has something to finish
    reconnect_required  the connection is the problem, not the account
    unavailable         eBay could not be asked; try again shortly
    contact_support     none of the above, with a reference to quote

The raw detail is not destroyed — it goes to the logs under a short reference
that comes back to the seller, so support can join the two without the seller
carrying eBay's JSON around.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

import httpx
from fastapi.testclient import TestClient


class _Resp:
    def __init__(self, status, text='{"errors":[{"errorId":20403}]}'):
        self.status_code = status
        self.text = text


@pytest.fixture()
def api(monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda _r: {"access_token": "tok", "_uid": "u1"})

    def _answer(value):
        def _fetch(_token):
            if isinstance(value, Exception):
                raise value
            return value
        monkeypatch.setattr(main.ebay_auth, "fetch_payments_program", _fetch)
        return TestClient(main.app)
    return _answer


def _http_error(status):
    return httpx.HTTPStatusError(
        "boom", request=httpx.Request("GET", "https://api.ebay.com/x"),
        response=httpx.Response(status, text='{"errors":[{"errorId":20403}]}'))


# ------------------------------------------------------------ the states

def test_a_set_up_account_reads_as_ready(api):
    body = api({"status": "OPTED_IN"}).get("/api/ebay/payments-status").json()

    assert body["state"] == "ready"
    assert body["opted_in"] is True


def test_an_account_still_being_set_up_asks_for_the_next_step(api):
    body = api({"status": "NOT_OPTED_IN"}).get("/api/ebay/payments-status").json()

    assert body["state"] == "action_required"
    assert body["opted_in"] is False
    assert "seller hub" in body["message"].lower(), body["message"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_reads_as_reconnect_required(api, status):
    """"Reconnect eBay" is a different button from "finish payout setup", and
    sending a seller to the wrong one wastes a support round trip."""
    body = api(_http_error(status)).get("/api/ebay/payments-status").json()

    assert body["state"] == "reconnect_required"
    assert "reconnect" in body["message"].lower()


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_eBay_being_unreachable_reads_as_unavailable(api, status):
    body = api(_http_error(status)).get("/api/ebay/payments-status").json()

    assert body["state"] == "unavailable"
    assert body["opted_in"] is False


def test_a_network_failure_is_unavailable_too(api):
    body = api(httpx.ConnectError("no route")).get(
        "/api/ebay/payments-status").json()
    assert body["state"] == "unavailable"


def test_anything_else_points_at_support_with_something_to_quote(api):
    body = api(_http_error(418)).get("/api/ebay/payments-status").json()

    assert body["state"] == "contact_support"
    assert body["reference"], "nothing to quote to support"


# ------------------------------------------------- and nothing leaks

@pytest.mark.parametrize("answer", [
    {"status": "OPTED_IN"}, {"status": "NOT_OPTED_IN"},
])
def test_the_deployment_environment_is_not_published(api, answer):
    """`production` / `sandbox` is deployment configuration, and this route is
    reachable by any signed-in seller."""
    text = api(answer).get("/api/ebay/payments-status").text
    assert "sandbox" not in text.lower()
    assert "production" not in text.lower()
    assert "env" not in text.lower()


def test_ebays_response_body_does_not_reach_the_browser(api):
    resp = api(_http_error(500)).get("/api/ebay/payments-status")

    assert "errorId" not in resp.text, "eBay's raw response body was returned"
    assert "20403" not in resp.text
    assert "500" not in resp.text, "a raw HTTP status was returned"


def test_the_reference_is_short_enough_to_read_out(api):
    body = api(_http_error(500)).get("/api/ebay/payments-status").json()
    assert 4 <= len(body["reference"]) <= 16


def test_the_raw_detail_still_reaches_the_logs(api, caplog):
    """Mapping to a product state must not throw the evidence away — it is the
    only thing that makes the reference worth quoting."""
    import logging

    with caplog.at_level(logging.WARNING):
        body = api(_http_error(500)).get("/api/ebay/payments-status").json()

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert body["reference"] in logged
    assert "errorId" in logged or "20403" in logged

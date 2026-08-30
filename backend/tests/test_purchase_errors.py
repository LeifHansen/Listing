"""Stripe's own words are not something to show a seller.

`POST /api/tokens/checkout` answered a failure with `Couldn't start the
purchase: {exc}`, and `exc` is whatever came back from Stripe:

    Couldn't start the purchase: Stripe error: Invalid API Key provided:
    sk_live_********************************

    Couldn't start the purchase: Stripe error: No such price: price_1ABC…

A misconfigured key, a deleted price, a network error naming an internal URL —
none of it is something a seller can act on, one of them is a fragment of a
live secret, and all of it appears in a toast on a screen where they were
trying to give us money. `/api/tokens/confirm` returned the exception text
verbatim.

Same rule the payments check now follows (P2-07): a product state and a
sentence, with the raw detail in the log under a reference the seller can
quote. There is one thing worth SAYING here that the log cannot: whether they
have been charged.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

# What Stripe actually says when the key is wrong, with the key itself
# assembled at runtime rather than written out. A literal that LOOKS like a
# Stripe key is one GitHub's push protection rejects — correctly, since it
# cannot tell a fixture from the real thing, and a repository that trains
# people to click "allow this secret" is worse off than one that never asks.
_FAKE_KEY = "sk_" + "live_" + "51H4xAbCdEfGhIjKlMnOpQrSt"
LEAKY = f"Stripe error: Invalid API Key provided: {_FAKE_KEY}"


@pytest.fixture()
def api(monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main.tokens, "enabled", lambda: True)

    def _fail(where, exc):
        def _raise(*_a, **_k):
            raise exc
        monkeypatch.setattr(main.tokens, where, _raise)
        return TestClient(main.app)
    return _fail


def test_stripes_message_does_not_reach_the_buyer(api):
    """The finding, at its worst: a live key prefix in a toast."""
    client = api("create_checkout", RuntimeError(LEAKY))
    resp = client.post("/api/tokens/checkout", json={"pack_id": "p1"})

    assert resp.status_code == 502
    assert _FAKE_KEY not in resp.text
    assert "Stripe error" not in resp.text
    assert "Invalid API Key" not in resp.text


def test_the_buyer_is_told_they_have_not_been_charged(api):
    """The one thing they actually need to know, and the one thing the log
    cannot tell them. Creating a Checkout Session charges nothing."""
    client = api("create_checkout", RuntimeError(LEAKY))
    body = client.post("/api/tokens/checkout", json={"pack_id": "p1"}).json()

    assert "charged" in body["detail"].lower()


def test_there_is_something_to_quote_to_support(api):
    """In the sentence, not beside it: the client reads `detail` as a string,
    so a structured body renders as "[object Object]" in the toast."""
    import re

    client = api("create_checkout", RuntimeError(LEAKY))
    body = client.post("/api/tokens/checkout", json={"pack_id": "p1"}).json()

    assert re.search(r"quote ([0-9a-f]{6,}) to support", body["detail"]), \
        body["detail"]


def test_the_raw_detail_still_reaches_the_logs(api, caplog):
    import logging

    client = api("create_checkout", RuntimeError(LEAKY))
    with caplog.at_level(logging.WARNING):
        body = client.post("/api/tokens/checkout", json={"pack_id": "p1"}).json()

    import re

    logged = "\n".join(r.getMessage() for r in caplog.records)
    reference = re.search(r"quote ([0-9a-f]{6,}) to support",
                          body["detail"]).group(1)
    assert reference in logged, "the reference the buyer was given is not in the log"
    assert "Invalid API Key" in logged


def test_a_bad_pack_id_is_still_a_plain_client_error(api):
    """A ValueError here is the app's own validation ("no such pack"), which
    IS safe and useful to show. The guard must not swallow it."""
    client = api("create_checkout", ValueError("That token pack doesn't exist."))
    resp = client.post("/api/tokens/checkout", json={"pack_id": "nope"})

    assert resp.status_code == 400
    assert "token pack" in resp.text


# ----------------------------------------------------------- confirming

def test_confirm_does_not_echo_stripe_either(api):
    client = api("confirm_checkout", RuntimeError(LEAKY))
    resp = client.get("/api/tokens/confirm", params={"session_id": "cs_1"})

    assert resp.status_code == 502
    assert _FAKE_KEY not in resp.text


def test_confirm_says_the_payment_is_safe(api):
    """The opposite reassurance from checkout: here the money may already
    have moved, so the honest thing is that the tokens are still coming."""
    client = api("confirm_checkout", RuntimeError(LEAKY))
    body = client.get("/api/tokens/confirm",
                      params={"session_id": "cs_1"}).json()

    import re

    text = body["detail"].lower()
    assert "credited" in text or "tokens" in text
    assert re.search(r"quote [0-9a-f]{6,} to support", body["detail"])


def test_confirms_own_refusals_are_still_shown(api):
    """"This purchase belongs to a different account" is the app talking, and
    it is exactly what the buyer needs to read."""
    client = api("confirm_checkout",
                 ValueError("This purchase belongs to a different account."))
    resp = client.get("/api/tokens/confirm", params={"session_id": "cs_1"})

    assert resp.status_code == 400
    assert "different account" in resp.text

"""Business policies are a program the app could read but never switch on.

Until an eBay account is opted into SELLING_POLICY_MANAGEMENT, every business
policy list comes back empty and every policy id is rejected. That is exactly
the state a new seller lands in: dropdowns with nothing in them, publishes
that fail, and nothing on screen naming the cause. `account_overview` has
always reported the program's status; nothing could ever set it, so the only
fix was a Seller Hub page the seller had to be told to find.

Two things have to be right:

  - "eBay didn't answer" must not read as "opted into nothing". The first
    means nothing is known; the second is what makes the app offer to turn
    policies on. Collapsing them is the mistake this integration has made in
    four other places.
  - The opt-in takes up to 24 hours and returns no payload, so a 2xx means
    eBay ACCEPTED the request, never that policies are ready.
"""
from __future__ import annotations

import httpx
import pytest

from backend import ebay_auth

PROGRAM = ebay_auth.SELLING_POLICY_MANAGEMENT


def _response(status: int, body=None, text: str = "") -> httpx.Response:
    req = httpx.Request("POST", "https://api.ebay.com/x")
    if body is not None:
        return httpx.Response(status, json=body, request=req)
    return httpx.Response(status, text=text, request=req)


# --- reading the program's status ------------------------------------------

def test_an_opted_in_account_is_reported_as_such(monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get",
                        lambda path, token: {"programs": [{"programType": PROGRAM}]})
    assert ebay_auth.opted_in_programs("tok") == {PROGRAM}


def test_an_account_opted_into_nothing_is_an_empty_set(monkeypatch):
    """Empty set, not None: eBay answered, and the answer is "none". This is
    what makes offering the opt-in honest."""
    monkeypatch.setattr(ebay_auth, "_account_get", lambda path, token: {"programs": []})
    assert ebay_auth.opted_in_programs("tok") == set()


def test_an_unreadable_lookup_is_none_not_empty(monkeypatch):
    """The tri-state. "We couldn't ask" and "opted into nothing" lead to
    opposite advice, and only one of them should offer a button."""
    def _boom(path, token):
        raise httpx.ConnectError("eBay unreachable")
    monkeypatch.setattr(ebay_auth, "_account_get", _boom)
    assert ebay_auth.opted_in_programs("tok") is None


# --- switching it on --------------------------------------------------------

def test_the_opt_in_sends_the_program_eBay_expects(monkeypatch):
    sent = {}

    def _post(url, **kw):
        sent.update(url=url, json=kw.get("json"))
        return _response(204, text="")
    monkeypatch.setattr(ebay_auth.httpx, "post", _post)
    ebay_auth.opt_in_to_program("tok")
    assert sent["url"].endswith("/sell/account/v1/program/opt_in")
    assert sent["json"] == {"programType": PROGRAM}


def test_a_refused_opt_in_raises_with_ebays_words(monkeypatch):
    monkeypatch.setattr(ebay_auth.httpx, "post",
                        lambda url, **kw: _response(403, text="not eligible"))
    with pytest.raises(ebay_auth.OAuthError) as caught:
        ebay_auth.opt_in_to_program("tok")
    assert caught.value.status == 403
    assert "not eligible" in caught.value.description


def test_an_empty_body_is_a_success_not_a_parse_error(monkeypatch):
    """eBay documents no response payload. Treating that as a failure would
    report every successful opt-in as broken."""
    monkeypatch.setattr(ebay_auth.httpx, "post",
                        lambda url, **kw: _response(204, text=""))
    assert ebay_auth.opt_in_to_program("tok") is None


# --- selling privileges -----------------------------------------------------

def test_privileges_report_the_limit_that_blocks_a_publish(monkeypatch):
    """The monthly selling limit is error 21919188 — which this app spent a
    release reporting as a duplicate submission. Reading it up front is the
    difference between a checklist item and a rejection."""
    monkeypatch.setattr(ebay_auth, "_account_get", lambda path, token: {
        "sellerRegistrationCompleted": True,
        "sellingLimit": {"amount": {"value": "500.0", "currency": "USD"},
                         "quantity": 10}})
    got = ebay_auth.fetch_privileges("tok")
    assert got["registration_complete"] is True
    assert got["selling_limit"] == {"amount": "500.0", "currency": "USD",
                                    "quantity": 10}


def test_an_uncapped_account_has_no_limit_rather_than_a_zero(monkeypatch):
    """eBay omits sellingLimit for accounts it does not cap. A missing cap is
    not a cap of nothing, and showing "0 items" would be a lie."""
    monkeypatch.setattr(ebay_auth, "_account_get",
                        lambda path, token: {"sellerRegistrationCompleted": True})
    assert ebay_auth.fetch_privileges("tok")["selling_limit"] is None


def test_an_incomplete_registration_is_reported(monkeypatch):
    """A publish cannot succeed until this is true, and no listing field
    explains why."""
    monkeypatch.setattr(ebay_auth, "_account_get",
                        lambda path, token: {"sellerRegistrationCompleted": False})
    assert ebay_auth.fetch_privileges("tok")["registration_complete"] is False


def test_unreadable_privileges_are_none(monkeypatch):
    def _boom(path, token):
        raise httpx.ConnectError("nope")
    monkeypatch.setattr(ebay_auth, "_account_get", _boom)
    assert ebay_auth.fetch_privileges("tok") is None


# --- the overview carries the distinction through ---------------------------

def test_the_overview_says_whether_the_program_list_is_known(monkeypatch):
    monkeypatch.setattr(ebay_auth, "list_business_policies", lambda t: {})
    monkeypatch.setattr(ebay_auth, "fetch_payments_program", lambda t: {})
    monkeypatch.setattr(ebay_auth, "fetch_privileges", lambda t: None)
    monkeypatch.setattr(ebay_auth.httpx, "get",
                        lambda *a, **k: _response(500, text=""))

    monkeypatch.setattr(ebay_auth, "opted_in_programs", lambda t: None)
    assert ebay_auth.account_overview("tok")["programs_known"] is False

    monkeypatch.setattr(ebay_auth, "opted_in_programs", lambda t: set())
    ov = ebay_auth.account_overview("tok")
    assert ov["programs_known"] is True and ov["programs"] == []

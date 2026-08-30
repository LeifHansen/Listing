"""Creating the policies an account needs, not just listing them.

Being opted into the business-policy program is necessary and not sufficient:
the account still needs one shipping, one payment and one return policy before
eBay accepts a listing. Only the fulfillment half of that existed — payment
and return could be listed and never created — so a seller with none was sent
to Seller Hub to hand-build two policies whose contents this app already
assumes.
"""
from __future__ import annotations

import httpx
import pytest

from backend import ebay_auth


def _response(status: int, body=None, text: str = "") -> httpx.Response:
    req = httpx.Request("POST", "https://api.ebay.com/x")
    if body is not None:
        return httpx.Response(status, json=body, request=req)
    return httpx.Response(status, text=text, request=req)


@pytest.fixture
def no_policies(monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", lambda path, token: {})


@pytest.fixture
def captures(monkeypatch):
    sent = {}

    def _post(url, **kw):
        sent.update(url=url, json=kw.get("json"))
        return _response(201, {"paymentPolicyId": "PP-1", "returnPolicyId": "RP-1",
                               "name": kw.get("json", {}).get("name", "")})
    monkeypatch.setattr(ebay_auth.httpx, "post", _post)
    return sent


# --- payment ----------------------------------------------------------------

def test_a_payment_policy_is_created_when_the_account_has_none(no_policies, captures):
    got = ebay_auth.ensure_payment_policy("tok")
    assert got["created"] is True and got["id"] == "PP-1"
    assert captures["url"].endswith("/sell/account/v1/payment_policy")


def test_the_payment_policy_does_not_send_payment_methods(no_policies, captures):
    """On a managed-payments account eBay handles the money, and sending
    paymentMethods is what gets these rejected."""
    ebay_auth.ensure_payment_policy("tok")
    assert "paymentMethods" not in captures["json"]
    assert captures["json"]["immediatePay"] is True


def test_an_existing_payment_policy_is_reused(monkeypatch, captures):
    monkeypatch.setattr(ebay_auth, "_account_get", lambda path, token: {
        "paymentPolicies": [{"paymentPolicyId": "existing", "name": "Mine"}]})

    def _must_not_post(*a, **k):
        raise AssertionError("created a policy when one already existed")
    monkeypatch.setattr(ebay_auth.httpx, "post", _must_not_post)
    got = ebay_auth.ensure_payment_policy("tok")
    assert got == {"id": "existing", "name": "Mine", "created": False}


# --- return -----------------------------------------------------------------

def test_a_return_policy_carries_the_period_ebay_requires(no_policies, captures):
    """returnPeriod is required whenever returns are accepted; without it the
    create is rejected."""
    ebay_auth.ensure_return_policy("tok")
    body = captures["json"]
    assert body["returnsAccepted"] is True
    assert body["returnPeriod"] == {"value": 30, "unit": "DAY"}
    assert body["returnShippingCostPayer"] == "BUYER"


def test_the_only_legal_refund_method_is_sent(no_policies, captures):
    """refundMethod is deprecated to MONEY_BACK and any other value is
    rejected, so it is sent explicitly rather than left to a default."""
    ebay_auth.ensure_return_policy("tok")
    assert captures["json"]["refundMethod"] == "MONEY_BACK"


def test_the_return_window_and_payer_are_arguments(no_policies, captures):
    """They are defaults, not opinions worth hard-coding — Settings owns them
    next."""
    ebay_auth.ensure_return_policy("tok", days=14, payer="SELLER")
    assert captures["json"]["returnPeriod"]["value"] == 14
    assert captures["json"]["returnShippingCostPayer"] == "SELLER"


# --- refusals ---------------------------------------------------------------

def test_a_refused_create_carries_ebays_words(no_policies, monkeypatch):
    """"name already used", "not opted in" and "category type not allowed" all
    arrive as the same 400 otherwise."""
    monkeypatch.setattr(ebay_auth.httpx, "post",
                        lambda url, **kw: _response(400, text="policy name already used"))
    with pytest.raises(ebay_auth.AccountApiError) as caught:
        ebay_auth.ensure_return_policy("tok")
    assert "already used" in caught.value.description
    assert caught.value.status == 400


def test_an_unreadable_policy_list_creates_nothing(monkeypatch, captures):
    """This asserted the opposite -- that "couldn't ask" creates one anyway,
    because "a duplicate policy is recoverable while publishing with none is
    not".

    The recoverable half does not hold up. Every timeout, 500 or token blip
    minted another "Thryft Shop" policy on the seller's REAL eBay account;
    they accumulate, they are visible in Seller Hub, and nothing in this app
    removes them, so recovering means the seller deleting them by hand. The
    other half is a false choice: refusing here does not publish with no
    policy, it declines to guess and says to try again.

    ebay_auth already states this rule for the same kind of question, in
    fulfillment_policy_lookup: "we couldn't ask" must never be reported as
    "you don't have one".
    """
    def _boom(path, token):
        raise httpx.ConnectError("eBay unreachable")
    monkeypatch.setattr(ebay_auth, "_account_get", _boom)
    with pytest.raises(ebay_auth.PolicyLookupUnavailable):
        ebay_auth.ensure_payment_policy("tok")
    assert captures == {}, "an outage created a policy on the seller's account"

""""We couldn't ask" must never be treated as "you don't have one".

"Create my policies" looks up the seller's existing eBay business policies and
creates the ones they lack. The lookup collapsed two different answers into
`None`: eBay said the account has no such policy, and eBay could not be
reached. Both then created one.

So every transient failure — a timeout, a 500, a token blip — minted another
"Thryft Shop" shipping, payment or return policy on the seller's real eBay
account. They accumulate, they are visible in Seller Hub, and nothing in this
app ever cleans them up. The seller did not ask for five identical return
policies; they asked once and the network was bad.

This file's own `fulfillment_policy_lookup` already states the rule the rest
of the module was breaking:

    `exists` is False only when eBay positively says this account has no such
    policy (404) ... while "we couldn't ask" must never be reported as one.

The three-state answer is now used everywhere a create depends on it. On
unknown, the ensure-* helpers refuse rather than guess: an outage becomes
"try again", which is recoverable, instead of a duplicate on a live account,
which is not.
"""
from __future__ import annotations

import pytest

from backend import ebay_auth


class Boom(Exception):
    """Whatever eBay's client raises when it cannot answer."""


def _unreachable(*_a, **_k):
    raise Boom("connection reset by peer")


# ------------------------------------------------- the lookup's three states

def test_a_reachable_account_with_no_policies_answers_none_exist(monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {})
    found, known = ebay_auth._first_existing_policy("payment", "tok")
    assert found is None
    assert known is True, "eBay answered; 'you have none' is a real answer"


def test_an_unreachable_account_answers_unknown(monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", _unreachable)
    found, known = ebay_auth._first_existing_policy("payment", "tok")
    assert found is None
    assert known is False, "'we could not ask' is not 'you have none'"


def test_an_existing_policy_is_returned(monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {
        "paymentPolicies": [{"paymentPolicyId": "p-1", "name": "Mine"}]})
    found, known = ebay_auth._first_existing_policy("payment", "tok")
    assert found == {"id": "p-1", "name": "Mine"}
    assert known is True


# --------------------------------------------- and what the creators do with it

@pytest.mark.parametrize("ensure", ["ensure_payment_policy",
                                    "ensure_return_policy"])
def test_an_outage_does_not_create_a_duplicate_policy(monkeypatch, ensure):
    """The whole point. Each retry during an outage used to leave another
    policy behind on the seller's live eBay account."""
    created = []
    monkeypatch.setattr(ebay_auth, "_account_get", _unreachable)
    monkeypatch.setattr(ebay_auth, "_create_policy",
                        lambda *a, **k: created.append(a) or {"id": "new"})

    with pytest.raises(ebay_auth.PolicyLookupUnavailable):
        getattr(ebay_auth, ensure)("tok")

    assert created == [], "an outage created a policy on the seller's account"


@pytest.mark.parametrize("ensure", ["ensure_payment_policy",
                                    "ensure_return_policy"])
def test_a_genuine_absence_still_creates(monkeypatch, ensure):
    """The feature has to keep working: eBay answering "you have none" is a
    real answer, and creating one is exactly what the seller asked for."""
    created = []
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {})
    monkeypatch.setattr(ebay_auth, "_create_policy",
                        lambda *a, **k: created.append(a) or
                        {"id": "new", "name": "n"})

    result = getattr(ebay_auth, ensure)("tok")

    assert len(created) == 1
    assert result["created"] is True


def test_an_existing_policy_is_reused_not_duplicated(monkeypatch):
    created = []
    monkeypatch.setattr(ebay_auth, "_account_get", lambda *_a, **_k: {
        "paymentPolicies": [{"paymentPolicyId": "p-1", "name": "Mine"}]})
    monkeypatch.setattr(ebay_auth, "_create_policy",
                        lambda *a, **k: created.append(a) or {"id": "new"})

    result = ebay_auth.ensure_payment_policy("tok")

    assert created == []
    assert result == {"id": "p-1", "name": "Mine", "created": False}


def test_the_shipping_policy_lookup_has_the_same_three_states(monkeypatch):
    """find_policy_for_service fed the same create-on-None decision."""
    monkeypatch.setattr(ebay_auth, "_account_get", _unreachable)
    found, known = ebay_auth.find_policy_for_service("tok", "USPSGroundAdvantage")
    assert (found, known) == (None, False)

    monkeypatch.setattr(ebay_auth, "_account_get",
                        lambda *_a, **_k: {"fulfillmentPolicies": []})
    found, known = ebay_auth.find_policy_for_service("tok", "USPSGroundAdvantage")
    assert (found, known) == (None, True)


def test_an_outage_does_not_create_a_duplicate_shipping_policy(monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", _unreachable)
    posted = []
    monkeypatch.setattr(ebay_auth.httpx, "post",
                        lambda *a, **k: posted.append(a) or None)

    with pytest.raises(ebay_auth.PolicyLookupUnavailable):
        ebay_auth.ensure_service_policy(
            "tok", {"code": "USPSGroundAdvantage", "label": "Ground",
                    "carrier": "USPS"})

    assert posted == []


def test_the_refusal_is_its_own_type_not_a_generic_failure():
    """The route turns this into "try again", which is a different message
    from eBay refusing the policy itself — that one names a field to fix."""
    assert issubclass(ebay_auth.PolicyLookupUnavailable, Exception)
    assert not issubclass(ebay_auth.PolicyLookupUnavailable,
                          ebay_auth.AccountApiError)


def test_the_route_answers_retry_not_a_server_error(monkeypatch):
    """503 and not 502 or 500: nothing is wrong with the seller's request, and
    "eBay refused this" is a different sentence from "we couldn't ask"."""
    pytest.importorskip("fastapi")
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from fastapi.testclient import TestClient

    from backend import main

    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda _r: {"access_token": "tok", "_uid": "u1"})
    monkeypatch.setattr(main.ebay_auth, "service_by_code",
                        lambda _c: {"code": "USPSGroundAdvantage",
                                    "label": "Ground", "carrier": "USPS"})

    def _unavailable(*_a, **_k):
        raise ebay_auth.PolicyLookupUnavailable("couldn't check")

    monkeypatch.setattr(main.ebay_auth, "ensure_service_policy", _unavailable)

    resp = TestClient(main.app).post(
        "/api/ebay/ensure-policy", json={"service_code": "USPSGroundAdvantage"})

    assert resp.status_code == 503, resp.text

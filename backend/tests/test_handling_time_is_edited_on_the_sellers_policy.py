"""Handling time is edited on the seller's own eBay shipping policy.

eBay has no account-wide handling time: it lives on each fulfillment policy
and every listing under the policy is measured against it. So Settings does
not store a number -- it changes the policy eBay holds, through
updateFulfillmentPolicy, and the next policies read shows what eBay now
says. This file pins the glue around that:

  * the route refuses anything but a window eBay offers, before eBay is
    asked, and refuses to act until a default shipping policy is picked --
    that policy is where the change lands;
  * the PUT sends eBay's own policy back with ONLY the handling time
    changed, minus the read-only fields eBay rejects on the way in, so a
    policy made in Seller Hub keeps every field this app never set;
  * eBay's refusal reaches the seller in eBay's words as a 502 (nothing the
    seller typed is wrong), and a transport failure as a 503;
  * the policies list carries the terms the screen shows -- handling days,
    return window and payer, immediate pay -- read off the bodies it fetches.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import ebay_auth, main  # noqa: E402


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.text = text or ("" if status < 400 else "eBay said no")
        self.is_success = 200 <= status < 300

    def json(self):
        return self._body


POLICY = {"fulfillmentPolicyId": "FP-1", "name": "Ground (Thryft Shop)",
          "marketplaceId": "EBAY_US",
          "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES"}],
          "handlingTime": {"value": 2, "unit": "DAY"},
          "shippingOptions": [{"optionType": "DOMESTIC", "costType": "CALCULATED",
                               "shippingServices": [{"shippingServiceCode": "USPSGroundAdvantage"}]}],
          "warnings": []}


# ------------------------------------------------- the helper's PUT

def test_the_put_is_ebays_policy_with_only_the_handling_time_changed(monkeypatch):
    sent = {}
    monkeypatch.setattr(ebay_auth.httpx, "get",
                        lambda url, **kw: _Resp(200, dict(POLICY)))

    def _put(url, **kw):
        sent["url"] = url
        sent["json"] = kw["json"]
        return _Resp(200, {**kw["json"], "fulfillmentPolicyId": "FP-1"})
    monkeypatch.setattr(ebay_auth.httpx, "put", _put)

    out = ebay_auth.set_fulfillment_handling_time("tok", "FP-1", 1)
    assert out == {"handling_days": 1, "name": "Ground (Thryft Shop)",
                   "policy_id": "FP-1"}
    assert sent["url"].endswith("/sell/account/v1/fulfillment_policy/FP-1")
    body = sent["json"]
    assert body["handlingTime"] == {"value": 1, "unit": "DAY"}
    assert "fulfillmentPolicyId" not in body and "warnings" not in body
    # Everything else is eBay's own, untouched.
    assert body["shippingOptions"] == POLICY["shippingOptions"]
    assert body["categoryTypes"] == POLICY["categoryTypes"]


def test_a_policy_ebay_wont_return_is_a_refusal_with_ebays_words(monkeypatch):
    monkeypatch.setattr(ebay_auth.httpx, "get",
                        lambda url, **kw: _Resp(404, text="not on this account"))
    with pytest.raises(ebay_auth.AccountApiError) as exc:
        ebay_auth.set_fulfillment_handling_time("tok", "FP-9", 1)
    assert "not on this account" in exc.value.description


# ------------------------------------------------- the route

@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "tok", "_uid": "u1",
                                         "fulfillment_policy_id": "FP-1"})
    monkeypatch.setattr(main.deps, "uid", lambda request: "u1")
    return TestClient(main.app)


def _patch(client, days):
    return client.patch("/api/ebay/handling-time", json={"days": days})


def test_a_window_ebay_does_not_offer_is_refused_before_ebay_is_asked(
        connected, monkeypatch):
    def _must_not_run(*a, **k):
        raise AssertionError("eBay was asked to set a handling time it refuses")
    monkeypatch.setattr(main.ebay_auth, "set_fulfillment_handling_time",
                        _must_not_run)
    for days in (7, -1, "soon", None):
        r = _patch(connected, days)
        assert r.status_code == 400, days
        assert "eBay offers" in r.json()["detail"]


def test_no_default_shipping_policy_means_nowhere_to_put_it(monkeypatch):
    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "tok", "_uid": "u1"})
    monkeypatch.setattr(main.deps, "uid", lambda request: "u1")
    r = _patch(TestClient(main.app), 1)
    assert r.status_code == 400
    assert "shipping policy" in r.json()["detail"]


def test_the_change_lands_on_the_default_policy(connected, monkeypatch):
    asked = {}

    def _set(token, policy_id, days):
        asked.update(policy_id=policy_id, days=days)
        return {"handling_days": days, "name": "Ground", "policy_id": policy_id}
    monkeypatch.setattr(main.ebay_auth, "set_fulfillment_handling_time", _set)
    body = _patch(connected, 1).json()
    assert body == {"ok": True, "handling_days": 1, "policy_id": "FP-1"}
    assert asked == {"policy_id": "FP-1", "days": 1}


def test_ebays_refusal_reaches_the_seller_in_ebays_words(connected, monkeypatch):
    def _refuse(token, policy_id, days):
        raise ebay_auth.AccountApiError("no", status=400,
                                        description="Handling time not allowed")
    monkeypatch.setattr(main.ebay_auth, "set_fulfillment_handling_time", _refuse)
    r = _patch(connected, 3)
    assert r.status_code == 502
    assert "Handling time not allowed" in r.json()["detail"]


def test_a_transport_failure_is_a_try_again_not_a_refusal(connected, monkeypatch):
    def _drop(token, policy_id, days):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(main.ebay_auth, "set_fulfillment_handling_time", _drop)
    r = _patch(connected, 1)
    assert r.status_code == 503
    assert "no route" not in r.json()["detail"]


def test_not_connected_is_told_so(monkeypatch):
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: None)
    monkeypatch.setattr(main.deps, "uid", lambda request: "u1")
    assert _patch(TestClient(main.app), 1).status_code == 400


# ------------------------------------------------- the terms the list carries

def test_the_policies_list_carries_the_terms_the_screen_shows(monkeypatch):
    answers = {
        "/sell/account/v1/fulfillment_policy": {"fulfillmentPolicies": [
            {**POLICY, "globalShipping": True}]},
        "/sell/account/v1/payment_policy": {"paymentPolicies": [
            {"paymentPolicyId": "PP-1", "name": "Pay", "immediatePay": True}]},
        "/sell/account/v1/return_policy": {"returnPolicies": [
            {"returnPolicyId": "RP-1", "name": "Returns", "returnsAccepted": True,
             "returnPeriod": {"value": 30, "unit": "DAY"},
             "returnShippingCostPayer": "BUYER"},
            {"returnPolicyId": "RP-2", "name": "None", "returnsAccepted": False}]},
    }
    monkeypatch.setattr(ebay_auth, "_account_get",
                        lambda path, token: answers[path])
    out = ebay_auth.list_business_policies("tok")
    ship = out["fulfillment"][0]
    assert ship["handling_days"] == 2 and ship["international_shipping"] is True
    assert out["payment"][0]["immediate_pay"] is True
    assert out["return"][0]["terms"] == {"accepted": True, "days": 30,
                                         "payer": "BUYER"}
    assert out["return"][1]["terms"] == {"accepted": False, "days": None,
                                         "payer": ""}


def test_a_policy_with_no_readable_handling_time_says_none(monkeypatch):
    monkeypatch.setattr(ebay_auth, "_account_get", lambda path, token: {
        "fulfillmentPolicies": [{"fulfillmentPolicyId": "FP-1", "name": "x",
                                 "handlingTime": {"value": "soon"}}]}
        if "fulfillment" in path else {})
    assert ebay_auth.list_business_policies("tok")["fulfillment"][0][
        "handling_days"] is None

"""One eBay policy re-check per account at a time, not one per request.

creds_for re-checks the account-scoped policy ids at most once per VERIFY_TTL
— four sequential eBay account-API calls at a 30-second timeout each. The gate
only READ the clock, so once the TTL lapsed every request building creds at the
same moment ran its own check: the dashboard's insights, metrics, inbox poll and
orders all fire together, and each held a threadpool slot for the whole of it.
One of them checking is the same answer for all of them; the rest go on with the
stored settings, exactly as they would inside the TTL.
"""
from __future__ import annotations

import threading

import pytest

from backend.marketplaces import ebay_provider
from backend.services import ebay_account

ACCT = {
    "ebay_username": "seller",
    "fulfillment_policy_id": "F1", "payment_policy_id": "P1",
    "return_policy_id": "R1", "merchant_location_key": "L1",
    "ship_from_postal": "90210", "refresh_token": "r",
}
MINE = ({"fulfillment": {"F1"}, "payment": {"P1"}, "return": {"R1"}}, {"L1"})


@pytest.fixture(autouse=True)
def _fresh():
    ebay_account.forget_verified()
    ebay_account.release_verify("u1")
    yield
    ebay_account.forget_verified()
    ebay_account.release_verify("u1")


def _stub(monkeypatch, fetch, policies=MINE[0], locations=MINE[1]):
    monkeypatch.setattr(ebay_provider.ebay_auth, "fetch_policies_and_location",
                        fetch)
    monkeypatch.setattr(ebay_provider.ebay_auth, "policy_ids_on_account",
                        lambda _t: policies)
    monkeypatch.setattr(ebay_provider.ebay_auth, "location_keys_on_account",
                        lambda _t: locations)
    monkeypatch.setattr(ebay_provider.db, "save_ebay_account",
                        lambda uid, **kw: None)
    monkeypatch.setattr(ebay_provider.db, "stamp_ebay_account",
                        lambda uid, name: 0)


def test_requests_that_arrive_together_share_one_check(monkeypatch):
    calls = []
    inside = threading.Event()
    let_go = threading.Event()

    def slow_fetch(_token):
        calls.append(1)
        inside.set()
        let_go.wait(5)
        return {"fulfillment_policy_id": "F1", "payment_policy_id": "P1",
                "return_policy_id": "R1", "merchant_location_key": "L1"}

    _stub(monkeypatch, slow_fetch)
    first = threading.Thread(target=ebay_provider._with_current_policies,
                             args=("u1", dict(ACCT), "tok"))
    first.start()
    assert inside.wait(5)
    # While the first check is out at eBay, the others do not start their own.
    others = [ebay_provider._with_current_policies("u1", dict(ACCT), "tok")
              for _ in range(5)]
    let_go.set()
    first.join(5)
    assert len(calls) == 1, "every request ran its own four-call check"
    assert all(o == ACCT for o in others), "the others use the stored settings"


def test_an_inconclusive_check_is_retried_by_the_next_request(monkeypatch):
    """The claim is released however the check ends; only a pass eBay
    answered starts the clock."""
    calls = []
    _stub(monkeypatch, lambda _t: calls.append(1) or {},
          policies={}, locations=None)
    ebay_provider._with_current_policies("u1", dict(ACCT), "tok")
    ebay_provider._with_current_policies("u1", dict(ACCT), "tok")
    assert len(calls) == 2


def test_a_check_that_raised_is_retried_by_the_next_request(monkeypatch):
    calls = []

    def boom(_t):
        calls.append(1)
        raise RuntimeError("eBay is down")

    _stub(monkeypatch, boom)
    ebay_provider._with_current_policies("u1", dict(ACCT), "tok")
    ebay_provider._with_current_policies("u1", dict(ACCT), "tok")
    assert len(calls) == 2

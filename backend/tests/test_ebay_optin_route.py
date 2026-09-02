"""The opt-in route's glue, which is where this can regress quietly.

The service functions are covered in test_ebay_policy_optin.py. What this file
pins is the handler around them:

  - an unreadable program list must NOT be treated as "not opted in". Doing so
    fires a needless opt-in at eBay every time the lookup blips, and tells a
    seller their policies are on the way when nothing was wrong.
  - an account already opted in must not be asked again.
  - a refusal has to reach the seller as somewhere to go, not a status code.
"""
from __future__ import annotations

import pytest

# Importing backend.main pulls the whole app in. `checks` has neither of these,
# so it skips the file; the smoke job's "API tests" step is where it runs, and
# that step fails on a skip so this can never quietly stop running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402

PROGRAM = "SELLING_POLICY_MANAGEMENT"


@pytest.fixture
def connected(monkeypatch):
    # `_uid` rides along in the real creds dict — the existing ensure-policy
    # route reads it the same way.
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "tok", "_uid": "u1"})
    monkeypatch.setattr(main, "_uid", lambda request: "u1")
    return TestClient(main.app)


def _post(client):
    return client.post("/api/ebay/opt-in-policies")


def test_an_account_already_opted_in_is_not_asked_again(connected, monkeypatch):
    monkeypatch.setattr(main.ebay_auth, "opted_in_programs", lambda t: {PROGRAM})

    def _must_not_run(*a, **k):
        raise AssertionError("opted in again for an account that already was")
    monkeypatch.setattr(main.ebay_auth, "opt_in_to_program", _must_not_run)

    body = _post(connected).json()
    assert body["already"] is True and body["pending"] is False


def test_an_account_opted_into_nothing_gets_the_opt_in(connected, monkeypatch):
    asked = []
    monkeypatch.setattr(main.ebay_auth, "opted_in_programs", lambda t: set())
    monkeypatch.setattr(main.ebay_auth, "opt_in_to_program",
                        lambda t, p=PROGRAM: asked.append(p))
    body = _post(connected).json()
    assert asked == [PROGRAM]
    assert body["pending"] is True and body["already"] is False


def test_an_unreadable_program_list_still_opts_in_rather_than_claiming_success(
        connected, monkeypatch):
    """None means "we couldn't ask". Short-circuiting on it would report
    "already switched on" to an account that may not be — the failure this
    audit keeps finding. Asking eBay again is harmless; opt-in is idempotent."""
    asked = []
    monkeypatch.setattr(main.ebay_auth, "opted_in_programs", lambda t: None)
    monkeypatch.setattr(main.ebay_auth, "opt_in_to_program",
                        lambda t, p=PROGRAM: asked.append(p))
    body = _post(connected).json()
    assert asked == [PROGRAM]
    assert body["already"] is False


def test_the_response_never_says_the_policies_are_ready(connected, monkeypatch):
    """eBay takes up to 24 hours and returns no payload, so the only honest
    claim is that the request was accepted."""
    monkeypatch.setattr(main.ebay_auth, "opted_in_programs", lambda t: set())
    monkeypatch.setattr(main.ebay_auth, "opt_in_to_program", lambda t, p=PROGRAM: None)
    message = _post(connected).json()["message"]
    assert "24 hours" in message
    assert "ready" not in message.lower()


def test_a_refusal_points_somewhere_the_seller_can_go(connected, monkeypatch):
    monkeypatch.setattr(main.ebay_auth, "opted_in_programs", lambda t: set())

    def _refuse(t, p=PROGRAM):
        raise main.ebay_auth.AccountApiError("nope", status=403, description="not eligible")
    monkeypatch.setattr(main.ebay_auth, "opt_in_to_program", _refuse)

    resp = _post(connected)
    assert resp.status_code == 502
    assert "Seller Hub" in resp.json()["detail"]


def test_a_disconnected_account_is_told_to_connect(monkeypatch):
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: None)
    assert _post(TestClient(main.app)).status_code == 400


# --- ensure-all-policies ----------------------------------------------------
#
# One policy eBay won't make must not cost the other two: the most common
# reason is the account not being opted in yet, which is a different button on
# the same screen, and an all-or-nothing failure hides which part worked.
#
# These posted `{}` when the route created policies unasked. It now requires
# the seller to have seen the terms (see test_policy_terms_consent.py), so the
# payload carries that acknowledgement -- these two tests are about what the
# route REPORTS and what it saves, both of which are past the gate. The gate
# itself is asserted there, including that `{}` creates nothing.

def test_a_partial_failure_still_reports_what_was_created(connected, monkeypatch):
    monkeypatch.setattr(main.ebay_auth, "ensure_service_policy",
                        lambda t, svc: {"id": "FP-1", "name": "Ship", "created": True})
    monkeypatch.setattr(main.ebay_auth, "ensure_payment_policy",
                        lambda t: {"id": "PP-1", "name": "Pay", "created": False})

    def _refuse(t, **kw):
        raise main.ebay_auth.AccountApiError("no", status=400,
                                             description="not opted in")
    monkeypatch.setattr(main.ebay_auth, "ensure_return_policy", _refuse)
    monkeypatch.setattr(main.db, "save_ebay_account", lambda uid, **kw: None)
    monkeypatch.setattr(main.ebay_account, "note_verified", lambda uid: None)

    body = connected.post("/api/ebay/ensure-all-policies",
                   json={"accept_terms": True}).json()
    assert body["ok"] is False
    assert body["created"] == ["fulfillment"]
    assert "not opted in" in body["errors"]["return"]
    assert set(body["policies"]) == {"fulfillment", "payment"}


def test_a_policy_the_seller_already_chose_is_not_overwritten(connected, monkeypatch):
    """Ids are saved as defaults only where none is set. Overwriting a
    deliberate choice with eBay's first policy is the bug #186 was about."""
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {
        "access_token": "tok", "_uid": "u1",
        "fulfillment_policy_id": "chosen-by-the-seller"})
    for name, pol in (("ensure_service_policy", {"id": "FP-new", "created": True}),
                      ("ensure_payment_policy", {"id": "PP-1", "created": True}),
                      ("ensure_return_policy", {"id": "RP-1", "created": True})):
        monkeypatch.setattr(main.ebay_auth, name,
                            lambda *a, _p=pol, **k: dict(_p, name="x"))
    saved = {}
    monkeypatch.setattr(main.db, "save_ebay_account",
                        lambda uid, **kw: saved.update(kw))
    monkeypatch.setattr(main.ebay_account, "note_verified", lambda uid: None)

    connected.post("/api/ebay/ensure-all-policies",
                   json={"accept_terms": True})
    assert "fulfillment_policy_id" not in saved
    assert saved == {"payment_policy_id": "PP-1", "return_policy_id": "RP-1"}


# --- the ship-from ZIP can be cleared ---------------------------------------

def test_clearing_the_zip_actually_clears_it(connected, monkeypatch):
    """It used to be a silent no-op reported as success: the field showed
    empty, the stored value never changed, and the old ZIP came back on the
    next load."""
    saved = {}
    monkeypatch.setattr(main.db, "save_ebay_account", lambda uid, **kw: saved.update(kw))
    connected.post("/api/ebay/policies", json={"ship_from_postal": ""})
    assert saved["ship_from_postal"] == ""


def test_clearing_the_zip_keeps_the_location_key(connected, monkeypatch):
    """Publishing needs a ship-from ZIP, and with no typed one create_on_ebay
    reads it off the seller's eBay location via that key. Dropping both would
    leave the account unable to publish for having emptied a text box."""
    saved = {}
    monkeypatch.setattr(main.db, "save_ebay_account", lambda uid, **kw: saved.update(kw))
    connected.post("/api/ebay/policies", json={"ship_from_postal": ""})
    assert "merchant_location_key" not in saved


def test_a_request_that_omits_the_zip_leaves_it_alone(connected, monkeypatch):
    """Saving only a policy selection must not wipe the ZIP — the key is
    absence of the field, not emptiness of it."""
    saved = {}
    monkeypatch.setattr(main.db, "save_ebay_account", lambda uid, **kw: saved.update(kw))
    connected.post("/api/ebay/policies", json={"payment_policy_id": "PP-1"})
    assert "ship_from_postal" not in saved


def test_setting_a_zip_still_creates_the_location(connected, monkeypatch):
    saved = {}
    monkeypatch.setattr(main.db, "save_ebay_account", lambda uid, **kw: saved.update(kw))
    monkeypatch.setattr(main.ebay_auth, "ensure_inventory_location",
                        lambda token, postal: "LOC-1")
    connected.post("/api/ebay/policies", json={"ship_from_postal": "97201"})
    assert saved == {"merchant_location_key": "LOC-1", "ship_from_postal": "97201"}

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
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {"access_token": "tok"})
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
        raise main.ebay_auth.OAuthError("nope", status=403, description="not eligible")
    monkeypatch.setattr(main.ebay_auth, "opt_in_to_program", _refuse)

    resp = _post(connected)
    assert resp.status_code == 502
    assert "Seller Hub" in resp.json()["detail"]


def test_a_disconnected_account_is_told_to_connect(monkeypatch):
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: None)
    assert _post(TestClient(main.app)).status_code == 400

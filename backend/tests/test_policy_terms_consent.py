"""A business policy is a promise to buyers. The seller has to see it first.

"Create my policies" builds three real eBay business policies on the seller's
own account, and every one of them carries commercial terms this app chose:

  - dispatch within 2 business days (missing it costs the seller's eBay
    seller standing, and eBay measures it),
  - returns accepted for 30 days,
  - the BUYER pays return shipping,
  - immediate payment required at checkout,
  - domestic shipping only, cost calculated by eBay.

Those terms are shown to buyers on every listing that references the policy,
and they bind the seller until they change them in Seller Hub. The app picked
all of them behind a button whose entire label was "Create my policies", and
the seller found out what they had agreed to by reading it back off eBay.

So: the terms are previewable before anything is created, the preview is the
SAME data the create actually sends, and creating requires the seller to say
yes to it. A missing acknowledgement is not consent.
"""
from __future__ import annotations

import pytest

# Importing backend.main pulls the whole app in. The `checks` job installs
# neither of these, so it skips this file; the smoke job's "API tests" step is
# where it runs, and that step fails on a skip so this can never quietly stop
# running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient


@pytest.fixture()
def connected(monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {
        "access_token": "tok", "_uid": "u1"})
    monkeypatch.setattr(main.db, "save_ebay_account", lambda uid, **kw: None)
    monkeypatch.setattr(main.ebay_account, "note_verified", lambda uid: None)
    return TestClient(main.app)


@pytest.fixture()
def would_create(monkeypatch):
    """Record which policies a create attempt reached, without eBay."""
    from backend import main

    reached: list[str] = []
    for name, kind in (("ensure_service_policy", "fulfillment"),
                       ("ensure_payment_policy", "payment"),
                       ("ensure_return_policy", "return")):
        monkeypatch.setattr(
            main.ebay_auth, name,
            lambda *a, _k=kind, **kw: (reached.append(_k),
                                       {"id": f"{_k}-1", "name": _k,
                                        "created": True})[1])
    return reached


# ----------------------------------------------- the terms are previewable

def test_the_terms_can_be_read_before_anything_is_created(connected):
    body = connected.get("/api/ebay/policy-preview")
    assert body.status_code == 200, body.text
    kinds = body.json()["kinds"]
    assert set(kinds) == {"fulfillment", "payment", "return"}
    for kind in kinds.values():
        assert kind["terms"], "a policy was previewed with no terms at all"


def test_the_preview_names_the_terms_that_bind_the_seller(connected):
    """Not a vague summary: the four commitments a seller can be penalised
    for, or lose money on, have to be legible in the preview text."""
    blob = connected.get("/api/ebay/policy-preview").text.lower()

    for owed in ("2 business day", "30 day", "buyer", "immediate"):
        assert owed in blob, f"the preview never mentions {owed!r}"


def test_the_preview_reflects_the_choices_the_seller_made(connected):
    """A seller who picks a 14-day window and pays return postage themselves
    must be shown THAT, not the defaults."""
    body = connected.get("/api/ebay/policy-preview",
                         params={"return_days": 14, "return_payer": "SELLER"})
    terms = body.json()["kinds"]["return"]["terms"]
    blob = " ".join(f"{t['label']}: {t['value']}" for t in terms).lower()

    assert "14 day" in blob
    assert "buyer" not in blob, "a seller-paid return still said the buyer pays"


def test_the_preview_creates_nothing(connected, would_create):
    """It is a GET. Previewing terms on a seller's live eBay account must not
    be how the policies come to exist."""
    resp = connected.get("/api/ebay/policy-preview",
                         params={"service_code": "USPSGroundAdvantage"})
    assert resp.status_code == 200, resp.text
    assert would_create == []
    # And it is not a POST route wearing a GET's name.
    assert connected.post("/api/ebay/policy-preview").status_code == 405


# --------------------------------------- the preview is what actually goes

@pytest.mark.parametrize("kind,builder,args", [
    ("payment", "payment_body", {}),
    ("return", "return_body", {"days": 30, "payer": "BUYER"}),
])
def test_every_previewed_term_is_a_real_field_of_the_request(kind, builder, args):
    """The preview is derived FROM the request body, so it cannot drift into
    describing terms the create does not send. This asserts the derivation is
    live rather than a second hand-written copy that happens to agree today.
    """
    from backend import ebay_auth
    from backend.services import policy_terms

    body = getattr(ebay_auth, builder)(**args)
    described = policy_terms.describe()["kinds"][kind]

    assert described["body"] == body, \
        "the previewed policy is not the one that would be created"


def test_the_shipping_preview_matches_the_body_for_the_chosen_service():
    from backend import ebay_auth
    from backend.services import policy_terms

    svc = ebay_auth.service_by_code("USPSPriority")
    described = policy_terms.describe(service_code="USPSPriority")

    assert described["kinds"]["fulfillment"]["body"] == \
        ebay_auth.fulfillment_body(svc)


def test_the_handling_time_shown_is_the_handling_time_sent():
    """The single term most likely to cost the seller money if it is wrong."""
    from backend import ebay_auth
    from backend.services import policy_terms

    shown = policy_terms.describe()["kinds"]["fulfillment"]
    sent = ebay_auth.fulfillment_body(
        ebay_auth.service_by_code("USPSGroundAdvantage"))

    assert sent["handlingTime"]["value"] == ebay_auth.DEFAULT_HANDLING_DAYS
    assert f"{ebay_auth.DEFAULT_HANDLING_DAYS} business day" in \
        " ".join(t["value"] for t in shown["terms"]).lower()


# ------------------------------------------------ creating requires consent

def test_creating_without_an_acknowledgement_is_refused(connected, would_create):
    """The finding. An unconfirmed request reached eBay and made three real
    policies on the seller's account."""
    resp = connected.post("/api/ebay/ensure-all-policies", json={})

    assert resp.status_code == 400, resp.text
    assert would_create == [], \
        "policies were created on a live eBay account without consent"


def test_the_refusal_says_what_to_do(connected, would_create):
    resp = connected.post("/api/ebay/ensure-all-policies", json={})
    assert "terms" in resp.text.lower() or "review" in resp.text.lower()


def test_a_confirmed_request_goes_through(connected, would_create):
    resp = connected.post("/api/ebay/ensure-all-policies",
                          json={"accept_terms": True})

    assert resp.status_code == 200, resp.text
    assert sorted(would_create) == ["fulfillment", "payment", "return"]


@pytest.mark.parametrize("payload", [
    {}, {"accept_terms": False}, {"accept_terms": "no"}, {"accept_terms": None},
    {"accept_terms": 0}, {"accepted": True},
])
def test_nothing_short_of_yes_counts_as_yes(connected, would_create, payload):
    """A missing preference, a stale client, a typo'd key: none of them are a
    seller agreeing to a 30-day return window."""
    connected.post("/api/ebay/ensure-all-policies", json=payload)
    assert would_create == []


def test_one_shipping_service_is_gated_too(connected, would_create):
    """ensure-policy creates a fulfillment policy with the same 2-day handling
    commitment. Gating only the three-at-once button leaves the same terms
    reachable one door over."""
    resp = connected.post("/api/ebay/ensure-policy",
                          json={"service_code": "USPSGroundAdvantage"})

    assert resp.status_code == 400, resp.text
    assert would_create == []


def test_a_confirmed_single_service_goes_through(connected, would_create):
    resp = connected.post(
        "/api/ebay/ensure-policy",
        json={"service_code": "USPSGroundAdvantage", "accept_terms": True})

    assert resp.status_code == 200, resp.text
    assert would_create == ["fulfillment"]


def test_consent_is_refused_before_the_account_is_even_looked_at(
        connected, monkeypatch, would_create):
    """The gate is not a late check somewhere past a partial write: an
    unconfirmed create must not save policy ids either."""
    from backend import main

    saved: dict = {}
    monkeypatch.setattr(main.db, "save_ebay_account",
                        lambda uid, **kw: saved.update(kw))

    connected.post("/api/ebay/ensure-all-policies", json={})
    assert saved == {}

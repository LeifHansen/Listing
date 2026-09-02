"""The per-marketplace results map has to carry the same answer.

Sibling of test_an_unknown_outcome_reaches_the_client, which pins the legacy
single-eBay body. A publish with more than one marketplace selected takes the
other shape -- {multi: true, results: {ebay: {...}, etsy: {...}}} -- built
field by field in main.publish, so anything a provider reports that is not
listed there is dropped on the way out.

That is how it was dropped: the providers classify a lost answer, ebay_errors
words it, and the fan-out response said only `ok: false`. The bulk queues then
counted an unanswered publish as a refusal and told the seller to fix a field
on a listing that may already be live.

Present only when true, like `promote_status` and `record_warning` beside it.
Its absence is the answer for every marketplace that gave one.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend.marketplaces.base import PublishOutcome  # noqa: E402


class _Provider:
    """One marketplace, with its answer to this publish decided up front."""

    oauth_ready = staticmethod(lambda: True)

    def __init__(self, key: str, label: str, outcome: PublishOutcome):
        self.key, self.label, self._outcome = key, label, outcome

    def creds_for(self, _uid):
        return {"access_token": "tok"}

    def publish(self, _ctx, _creds):
        return self._outcome


@pytest.fixture()
def api(monkeypatch):
    from backend import main

    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main, "_restore_server_state", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)
    monkeypatch.setattr(main.db, "get_listing", lambda _sid: {})
    monkeypatch.setattr(main.db, "mutate_listing_data",
                        lambda *a, **k: {"title": "Vintage Levi's 501"})
    monkeypatch.setattr(main.db, "upsert_listing", lambda *a, **k: True)

    def _install(**providers):
        monkeypatch.setattr(main.marketplaces, "get", providers.get)
        return TestClient(main.app)
    return _install


def _publish(client, targets=("ebay", "etsy")):
    return client.post("/api/publish", json={
        "session_id": "s1",
        "listing": {"title": "Vintage Levi's 501", "description": "Nice.",
                    "price": 45.0, "quantity": 1},
        "mode": "live",
        "marketplaces": list(targets),
    })


LOST = PublishOutcome(
    ok=False, outcome_unknown=True,
    message="The request reached eBay and the answer didn't come back.",
    issues=[{"target": "generic", "level": "error",
             "title": "We could not confirm what eBay did",
             "fix": "Check this item in your eBay listings before trying "
                    "again — retrying blind could publish it twice."}])
REFUSED = PublishOutcome(
    ok=False, message="Add a package weight.",
    issues=[{"target": "shipping", "level": "error",
             "title": "eBay needs a package weight", "fix": "Add one."}])
LIVE = PublishOutcome(ok=True, status="published", listing_id="99",
                      url="https://ebay.test/99", message="Live.")


def test_a_marketplace_that_could_not_answer_says_so(api):
    client = api(ebay=_Provider("ebay", "eBay", LOST),
                 etsy=_Provider("etsy", "Etsy", LIVE))

    results = _publish(client).json()["results"]

    assert results["ebay"]["ok"] is False
    assert results["ebay"]["outcome_unknown"] is True


def test_a_marketplace_that_refused_does_not(api):
    """The absence is the signal — a flag on every entry tells a client
    nothing about which of them it has to go and check."""
    client = api(ebay=_Provider("ebay", "eBay", REFUSED),
                 etsy=_Provider("etsy", "Etsy", LIVE))

    results = _publish(client).json()["results"]

    assert results["ebay"]["ok"] is False
    assert "outcome_unknown" not in results["ebay"]


def test_a_marketplace_that_published_does_not_either(api):
    client = api(ebay=_Provider("ebay", "eBay", LIVE),
                 etsy=_Provider("etsy", "Etsy", LIVE))

    results = _publish(client).json()["results"]

    assert results["ebay"]["published"] is True
    assert "outcome_unknown" not in results["ebay"]


def test_one_unknown_outcome_does_not_speak_for_the_others(api):
    """Each marketplace is its own pipeline and its own answer. A seller told
    to go and check has to be told WHERE."""
    client = api(ebay=_Provider("ebay", "eBay", LOST),
                 etsy=_Provider("etsy", "Etsy", REFUSED))

    results = _publish(client).json()["results"]

    assert results["ebay"]["outcome_unknown"] is True
    assert "outcome_unknown" not in results["etsy"]


def test_the_words_still_come_with_it(api):
    """The flag is for counting; the issue is what the seller reads. Neither
    is enough on its own, so the response carries both."""
    client = api(ebay=_Provider("ebay", "eBay", LOST),
                 etsy=_Provider("etsy", "Etsy", LIVE))

    ebay_result = _publish(client).json()["results"]["ebay"]

    titles = " ".join(i["title"] for i in ebay_result["issues"]).lower()
    assert "reject" not in titles
    assert "could not confirm" in titles

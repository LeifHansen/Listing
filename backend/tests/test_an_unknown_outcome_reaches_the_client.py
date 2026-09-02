""""We could not confirm what eBay did" has to survive the trip to the browser.

The server already knows the difference. services/ebay_trading raises its own
UnknownOutcome when a write reached eBay and the answer never came back, and
backend/ebay_errors turns that into an issue careful never to say "rejected"
-- its comment names this exact scenario:

    "A seller who reads 'rejected' fixes something and publishes again, which
    is how the duplicate live listing happens."

Then the publish response carried no flag saying so, and the surfaces that
COUNT publishes rather than render them had nothing to count on. Both bulk
queues filed an unanswered publish under `failed` and wrote the summary line
themselves:

    "Published 5 listings. All 2 were refused: We could not confirm what eBay
    did"

A sentence that contradicts itself, directly above the one action that must
not be taken. So the classification the clients already make now travels:
`outcome_unknown` on the PublishOutcome, in the legacy single-eBay body the
bulk queues read, and in the fan-out's per-marketplace results map.

It stays absent on a real rejection, and that absence is what a client reads
as "this outcome is known" -- a flag that is always present says nothing.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("pydantic")

from backend.marketplaces import ebay_provider
from backend.marketplaces.base import PublishContext, PublishOutcome
from backend.models import Listing
from backend.services import depop, ebay_trading, etsy

CREDS = {"access_token": "tok", "ebay_username": "seller", "_uid": "u1"}
LIVE_ITEM = "110011223344"


def _ctx(prev_record=None, **over) -> PublishContext:
    return PublishContext(
        session_id="sess-1",
        listing=Listing(title="Vintage Levi's 501", description="Nice.",
                        price=45.0, quantity=1, images=["img_000.jpg"], **over),
        mode="live", base_url="https://app.test", uid="u1",
        prev_record=prev_record or {})


@pytest.fixture()
def quiet(monkeypatch):
    """Everything around the one eBay call this file is about."""
    monkeypatch.setattr(ebay_provider.db, "upsert_listing", lambda *a, **k: True)
    monkeypatch.setattr(ebay_provider, "_record_published", lambda *a, **k: True)
    monkeypatch.setattr(ebay_provider, "preflight_issues", lambda *a, **k: [])
    monkeypatch.setattr(ebay_provider.ebay, "image_urls_for",
                        lambda *a, **k: ["https://app.test/media/img_000.jpg"])
    monkeypatch.setattr(ebay_provider.storage, "save_listing", lambda *a, **k: None)
    monkeypatch.setattr(ebay_provider.storage, "optimized_dir",
                        lambda sid: pathlib.Path("/nonexistent") / sid)
    return monkeypatch


def _raises(exc):
    def _boom(*_a, **_k):
        raise exc
    return _boom


def _lost():
    return ebay_trading.UnknownOutcome(
        "The request reached eBay and the answer didn't come back.",
        call="AddFixedPriceItem")


def _refused():
    return ebay_trading.TradingError("Add a package weight.", code="21916564")


# ------------------------------------------------------- a NEW live listing

@pytest.mark.parametrize("make_exc, unknown",
                         [(_lost, True), (_refused, False)])
def test_a_create_says_whether_its_outcome_is_known(quiet, make_exc, unknown):
    quiet.setattr(ebay_provider.listing_sync, "create_on_ebay",
                  _raises(make_exc()))

    out = ebay_provider.EbayProvider()._publish_locked(_ctx(), CREDS)

    assert out.ok is False
    assert out.outcome_unknown is unknown


@pytest.mark.parametrize("make_exc, unknown",
                         [(_lost, True), (_refused, False)])
def test_the_legacy_body_carries_it_too(quiet, make_exc, unknown):
    """`raw` is what /api/publish returns verbatim for a single-eBay publish,
    and a single-eBay publish is what both bulk queues send."""
    quiet.setattr(ebay_provider.listing_sync, "create_on_ebay",
                  _raises(make_exc()))

    out = ebay_provider.EbayProvider()._publish_locked(_ctx(), CREDS)

    assert out.raw.get("outcome_unknown", False) is unknown


def test_a_lost_create_is_never_titled_a_rejection(quiet):
    """The words and the flag have to agree; they are read by different
    surfaces and either one alone sends the seller somewhere wrong."""
    quiet.setattr(ebay_provider.listing_sync, "create_on_ebay",
                  _raises(_lost()))

    out = ebay_provider.EbayProvider()._publish_locked(_ctx(), CREDS)

    titles = " ".join(i.get("title", "") for i in out.issues).lower()
    assert "reject" not in titles
    assert out.issues, "an unknown outcome still has to say something"


# ----------------------------------------------- an existing (imported) one

@pytest.mark.parametrize("make_exc, unknown",
                         [(_lost, True), (_refused, False)])
def test_a_revise_says_whether_its_outcome_is_known(quiet, make_exc, unknown):
    """The same question on the other write. A revise that eBay took but did
    not answer for leaves the seller's edit in an unknown state, and 'refused'
    invites the same blind retry."""
    quiet.setattr(ebay_provider.listing_sync, "push_edit", _raises(make_exc()))
    quiet.setattr(ebay_provider.image_import, "images_changed",
                  lambda *a, **k: True)
    ctx = _ctx(prev_record={"status": "published"},
               source="ebay", ebay_listing_id=LIVE_ITEM)

    out = ebay_provider.EbayProvider()._publish_locked(ctx, CREDS)

    assert out.ok is False
    assert out.outcome_unknown is unknown
    assert out.raw.get("outcome_unknown", False) is unknown


# ------------------------------------------------------------- the contract

def test_a_publish_is_known_unless_it_says_otherwise():
    """The default is the safe one: a provider that has not thought about
    this reports a knowable outcome, which is what every ok=True publish and
    every preflight refusal already is."""
    assert PublishOutcome(ok=True).outcome_unknown is False
    assert PublishOutcome(ok=False).outcome_unknown is False


def test_every_client_that_can_lose_an_answer_can_be_asked(quiet):
    """The providers all read the flag off the exception with the same
    getattr, so each client has to spell it the same way. This is the
    coverage half of that, per client rather than per provider."""
    for module in (ebay_trading, etsy, depop):
        assert module.UnknownOutcome.outcome_unknown is True
        assert module.UnknownOutcome.__mro__[1].outcome_unknown is False

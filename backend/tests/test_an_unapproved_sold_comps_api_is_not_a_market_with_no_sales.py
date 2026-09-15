"""A source eBay has not switched on is not a market with nothing in it.

An asking price is what somebody hopes for. A sold price is what somebody
paid, and for art the gap between them is enormous: an unsold print can sit at
an optimistic number for years, and every one of those listings is a "comp" to
a keyword search. So `pricing.sold_comps` is implemented at last, against
eBay's Marketplace Insights API -- and `suggest` prefers it automatically,
because it has always been first in _SOURCES.

The catch is that Marketplace Insights is a LIMITED RELEASE. The credentials
work and the endpoint exists, and it answers 403 until eBay has approved this
application for the buy.marketplace.insights scope. Approval is per
application and is applied for, so "not approved yet" is the normal state for
most installs, indefinitely.

That has to survive to the seller intact. test_a_failed_price_lookup_is_not_no_comps
already established the vocabulary: say whether we actually looked
(`checked`), and never let a failure answer as a fact about the market. An
unapproved 403 is a third thing again -- not "no sales" and not "the lookup
broke", but "this source is not turned on" -- and the one outcome that must
never happen is a price card that says "no comparable listings found" because
of it.

And it must not cost a request per item forever either, against an endpoint
that will refuse every one of them. So the 403 latches: the first one raises,
so `checked` tells the truth about that call, and after that the source
short-circuits.
"""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("httpx")

import httpx  # noqa: E402

from backend.services import pricing  # noqa: E402


@pytest.fixture(autouse=True)
def _unlatched():
    pricing.reset_insights_latch()
    yield
    pricing.reset_insights_latch()


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


# --- reading real sold prices ----------------------------------------------

def test_a_sold_price_is_labelled_as_one_and_marked_sold_data():
    """The editor shows the basis, and "what it sold for" is a different
    claim from "what people are asking"."""
    out = pricing.parse_sold({"itemSales": [
        {"lastSoldPrice": {"value": "120.00"}, "title": "a"},
        {"lastSoldPrice": {"value": "80.00"}, "title": "b"},
        {"lastSoldPrice": {"value": "200.00"}, "title": "c"},
        {"lastSoldPrice": {"value": "150.00"}, "title": "d"},
    ]}, "chagall lithograph")
    assert out["source"] == "sold_comps"
    assert out["sold_data"] is True
    assert "actually sold for" in out["label"]
    assert out["estimate"] == 135.0 and out["count"] == 4
    # The link a seller can click to check the number themselves.
    assert "LH_Sold=1" in out["search_url"]


def test_a_row_with_no_sold_price_is_not_a_sale():
    out = pricing.parse_sold({"itemSales": [
        {"title": "never sold"},
        {"lastSoldPrice": {"value": "0"}, "title": "zero"},
        {"lastSoldPrice": {"value": "not a number"}, "title": "junk"},
        {"lastSoldPrice": {"value": "42.00"}, "title": "real"},
    ]}, "x")
    assert out["count"] == 1 and out["estimate"] == 42.0


def test_a_search_that_genuinely_found_nothing_is_none():
    """None here is a FACT about the market, and it is the only thing that may
    say so -- which is why it has to be kept apart from the 403 below."""
    assert pricing.parse_sold({"itemSales": []}, "x") is None
    assert pricing.parse_sold({}, "x") is None
    assert pricing.parse_sold(None, "x") is None


# --- and the source that is simply not switched on --------------------------

def test_an_unapproved_scope_raises_rather_than_reporting_an_empty_market(
        monkeypatch):
    monkeypatch.setattr(pricing.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(pricing, "_app_token", lambda: "t")
    monkeypatch.setattr(pricing.httpx, "get", lambda *a, **k: _Resp(403))
    with pytest.raises(pricing.InsightsNotApproved):
        pricing.sold_comps("chagall lithograph")


def test_a_failed_sold_lookup_still_leaves_checked_true_through_suggest(
        monkeypatch):
    """The whole point. `suggest` records the failure, active_comps answers,
    and the seller gets asking prices plus an honest `checked` -- never "no
    comparable listings found"."""
    monkeypatch.setattr(pricing.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(pricing, "_app_token", lambda: "t")

    def _denied(*a, **k):
        raise pricing.InsightsNotApproved("nope")

    def _active(query, **kw):
        return {"source": "active_comps", "label": "Live asking prices on eBay",
                "sold_data": False, "estimate": 40.0, "low": 30.0, "high": 55.0,
                "count": 9, "sample": [], "search_url": "https://ebay.test"}

    monkeypatch.setattr(pricing, "_SOURCES", (_denied, _active))
    out = pricing.suggest("chagall lithograph")
    assert out["checked"] is True
    assert out["suggestion"]["sold_data"] is False
    assert out["suggestion"]["price"]


def test_every_source_failing_is_reported_as_not_having_looked(monkeypatch):
    """The case test_a_failed_price_lookup_is_not_no_comps exists for, with a
    sold-comps denial as one of the failures."""
    def _denied(*a, **k):
        raise pricing.InsightsNotApproved("nope")

    def _broken(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(pricing, "_SOURCES", (_denied, _broken))
    out = pricing.suggest("anything")
    assert out["checked"] is False
    assert out["suggestion"] is None


def test_the_denial_latches_so_it_does_not_cost_a_request_per_item(monkeypatch):
    """Approval is per application and indefinite, so without a latch every
    draft in every batch pays a round trip to be refused."""
    monkeypatch.setattr(pricing.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(pricing, "_app_token", lambda: "t")
    calls = []

    def _get(*a, **k):
        calls.append(1)
        return _Resp(403)

    monkeypatch.setattr(pricing.httpx, "get", _get)
    with pytest.raises(pricing.InsightsNotApproved):
        pricing.sold_comps("first")
    # Every later call short-circuits -- and returns None rather than raising,
    # because by now it is a known-off source, not a failure to report again.
    assert pricing.sold_comps("second") is None
    assert pricing.sold_comps("third") is None
    assert len(calls) == 1
    assert pricing.insights_enabled() is False


def test_a_granted_approval_is_picked_up_after_a_restart(monkeypatch):
    """The latch is process-level, which is when a newly granted scope would
    be picked up anyway."""
    monkeypatch.setattr(pricing.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(pricing, "_app_token", lambda: "t")
    monkeypatch.setattr(pricing.httpx, "get", lambda *a, **k: _Resp(403))
    with pytest.raises(pricing.InsightsNotApproved):
        pricing.sold_comps("x")
    assert not pricing.insights_enabled()
    pricing.reset_insights_latch()
    assert pricing.insights_enabled()


def test_sold_comps_is_still_preferred_over_asking_prices():
    """It is first in _SOURCES, and suggest takes the first source that
    answers. A sold price beats an asking price whenever both exist."""
    assert pricing._SOURCES[0] is pricing.sold_comps


def test_the_denial_is_not_filed_as_a_production_error(monkeypatch, caplog):
    """The normal state for most installs does not belong in the error feed.

    Error capture starts at WARNING (see README, "Reading production errors"),
    because this codebase fails soft and the real failures are logged there.
    `suggest`'s catch-all logged EVERY source failure at that level, so the
    one condition this whole module exists to expect -- eBay not having
    approved the application -- filed itself as a bug report once per process
    restart. It was in the production feed on 2026-09-15, graded worth a fix,
    against code doing exactly the right thing.

    The failure is still RECORDED (`checked` below still tells the truth about
    whether anything got to look); it is just not reported as actionable.
    """
    def _denied(*a, **k):
        raise pricing.InsightsNotApproved("nope")

    monkeypatch.setattr(pricing, "_SOURCES", (_denied,))
    with caplog.at_level(logging.INFO, logger=pricing.log.name):
        out = pricing.suggest("chagall lithograph")

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert any(r.levelno == logging.INFO for r in caplog.records)
    # And the seller's card is unchanged by the quieter level: nothing looked,
    # so nothing is claimed about the market.
    assert out["checked"] is False
    assert out["suggestion"] is None


def test_a_real_source_failure_is_still_a_warning(monkeypatch, caplog):
    """The other half. Quieting the expected case must not quiet a lookup that
    genuinely broke -- a dead app token or a 429 is exactly what the feed is
    for, and is the reason the catch-all logs at WARNING in the first place."""
    def _broken(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(pricing, "_SOURCES", (_broken,))
    with caplog.at_level(logging.INFO, logger=pricing.log.name):
        pricing.suggest("anything")

    assert [r for r in caplog.records if r.levelno >= logging.WARNING]

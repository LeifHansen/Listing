""""We couldn't check" is not "there's nothing like this on eBay".

`pricing.suggest` runs each comp source inside a try/except and keeps going —
correct, because one dead source should not fail the call. What it did not do
is say that a source had died. A network blip, a 429 against the shared
application quota, or an expired app token all produced exactly the shape a
successful search over an empty market produces: `sources: []`,
`suggestion: null`.

The two screens reading that answer then stated it as fact:

  * the editor's price card — "No comparable listings found — try a simpler
    title or set a category first", which also sends the seller off to edit
    a listing that was never the problem;
  * Shop Mode — "No price estimate yet", to someone standing in a shop
    deciding whether to spend their own money on the item.

Same rule as the store read that reported an empty store, and the same
vocabulary the delete-account preview already uses for it: say whether we
actually looked (`checked`), and never let a failure answer as a fact about
the market.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from backend.services import pricing  # noqa: E402


def _sources(monkeypatch, *fns):
    monkeypatch.setattr(pricing, "_SOURCES", fns)


def _comps(**kw):
    base = {"source": "active_comps", "label": "Live asking prices on eBay",
            "sold_data": False, "estimate": 40.0, "low": 30.0, "high": 55.0,
            "count": 9, "sample": [], "search_url": "https://ebay.test/sch"}
    base.update(kw)
    return base


def test_an_empty_market_is_a_real_answer(monkeypatch):
    """eBay answered and had nothing comparable. That IS the finding."""
    _sources(monkeypatch, lambda *a, **k: None)
    out = pricing.suggest("obscure thing")
    assert out["suggestion"] is None
    assert out["checked"] is True


def test_a_source_that_raised_is_not_an_empty_market(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("eBay returned 429 for item_summary/search")

    _sources(monkeypatch, _boom)
    out = pricing.suggest("vintage levis 501")
    assert out["suggestion"] is None
    assert out["checked"] is False, (
        "a failed lookup reported itself as a market with no comparable items")


def test_one_live_source_still_counts_as_checked(monkeypatch):
    """A dead source alongside a live one is not a failed lookup — we have a
    real answer and reporting otherwise would bury it."""
    def _boom(*a, **k):
        raise RuntimeError("down")

    _sources(monkeypatch, _boom, lambda *a, **k: _comps())
    out = pricing.suggest("vintage levis 501")
    assert out["checked"] is True
    assert out["suggestion"]["price"] == 40.0


def test_a_source_returning_nothing_beside_one_that_raised_is_still_unknown(
        monkeypatch):
    """The quiet one found nothing; the loud one never got to look. Nobody
    can say the market is empty from that."""
    def _boom(*a, **k):
        raise RuntimeError("down")

    _sources(monkeypatch, lambda *a, **k: None, _boom)
    out = pricing.suggest("vintage levis 501")
    assert out["suggestion"] is None
    assert out["checked"] is False


def test_the_answer_still_carries_everything_it_used_to(monkeypatch):
    """`checked` is added, not swapped in for something a client reads."""
    _sources(monkeypatch, lambda *a, **k: _comps())
    out = pricing.suggest("vintage levis 501", strategy="quick_flip")
    assert out["query"] == "vintage levis 501"
    assert out["strategy"] == "quick_flip"
    assert out["sources"] and out["suggestion"]["price"] == 30.0
    assert out["suggestion"]["basis"].startswith("Live asking prices on eBay")

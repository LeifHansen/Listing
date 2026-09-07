"""The AI's own price, and the two numbers beside it that are not ours.

The seller asked for every price to end in .99 rather than being a whole
number; money.charm_price is that rule and test_a_price_this_app_chooses_
ends_in_99.py covers its arithmetic. This is the half that needs the AI paths:
a drafted price is shaped by it, while what the seller PAID (a fact read off a
price sticker) and a price the seller TYPED (echoed back through the same
parser by a refine) must come through untouched.

Needs the anthropic package for the import alone -- every call here is
stubbed. It runs in the smoke job, which installs from requirements.txt; the
fast unit job has no anthropic and skips the file whole.
"""
from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("anthropic")

from backend.models import Listing            # noqa: E402
from backend.services import claude_ai        # noqa: E402


def _draft(**fields) -> Listing:
    return claude_ai._to_listing({"title": "A thing", **fields}, ["img_000.jpg"])


def test_the_drafted_price_is_a_price_a_seller_would_have_written():
    assert _draft(price=25).price == 24.99


def test_a_draft_with_no_price_still_has_none():
    """null is the question the comps lookup answers; 0.99 would be an answer,
    and the wrong one."""
    assert _draft(price=None).price is None


def test_what_the_seller_paid_is_a_fact_not_a_price_we_choose():
    """purchase_price comes off a price sticker in the photos. Charming it
    would make the profit line lie about what the item cost."""
    listing = _draft(price=25, purchase_price=4.00)
    assert (listing.price, listing.purchase_price) == (24.99, 4.00)


def _answers(payload: dict):
    """A stubbed model that hands back exactly this listing JSON."""
    resp = types.SimpleNamespace(
        stop_reason="end_turn",
        content=[types.SimpleNamespace(type="text", text=json.dumps(payload))],
    )
    return types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: resp))


def test_a_refine_does_not_move_a_price_the_seller_set(monkeypatch):
    """A refine echoes the WHOLE draft back, so "make the title punchier"
    would otherwise hand a $25.00 the seller typed back as $24.99 -- the rule
    reaching a number it does not own."""
    listing = Listing(title="A thing", price=25.00, images=["img_000.jpg"])
    monkeypatch.setattr(claude_ai, "_client",
                        lambda: _answers({"title": "A better thing",
                                          "price": 25.00}))

    out = claude_ai.refine(listing, "make the title punchier")

    assert (out.title, out.price) == ("A better thing", 25.00)


def test_a_refine_that_actually_prices_it_lands_on_a_99(monkeypatch):
    """A number the instruction moved is a number this app chose."""
    listing = Listing(title="A thing", price=25.00, images=["img_000.jpg"])
    monkeypatch.setattr(claude_ai, "_client",
                        lambda: _answers({"title": "A thing", "price": 40}))

    assert claude_ai.refine(listing, "price it like the good one").price == 39.99

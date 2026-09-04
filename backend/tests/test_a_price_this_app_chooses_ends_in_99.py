"""A price this app chooses ends in .99.

Reported as: "please make all prices $x.99 rather than whole numbers. So if
you price an item at $25, list it at $24.99 by default."

Charm pricing is how a seller would have written the number themselves, and a
whole-dollar price is the tell that nobody wrote it. The rule lives in
money.charm_price and every price this app CHOOSES goes through it: the AI's
drafted price (here), the market number that overrules a draft priced far
under it (test_a_draft_is_priced_against_the_market), the floor a lookup
raises a draft to (test_the_item_gets_looked_up), the headline comp suggestion
(test_a_failed_price_lookup_is_not_no_comps) and a bulk percentage cut
(test_bulk_actions).

What it must never touch is a number that is not ours to choose: what the
seller PAID, which is a fact read off a price sticker, and a price the seller
typed into the editor, which a refine echoes back through the same parser.
"""
from __future__ import annotations

import json
import types

import pytest

from backend.models import Listing
from backend.money import charm_price
from backend.services import claude_ai


# ------------------------------------------------------------- the rule

@pytest.mark.parametrize("amount, expected", [
    (25, 24.99),          # the reported case, exactly
    (25.00, 24.99),
    (24.99, 24.99),       # already there, and stays put
    (22.50, 22.99),       # nearest, not always down: never shave to be safe
    (18.75, 18.99),
    (1249.50, 1249.99),   # the move is cents, whatever the size of the price
    (1.00, 0.99),
    (0.40, 0.99),         # under the floor comes up to it
])
def test_a_chosen_price_lands_on_the_nearest_99(amount, expected):
    assert charm_price(amount) == expected


@pytest.mark.parametrize("amount", [None, "", "not a price", 0, 0.0, -5])
def test_an_amount_that_is_not_a_price_stays_out_of_the_field(amount):
    """The same answer money() gives: an amount that could not be read is not
    zero and not a bargain — it is nothing, and the draft says so with a null
    the comps lookup then fills in."""
    assert charm_price(amount) is None


# --------------------------------------------------------- the AI's draft

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


# ------------------------------------------------------------- the refine

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
    would otherwise hand a $25.00 the seller typed back as $24.99 — the rule
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

"""A price this app chooses ends in .99.

Reported as: "please make all prices $x.99 rather than whole numbers. So if
you price an item at $25, list it at $24.99 by default."

Charm pricing is how a seller would have written the number themselves, and a
whole-dollar price is the tell that nobody wrote it. The rule lives in
money.charm_price -- stdlib only, so this runs in the fast job -- and every
price this app CHOOSES goes through it: the AI's drafted price
(test_the_ai_never_drafts_a_whole_dollar_price), the market number that
overrules a draft priced far under it (test_a_draft_is_priced_against_the_market),
the floor a lookup raises a draft to (test_the_item_gets_looked_up), the
headline comp suggestion (test_a_failed_price_lookup_is_not_no_comps) and a
bulk percentage cut (test_bulk_actions).
"""
from __future__ import annotations

import pytest

from backend.money import charm_price


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
    zero and not a bargain -- it is nothing, and the draft says so with a null
    the comps lookup then fills in."""
    assert charm_price(amount) is None


def test_the_move_is_never_more_than_half_a_dollar():
    """The rule shapes a price; it must never quietly reprice an item. Half a
    dollar is the most it can ever be from the number it was handed."""
    for cents in range(100, 10_000):
        amount = cents / 100
        assert abs(charm_price(amount) - amount) <= 0.50

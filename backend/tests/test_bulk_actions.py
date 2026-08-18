"""Applying a suggested action to a whole group at once.

A bulk edit has failure modes a single edit doesn't: it runs over listings the
recommendation engine grouped a while ago (some have since sold), one rejected
eBay call must not strand the rest, and a mistyped percentage would otherwise
reprice a store in one click. These cover that behaviour.
"""
from __future__ import annotations

import pytest

from backend.services import bulk_actions


# ------------------------------------------------------------- the arithmetic

@pytest.mark.parametrize("price, percent, expected", [
    (100.0, 10, 90.0),
    (59.99, 10, 53.99),      # rounded to cents, the way a seller writes it
    (45.0, 25, 33.75),
    (19.99, 15, 16.99),
    (10.0, 50, 5.0),
])
def test_a_percentage_off(price, percent, expected):
    assert bulk_actions.lower_price(price, percent) == expected


def test_the_floor_holds():
    """A big cut on a cheap item can't produce a 12-cent listing."""
    assert bulk_actions.lower_price(1.20, 75) == bulk_actions.MIN_PRICE


def test_a_cut_that_changes_nothing_is_not_a_change():
    """Counting no-ops as 'changed' would report work that never happened."""
    assert bulk_actions.lower_price(bulk_actions.MIN_PRICE, 10) is None
    assert bulk_actions.lower_price(1.00, 0.1) is None  # rounds back to 1.00


def test_a_listing_with_no_real_price_is_left_alone():
    assert bulk_actions.lower_price(0, 10) is None
    assert bulk_actions.lower_price(None, 10) is None
    assert bulk_actions.lower_price("", 10) is None
    assert bulk_actions.lower_price("not a price", 10) is None


# -------------------------------------------------------- the percentage gate

@pytest.mark.parametrize("value", [10, "10", 10.5, 0.5, bulk_actions.MAX_PERCENT])
def test_sensible_percentages_pass(value):
    assert bulk_actions.validate_percent(value) == float(value)


@pytest.mark.parametrize("value", [0, -5, "", None, "abc"])
def test_nonsense_percentages_are_refused(value):
    with pytest.raises(ValueError):
        bulk_actions.validate_percent(value)


def test_a_slipped_decimal_point_is_refused():
    """"90" where "9" was meant would gut a store's pricing in one click."""
    with pytest.raises(ValueError) as caught:
        bulk_actions.validate_percent(90)
    assert "capped" in str(caught.value)


# ------------------------------------------------------------------- the run

def _rec(rid, title="A thing", price=50.0, status="published"):
    return {"id": rid, "status": status,
            "listing": {"title": title, "price": price}}


def test_every_listing_gets_its_own_verdict():
    records = [_rec("a"), _rec("b"), _rec("c")]

    def apply_one(rec):
        if rec["id"] == "a":
            return {"ok": True, "was": 50.0, "now": 45.0}
        if rec["id"] == "b":
            return {"skip": "No longer live on eBay."}
        return {"message": "eBay said no."}

    result = bulk_actions.run(records, apply_one)
    assert [c["listing_id"] for c in result.changed] == ["a"]
    assert [s["listing_id"] for s in result.skipped] == ["b"]
    assert [f["listing_id"] for f in result.failed] == ["c"]
    assert result.as_dict()["total"] == 3


def test_one_listings_blow_up_does_not_strand_the_rest():
    """The whole value of a bulk action is not having to babysit it."""
    records = [_rec("a"), _rec("boom"), _rec("c")]
    seen = []

    def apply_one(rec):
        seen.append(rec["id"])
        if rec["id"] == "boom":
            raise RuntimeError("eBay timed out")
        return {"ok": True}

    result = bulk_actions.run(records, apply_one)
    assert seen == ["a", "boom", "c"], "the run stopped at the failure"
    assert len(result.changed) == 2
    assert result.failed[0]["message"] == "eBay timed out"


def test_the_outcome_carries_what_changed():
    """The UI reports "$59.99 → $53.99", so the numbers have to come back."""
    result = bulk_actions.run(
        [_rec("a", price=59.99)],
        lambda rec: {"ok": True, "was": 59.99, "now": 53.99})
    assert result.changed[0]["was"] == 59.99
    assert result.changed[0]["now"] == 53.99
    assert result.changed[0]["title"] == "A thing"

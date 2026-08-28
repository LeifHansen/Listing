"""eBay's ConditionID must survive a round trip through this app.

A listing imported from eBay carries a numeric ConditionID. The app stores it
as an enum on `Listing.condition`, and a later revise sends an id back. If the
two directions disagree about any id, every edit silently changes the item's
condition on eBay — a seller-visible, seller-damaging change that nothing in
the UI reports.

That is what 2750 (Like New) did. `ebay_trading` kept its own id->enum map
reading 2750 as USED_EXCELLENT, while its enum->id map sent LIKE_NEW back as
2750 and `taxonomy` — the module that builds the condition dropdown from eBay's
own metadata — read 2750 as LIKE_NEW. So importing a Like New listing and
saving any edit at all downgraded it to Used - Excellent (3000).
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading, taxonomy

# Ids that legitimately share an enum with another id: eBay has several
# refurbished grades that this app does not distinguish, so id -> enum -> id
# lands on the canonical one rather than where it started. Everything else
# must be exact.
SHARED_ENUM_IDS = {"2010", "2020", "2030"}


def test_the_two_modules_share_one_map():
    """One map, not two copies free to drift.

    Parameterisation below deliberately reads `_CONDITION_BY_ID`, which exists
    under that name before and after the fix, so the round-trip cases run
    against either implementation instead of failing at import.
    """
    assert ebay_trading._CONDITION_BY_ID is taxonomy.CONDITION_ID_TO_ENUM


@pytest.mark.parametrize("cid", sorted(
    set(ebay_trading._CONDITION_BY_ID) - SHARED_ENUM_IDS))
def test_a_condition_id_survives_import_then_revise(cid):
    """id -> enum -> id is identity for every id the app doesn't collapse.

    Fails against the old code for 2750: it read as USED_EXCELLENT and went
    back to eBay as 3000.
    """
    enum = ebay_trading._CONDITION_BY_ID[cid]
    assert ebay_trading._CONDITION_TO_ID.get(enum) == cid


def test_like_new_is_2750_in_both_directions():
    """The specific corruption, named. eBay's 'Like New' is 2750 — a distinct
    grade from 'Used - Excellent' (3000), used in media and trading-card
    categories where the difference is most of the price."""
    assert ebay_trading._CONDITION_BY_ID["2750"] == "LIKE_NEW"
    assert ebay_trading._CONDITION_TO_ID["LIKE_NEW"] == "2750"

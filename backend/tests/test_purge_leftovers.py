"""The two safety rules in scripts/purge_inventory_leftovers.py.

That script deletes inventory items off a seller's real eBay account, so the
interesting question is not what it removes but what it must never touch:

  - anything the seller made with another tool, and
  - anything that is a live listing.

Both are one-line predicates, which is exactly why they are worth pinning: a
wrong answer here ends listings a seller is selling from.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_PATH = (pathlib.Path(__file__).resolve().parents[2]
         / "scripts" / "purge_inventory_leftovers.py")
_spec = importlib.util.spec_from_file_location("purge_leftovers", _PATH)
purge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(purge)


# --- only this app's inventory is ever a candidate --------------------------

@pytest.mark.parametrize("sku", [
    "THRYFT-abc123", "thryft-abc123", "THRYFT-ABC", "THRYFT-",
])
def test_our_own_skus_are_candidates(sku):
    assert purge.is_app_sku(sku)


@pytest.mark.parametrize("sku", [
    "", "MYSTORE-1", "ABC-THRYFT-1", "thryf-1", "SKU123",
    "-THRYFT-1", " THRYFT-1",
])
def test_everything_else_is_left_alone(sku):
    """A seller's own inventory, from any other tool, must never be a
    candidate — including a SKU that merely CONTAINS the prefix."""
    assert not purge.is_app_sku(sku)


# --- a live listing is never deleted ---------------------------------------

def test_a_published_offer_marks_the_item_untouchable():
    ids = purge.live_listing_ids(
        [{"offerId": "o1", "status": "PUBLISHED",
          "listing": {"listingId": "110000000001"}}])
    assert ids == ["110000000001"]


def test_an_unpublished_offer_is_safe_to_remove():
    """The draft leftovers this script exists for."""
    assert purge.live_listing_ids([{"offerId": "o1", "status": "UNPUBLISHED"}]) == []


def test_an_item_with_no_offers_at_all_is_safe():
    assert purge.live_listing_ids([]) == []


@pytest.mark.parametrize("offer", [
    {"offerId": "o1"},                       # no status at all
    {"offerId": "o1", "status": ""},         # empty
    {"offerId": "o1", "status": "SOMETHING"},  # a status eBay added later
    {"offerId": "o1", "status": None},
])
def test_an_unreadable_status_counts_as_live(offer):
    """Deliberately not "anything that isn't PUBLISHED is safe". A status this
    script doesn't recognise must stop it, not wave it through — the cost of
    skipping litter is nothing, and the cost of deleting a live listing is a
    seller's listing."""
    assert purge.live_listing_ids([offer]) == ["o1"]


def test_one_published_offer_protects_the_whole_item():
    """Offers share the inventory item; deleting the item takes the live one
    with it, however many unpublished siblings it has."""
    assert purge.live_listing_ids([
        {"offerId": "o1", "status": "UNPUBLISHED"},
        {"offerId": "o2", "status": "PUBLISHED", "listing": {"listingId": "99"}},
    ]) == ["99"]

"""A save carries the seller's edits, not the server's bookkeeping.

`_restore_server_state` exists because a client's copy of a listing can only
be as fresh as the moment it loaded, and honouring a stale copy erases things
the server owns. It protected `source`, `view_url` and `ebay_account`. Several
fields that decide equally consequential things were left to whatever arrived:

  - `ebay_account_id` is the immutable eBay account id that decides which
    listings this app may sync, revise and end. P0-04 made ownership key on it
    precisely BECAUSE it cannot be renamed — and then a save could set it.
    Getting it wrong points the app at the wrong seller's listings.
  - `sku` is the idempotency key a publish is recovered by (P0-07). Changing
    it breaks the duplicate check that stops a retried publish minting a
    second live listing.
  - `has_variations` is the flag that stops this app rewriting a listing whose
    shape it cannot represent.
  - `sold_price`, `sold_quantity`, `watch_count` and `ebay_start_time` are
    eBay's own numbers, and two of them add up to the Sold total the seller
    reads as fact.

And `dirty_fields`: a save UNIONED the client's claim into the server's. Those
names decide which fields a revise sends, and every field it sends overwrites
whatever eBay has now — so a client naming extra ones could push stale
snapshots over newer Seller Hub edits, which is the P0-08 harm reached from
the other end. The server derives them by diffing against its own stored copy;
it does not need to be told.

The rule these all follow: a stored value wins, a stored blank leaves the
client's alone. Nothing is frozen out on a record that has none yet.
"""
from __future__ import annotations

import pytest

from backend.marketplaces.state import SERVER_OWNED_FIELDS, restore_server_fields
from backend.models import Listing
from backend.services import dirty_fields


@pytest.mark.parametrize("field,stored,forged", [
    ("ebay_account_id", "EBAYUSER-REAL", "EBAYUSER-SOMEONE-ELSE"),
    ("sku", "thryft-abc123", "thryft-something-else"),
    ("has_variations", True, False),
    ("sold_price", 25.0, 9999.0),
    ("sold_quantity", 3, 0),
    ("watch_count", 12, 0),
    ("ebay_start_time", "2026-01-01T00:00:00Z", "2020-01-01T00:00:00Z"),
])
def test_a_stored_value_wins_over_the_payload(field, stored, forged):
    listing = Listing(title="Blue lamp", **{field: forged})

    restore_server_fields(listing, {field: stored})

    assert getattr(listing, field) == stored, f"{field} was taken from the client"


@pytest.mark.parametrize("field", [
    "ebay_account_id", "sku", "has_variations", "sold_price",
    "sold_quantity", "watch_count", "ebay_start_time",
])
def test_each_one_is_actually_in_the_list(field):
    assert field in SERVER_OWNED_FIELDS


def test_a_record_with_none_yet_can_still_be_stamped():
    """A stored blank leaves the client's value alone — that is how a first
    publish stamps these at all."""
    listing = Listing(title="Blue lamp", ebay_account_id="EBAYUSER-1")

    restore_server_fields(listing, {})

    assert listing.ebay_account_id == "EBAYUSER-1"


def test_the_seller_s_own_fields_are_untouched():
    """The list must stay narrow: it protects bookkeeping, not the listing."""
    for name in SERVER_OWNED_FIELDS:
        assert name not in ("title", "description", "price", "quantity",
                            "condition", "category_id", "item_specifics")


# --------------------------------------------------------- dirty fields

def test_the_client_cannot_name_fields_as_edited():
    """Every field a revise sends overwrites whatever eBay has now. A client
    naming one it did not change pushes a stale snapshot over newer work."""
    stored = {"title": "Blue lamp", "price": 25.0, "description": "A lamp."}
    incoming = Listing(**stored)
    incoming.dirty_fields = ["title", "description"]  # nothing actually changed

    dirty_fields.accumulate(incoming, stored)

    assert incoming.dirty_fields == [], \
        "a client's claim about what it edited was believed"


def test_a_real_edit_is_still_recorded():
    stored = {"title": "Blue lamp", "price": 25.0}
    incoming = Listing(**{**stored, "price": 30.0})

    dirty_fields.accumulate(incoming, stored)

    assert incoming.dirty_fields == ["price"]


def test_edits_from_earlier_saves_still_accumulate():
    """The property this function exists for: change the price, save; change
    the title, save; publish — and the revise carries both."""
    stored = {"title": "Blue lamp", "price": 25.0, "dirty_fields": ["price"]}
    incoming = Listing(**{**stored, "title": "Blue ceramic lamp"})

    dirty_fields.accumulate(incoming, stored)

    assert incoming.dirty_fields == ["price", "title"]

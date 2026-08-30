"""Every field on a listing is either the seller's to send or the server's to keep.

`SERVER_OWNED_FIELDS` is a hand-written list, and the way it goes wrong is by
omission: this branch already found `remote_shadow` and `conflicts` missing —
so a save from a tab opened before the first sync erased the base the
three-way merge reconciles against — and four more while pulling that thread,
including the immutable id P0-04 decides ownership on.

Nothing stopped the next one. A field added to `Listing` is client-settable by
default, because every save round-trips the whole document; whether that is
right is a decision, and this makes it one somebody has to record.

The two lists below must together cover the model exactly. Add a field and
this fails until it is named in one of them.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.marketplaces.state import SERVER_OWNED_FIELDS  # noqa: E402
from backend.models import Listing  # noqa: E402

# What a seller (or the editor on their behalf) legitimately sends. Everything
# here is either something they type, something they choose, or something the
# app derives locally from their photos.
SELLER_FIELDS = {
    # what they write
    "title", "subtitle", "description", "brand", "condition",
    "condition_description", "category_suggestion", "category_id",
    "item_specifics", "missing_info",
    # what they price and stock
    "price", "purchase_price", "currency", "quantity", "listing_format",
    "auction_start_price", "auction_duration",
    # what they ship in
    "package_weight_lb", "package_weight_oz", "package_length_in",
    "package_width_in", "package_height_in", "fulfillment_policy_id",
    # their photos, as local working copies
    "images",
    # money they are agreeing to spend — consent, so never server-supplied
    "promote", "ad_rate_percent",
    # per-marketplace fields the seller fills in
    "etsy", "depop",
    # Guarded elsewhere, deliberately, and each has its own test:
    #   marketplaces / ebay_listing_id -> state.owned_state_from, which merges
    #     rather than replaces (a client's map is missing entries, not wrong)
    #   dirty_fields -> services/dirty_fields, which recomputes from the
    #     stored copy instead of believing what the client names
    "marketplaces", "ebay_listing_id", "dirty_fields",
}


def test_the_two_lists_cover_the_model_exactly():
    fields = set(Listing.model_fields)
    unclassified = fields - set(SERVER_OWNED_FIELDS) - SELLER_FIELDS
    assert not unclassified, (
        "these listing fields are neither server-owned nor listed as the "
        "seller's — decide which, and say so here: "
        + ", ".join(sorted(unclassified)))


def test_neither_list_names_a_field_that_is_gone():
    """A stale name in either list is a check that silently stops applying."""
    fields = set(Listing.model_fields)
    assert not (set(SERVER_OWNED_FIELDS) - fields), (
        "SERVER_OWNED_FIELDS names fields the model no longer has: "
        + ", ".join(sorted(set(SERVER_OWNED_FIELDS) - fields)))
    assert not (SELLER_FIELDS - fields), (
        "SELLER_FIELDS names fields the model no longer has: "
        + ", ".join(sorted(SELLER_FIELDS - fields)))


def test_nothing_is_in_both():
    assert not (set(SERVER_OWNED_FIELDS) & SELLER_FIELDS)


def test_everything_ebay_reports_about_a_sale_is_server_owned():
    """`sold_price`, `sold_quantity` and `sold_at` are one observation in
    three parts — eBay's transaction, or the moment the sync watched the
    listing flip. Two were protected and the third was not, which is how the
    dashboard's "sold in the last N days" tile counted against a date a client
    could set."""
    for field in ("sold_price", "sold_quantity", "sold_at"):
        assert field in SERVER_OWNED_FIELDS, f"{field} is client-settable"

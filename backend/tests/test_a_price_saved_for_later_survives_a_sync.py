"""A price, quantity or photo change waiting to go to eBay survives a sync.

The sync copied price, quantity and image_urls from eBay before the three-way
merge ever looked at them. So a price the seller saved for the next "Update
on eBay" -- or one a refused or unanswered revise left marked to send again --
was replaced by eBay's on the next import, with its dirty mark still set: the
later revise sent eBay its own old price back and reported success. And when
both sides had changed it, eBay's value won without the conflict the merge
exists to raise.

Once a record carries a remote shadow, those three fields go through the
three-way merge like every other tracked field. A record with no shadow yet
keeps the old rule, because without a base the merge reconciles nothing.
"""
from __future__ import annotations

from backend.models import Listing
from backend.services import listing_sync, sync_merge

BASE = dict(title="Blue lamp", description="A lamp.", price=30.0, quantity=1,
            category_id="123", currency="USD", listing_format="FIXED_PRICE",
            condition="USED_GOOD",
            image_urls=["https://i.ebayimg.com/images/g/a/s-l1600.jpg"],
            ebay_listing_id="1234567890", source="ebay")


def _local(shadow=True, **changes):
    listing = Listing(**{**BASE, **changes})
    if changes:
        listing.mark_dirty(*changes)
    out = listing.model_dump()
    if shadow:
        out["remote_shadow"] = sync_merge.shadow_from(BASE)
    return out


def _sync(local, **ebay):
    fresh = {**BASE, **ebay}
    merged = listing_sync._merge(local, fresh)
    return listing_sync._reconcile(local, merged, fresh)


def test_a_price_waiting_to_go_is_not_replaced_by_ebays():
    out = _sync(_local(price=25.0))
    assert out["price"] == 25.0
    assert "price" in out["dirty_fields"]


def test_a_price_changed_on_both_sides_is_a_conflict_not_a_silent_win():
    out = _sync(_local(price=25.0), price=28.0)
    assert out["price"] == 25.0
    assert set(out["conflicts"]) == {"price"}
    assert "price" not in out["dirty_fields"]


def test_a_quantity_and_photos_waiting_to_go_survive_too():
    photos = ["https://i.ebayimg.com/images/g/b/s-l1600.jpg"]
    out = _sync(_local(quantity=3, image_urls=photos))
    assert out["quantity"] == 3
    assert out["image_urls"] == photos


def test_a_change_made_on_ebay_still_arrives():
    out = _sync(_local(), price=28.0, quantity=4)
    assert out["price"] == 28.0
    assert out["quantity"] == 4
    assert not out["conflicts"]


def test_a_record_with_no_shadow_still_takes_ebays_live_facts():
    """No base, no reconcile: the legacy rule is all there is."""
    out = _sync(_local(shadow=False), price=28.0)
    assert out["price"] == 28.0

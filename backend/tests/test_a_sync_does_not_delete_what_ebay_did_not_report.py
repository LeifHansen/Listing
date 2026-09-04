"""A sync must not read "eBay said nothing" as "eBay cleared it".

On 2026-09-03 a seller's listing — published live through this app,
successfully — stopped publishing. The editor said the item specifics card
was Complete, "Required to publish 5/5, all set, nothing here is blocking",
and eBay refused it as missing a required item specific. What had happened
in between was a routine sync, and what the sync did was delete the
listing's eBay category id.

The mechanism. ebay_trading's remote dict is built with `_text(item, path)`,
which returns "" for a node it cannot find, and it sets EVERY key whether or
not the response carried one. So "eBay did not report a category" and "eBay
cleared the category" reached three_way as the same value. The guard above
the merge only skipped a field ABSENT from the dict, which this never is, so
the empty string went through the ordinary rule — remote changed, local
didn't, take eBay's copy — and overwrote a good category id with nothing.

Nothing said so. And the damage is invisible in exactly the wrong way: the
category id is what the required-aspect list is READ from, so a listing that
loses it cannot say which specifics eBay wants, which is why the seller was
left staring at a card claiming nothing was blocking.

The fix is a small honest asymmetry. For the fields eBay cannot report as
empty on a listing that is live — a title, a category, a price, a currency,
a format, a condition, a photo — an empty remote value is a parse that found
nothing, and the local value stands. Fields a seller CAN legitimately empty
on eBay (a subtitle, condition notes, a shipping policy, the item specifics)
are deliberately not covered and still reconcile normally.
"""
from __future__ import annotations

import pytest

from backend.models import Listing  # noqa: E402
from backend.services import sync_merge  # noqa: E402

LIVE = {
    "title": "Panama Jack Camp Shirt XL Tan",
    "category_id": "57990",
    "price": 24.0,
    "currency": "USD",
    "listing_format": "FIXED_PRICE",
    "condition": "USED_EXCELLENT",
    "images": ["img_000.jpg"],
}


def _pair():
    """(local, shadow) for a listing that is live and agreed with eBay."""
    return Listing(**LIVE), dict(LIVE)


# ------------------------------------------------- the reported failure

def test_a_category_ebay_did_not_report_is_not_deleted():
    local, shadow = _pair()
    merged = sync_merge.three_way(local, shadow, dict(shadow, category_id=""))
    assert merged.listing.category_id == "57990"
    assert "category_id" not in merged.took_remote


@pytest.mark.parametrize("name, empty", [
    ("title", ""),
    ("category_id", ""),
    ("price", 0),
    ("currency", ""),
    ("listing_format", ""),
    ("condition", ""),
    ("images", []),
])
def test_nothing_a_live_listing_must_have_is_cleared_by_silence(name, empty):
    local, shadow = _pair()
    merged = sync_merge.three_way(local, shadow, dict(shadow, **{name: empty}))
    assert getattr(merged.listing, name) == LIVE[name], name
    assert name not in merged.took_remote


def test_none_counts_as_silence_too():
    local, shadow = _pair()
    merged = sync_merge.three_way(local, shadow, dict(shadow, price=None))
    assert merged.listing.price == 24.0


# ------------------------------------------ what must still get through

def test_a_real_change_on_ebay_still_lands():
    """The guard is about EMPTY, not about category changes. A seller who
    recategorises on eBay still has that pulled in."""
    local, shadow = _pair()
    merged = sync_merge.three_way(local, shadow, dict(shadow, category_id="15687"))
    assert merged.listing.category_id == "15687"
    assert "category_id" in merged.took_remote


def test_a_field_the_seller_can_really_empty_still_empties():
    """A subtitle removed on eBay is a real edit — this guard must not turn
    every clear into a no-op."""
    local = Listing(**LIVE, subtitle="Vintage Hawaiian")
    shadow = dict(LIVE, subtitle="Vintage Hawaiian")
    merged = sync_merge.three_way(local, shadow, dict(shadow, subtitle=""))
    assert merged.listing.subtitle == ""
    assert "subtitle" in merged.took_remote


def test_a_local_blank_still_takes_ebays_blank():
    """Nothing to protect: if the local value is empty too, the ordinary
    rules apply and no warning is worth logging."""
    local = Listing(**dict(LIVE, category_id=""))
    shadow = dict(LIVE, category_id="")
    merged = sync_merge.three_way(local, shadow, dict(shadow, category_id=""))
    assert merged.listing.category_id == ""


def test_an_edit_the_seller_made_locally_is_still_kept():
    local, shadow = _pair()
    local.category_id = "15687"
    merged = sync_merge.three_way(local, shadow, dict(shadow), dirty={"category_id"})
    assert merged.listing.category_id == "15687"

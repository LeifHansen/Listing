"""Releasing an eBay listing must release eBay, and nothing else.

"Release" unlinks records left behind by a previously-connected eBay account,
so they stop being ghosts of a store the seller no longer has connected. The
records themselves stay — that is the whole promise: the seller's own work,
photos and all, becomes an ordinary local draft.

Dropping the whole `marketplaces` map breaks that promise for every OTHER
marketplace. An item that is also live on Etsy and Depop loses its Etsy and
Depop ids, URLs and statuses too. Nothing on those marketplaces changes, so
the listings stay up and the app simply forgets them: the record still looks
present and correct, and the loss surfaces later as a duplicate publish, or
as an update that silently goes nowhere.
"""
from __future__ import annotations

from backend.services import ebay_account


def _linked_everywhere() -> dict:
    """A record live on all three marketplaces, in both the modern map and
    eBay's legacy top-level projection."""
    return {
        "title": "Blue lamp",
        "ebay_account": "old-seller",
        "ebay_listing_id": "110000000001",
        "source": "ebay",
        "view_url": "https://www.ebay.com/itm/110000000001",
        "sku": "LAMP-1",
        "image_urls": ["https://i.ebayimg.com/1.jpg"],
        "marketplaces": {
            "ebay": {"listing_id": "110000000001", "status": "published",
                     "url": "https://www.ebay.com/itm/110000000001"},
            "etsy": {"listing_id": "9988776655", "status": "published",
                     "url": "https://www.etsy.com/listing/9988776655"},
            "depop": {"listing_id": "dp-42", "status": "published",
                      "url": "https://www.depop.com/products/dp-42"},
        },
    }


def test_etsy_and_depop_survive_an_ebay_release():
    """The finding itself: unlinking eBay took Etsy and Depop with it."""
    before = _linked_everywhere()
    after = ebay_account.unlink_ebay(dict(before))

    assert after["marketplaces"]["etsy"] == before["marketplaces"]["etsy"]
    assert after["marketplaces"]["depop"] == before["marketplaces"]["depop"]


def test_the_ebay_entry_really_is_removed():
    """The half that must still work, or release does nothing."""
    after = ebay_account.unlink_ebay(_linked_everywhere())
    assert "ebay" not in (after.get("marketplaces") or {})


def test_the_legacy_top_level_ebay_fields_are_cleared_too():
    """eBay is mirrored into top-level fields as well as the map. Leaving
    those set would keep routing edits down the Trading path to a listing on
    an account that is no longer connected."""
    after = ebay_account.unlink_ebay(_linked_everywhere())
    assert after["ebay_listing_id"] == ""
    assert after["ebay_account"] == ""
    assert after["source"] == ""
    assert after["view_url"] == ""
    assert after["sku"] == ""


def test_an_unknown_future_marketplace_is_left_alone():
    """The map is open-ended. A marketplace added after this code was written
    must not be collateral damage."""
    rec = _linked_everywhere()
    rec["marketplaces"]["poshmark"] = {"listing_id": "pm-7", "status": "published"}
    after = ebay_account.unlink_ebay(rec)
    assert after["marketplaces"]["poshmark"] == {"listing_id": "pm-7",
                                                 "status": "published"}


def test_a_record_with_no_marketplace_map_is_handled():
    """Older records carry only the legacy top-level fields."""
    after = ebay_account.unlink_ebay(
        {"ebay_listing_id": "1", "ebay_account": "old", "source": "ebay"})
    assert after["ebay_listing_id"] == ""
    assert after.get("marketplaces", {}) == {}


def test_an_ebay_only_record_ends_with_an_empty_map_not_a_missing_one():
    """Downstream readers treat a missing key and an empty map the same, but
    an empty map is the honest description: this record has no marketplaces."""
    after = ebay_account.unlink_ebay({
        "ebay_listing_id": "1",
        "marketplaces": {"ebay": {"listing_id": "1", "status": "published"}},
    })
    assert after["marketplaces"] == {}


def test_photos_are_not_collateral_damage():
    """The records stay, photos and all — that is the promise release makes."""
    before = _linked_everywhere()
    after = ebay_account.unlink_ebay(dict(before))
    assert after["image_urls"] == before["image_urls"]
    assert after["title"] == before["title"]

"""A relist makes a new draft from a sold record and clears the sale's
per-marketplace state — which, for a listing still live on Etsy, used to
orphan the Etsy listing: the next Etsy publish minted a twin and end-listing
answered "this listing isn't on Etsy". A live entry on any marketplace but
eBay now rides to the new draft; ended and draft entries stay with the sale.
"""
from backend.marketplaces.state import carry_live_others


def test_a_live_etsy_entry_is_carried_and_the_ebay_one_is_not():
    carried = carry_live_others({
        "ebay": {"listing_id": "1", "status": "published"},
        "etsy": {"listing_id": "77", "status": "published", "photo_sig": "abc"},
    })
    assert carried == {"etsy": {"listing_id": "77", "status": "published",
                                "photo_sig": "abc"}}


def test_an_ended_or_draft_entry_stays_with_the_sale():
    assert carry_live_others({
        "etsy": {"listing_id": "77", "status": "ended"},
        "depop": {"listing_id": "5", "status": "draft"},
    }) == {}


def test_the_copy_is_a_copy():
    source = {"etsy": {"listing_id": "77", "status": "published"}}
    carried = carry_live_others(source)
    carried["etsy"]["listing_id"] = "changed"
    assert source["etsy"]["listing_id"] == "77"


def test_nothing_in_nothing_out():
    assert carry_live_others({}) == {}
    assert carry_live_others(None) == {}

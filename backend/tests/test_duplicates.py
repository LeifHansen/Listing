"""Finding the duplicate pairs the old publish bug left on sellers' accounts.

The detector's job is to be USEFUL without being bossy: it has to surface the
obvious double-publishes at the top, and it must not shout at a reseller who
legitimately has two copies of the same thing listed. So most of these tests
are about what it declines to flag, or flags only weakly.
"""
from __future__ import annotations

from backend.services import duplicates


def rec(rid, title, item_id, price=45.0, status="published",
        started="2026-08-01T12:00:00Z", fmt="FIXED_PRICE"):
    return {"id": rid, "status": status, "user_id": "u1",
            "created_at": started,
            "listing": {"title": title, "price": price,
                        "ebay_listing_id": item_id, "listing_format": fmt,
                        "ebay_start_time": started,
                        "view_url": f"https://www.ebay.com/itm/{item_id}"}}


TITLE = "Walt Disney's Mickey Mouse Steamboat Willie Framed Mirror"


# ------------------------------------------------------------ normalization

def test_titles_match_through_the_drift_between_our_copy_and_ebays():
    assert duplicates.normalize_title("Walt Disney's Mickey Mouse — Framed!") \
        == duplicates.normalize_title("walt disney s mickey mouse   framed")


def test_normalization_does_not_collapse_genuinely_different_titles():
    assert duplicates.normalize_title("Pyrex Bowl Blue") \
        != duplicates.normalize_title("Pyrex Bowl Red")


# ---------------------------------------------------- the pairs we're after

def test_the_double_publish_pair_is_found_and_ranked_highest():
    """Same title, seconds apart, same price, one app row + one sync mirror."""
    groups = duplicates.find([
        rec("sess-abc", TITLE, "111111111111", started="2026-08-01T12:00:00Z"),
        rec("ebay-222222222222", TITLE, "222222222222",
            started="2026-08-01T12:00:12Z"),
    ])
    assert len(groups) == 1
    assert groups[0]["confidence"] == "high"
    assert len(groups[0]["listings"]) == 2
    joined = " ".join(groups[0]["reasons"])
    assert "12 seconds apart" in joined
    assert "Same price" in joined


def test_the_oldest_listing_is_listed_first():
    """The original is what a seller keeps; the accidental second one reads as
    the one to end."""
    groups = duplicates.find([
        rec("ebay-222222222222", TITLE, "222222222222",
            started="2026-08-01T12:00:12Z"),
        rec("sess-abc", TITLE, "111111111111", started="2026-08-01T12:00:00Z"),
    ])
    assert [x["ebay_listing_id"] for x in groups[0]["listings"]] \
        == ["111111111111", "222222222222"]


def test_the_app_created_row_is_marked_so_the_seller_can_tell_them_apart():
    groups = duplicates.find([
        rec("sess-abc", TITLE, "111111111111"),
        rec("ebay-222222222222", TITLE, "222222222222"),
    ])
    flags = {x["ebay_listing_id"]: x["created_here"] for x in groups[0]["listings"]}
    assert flags == {"111111111111": True, "222222222222": False}


# ------------------------------------------------- what it must NOT flag

def test_one_item_mirrored_twice_is_not_a_duplicate():
    """The app's record and the sync's mirror of the SAME eBay item are one
    listing wearing two rows — the store sync cleans that up, and calling it a
    duplicate would tell the seller to end their only listing."""
    same = "333333333333"
    assert duplicates.find([rec("sess-abc", TITLE, same),
                            rec(f"ebay-{same}", TITLE, same)]) == []


def test_an_ended_twin_is_already_resolved():
    assert duplicates.find([
        rec("sess-abc", TITLE, "111111111111"),
        rec("ebay-222222222222", TITLE, "222222222222", status="ended"),
    ]) == []


def test_a_sold_twin_is_not_flagged():
    assert duplicates.find([
        rec("sess-abc", TITLE, "111111111111"),
        rec("ebay-222222222222", TITLE, "222222222222", status="sold"),
    ]) == []


def test_a_draft_is_not_a_duplicate_of_a_live_listing():
    assert duplicates.find([
        rec("sess-abc", TITLE, "111111111111"),
        rec("sess-def", TITLE, "", status="draft"),
    ]) == []


def test_a_listing_with_no_ebay_id_is_ignored():
    assert duplicates.find([rec("sess-abc", TITLE, ""),
                            rec("sess-def", TITLE, "")]) == []


def test_generic_short_titles_are_not_grouped():
    """"Vase" would pair every vase in the store."""
    assert duplicates.find([rec("sess-a", "Vase", "111111111111"),
                            rec("sess-b", "Vase", "222222222222")]) == []


# ------------------------------- the reseller with two of the real thing

def test_two_copies_listed_days_apart_are_ranked_low_with_the_doubt_stated():
    """A seller really can own two of the same DVD. Flag it faintly, and say
    why it might be fine."""
    groups = duplicates.find([
        rec("sess-a", TITLE, "111111111111", started="2026-07-01T12:00:00Z"),
        rec("sess-b", TITLE, "222222222222", started="2026-08-01T12:00:00Z"),
    ])
    assert groups[0]["confidence"] == "medium"  # same price still counts
    assert any("31 days apart" in c for c in groups[0]["caveats"])


def test_different_prices_and_dates_read_as_two_real_items():
    groups = duplicates.find([
        rec("sess-a", TITLE, "111111111111", price=45.0,
            started="2026-07-01T12:00:00Z"),
        rec("sess-b", TITLE, "222222222222", price=60.0,
            started="2026-08-01T12:00:00Z"),
    ])
    assert groups[0]["confidence"] == "low"
    assert len(groups[0]["caveats"]) == 2


def test_an_auction_alongside_a_buy_it_now_is_never_ranked_high():
    """Running both formats on one item is a real selling strategy."""
    groups = duplicates.find([
        rec("sess-a", TITLE, "111111111111", started="2026-08-01T12:00:00Z",
            fmt="AUCTION"),
        rec("sess-b", TITLE, "222222222222", started="2026-08-01T12:00:05Z"),
    ])
    assert groups[0]["confidence"] == "low"
    assert any("auction" in c for c in groups[0]["caveats"])


# ------------------------------------------------------------- ordering

def test_the_strongest_evidence_comes_first():
    weak = "Vintage Pyrex Mixing Bowl Set Primary Colors"
    groups = duplicates.find([
        # A weak pair: months apart, different prices.
        rec("sess-a", weak, "111111111111", price=40.0,
            started="2026-01-01T12:00:00Z"),
        rec("sess-b", weak, "222222222222", price=95.0,
            started="2026-08-01T12:00:00Z"),
        # An obvious double-publish.
        rec("sess-c", TITLE, "333333333333", started="2026-08-01T12:00:00Z"),
        rec("ebay-444444444444", TITLE, "444444444444",
            started="2026-08-01T12:00:03Z"),
    ])
    assert [g["confidence"] for g in groups] == ["high", "low"]
    assert groups[0]["title"] == TITLE


def test_three_of_the_same_item_come_back_as_one_group():
    groups = duplicates.find([
        rec("sess-a", TITLE, "111111111111"),
        rec("sess-b", TITLE, "222222222222"),
        rec("sess-c", TITLE, "333333333333"),
    ])
    assert len(groups) == 1
    assert len(groups[0]["listings"]) == 3


def test_an_unrelated_store_produces_nothing():
    groups = duplicates.find([
        rec("sess-a", "Art Glass Vase Purple Amethyst Swirl", "111111111111"),
        rec("sess-b", "Mid Century Modern Teak Wall Clock", "222222222222"),
    ])
    assert groups == []

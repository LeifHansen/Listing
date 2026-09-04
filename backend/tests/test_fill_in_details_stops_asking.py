"""The dashboard's "Fill in details" group, and why it did nothing.

A seller reported the group as broken: 46 listings, "Enrich all", and nothing
changed — the run came back "nothing the photos could answer" and the badge
still read 46 the next time they looked.

Both halves of that were true, and both came from the same place. The group
was built from `missing_info`, the free-text notes the AI writes about an
item, and asked to fill ITEM SPECIFICS. Those are different questions:

  * an imported listing carries no notes at all, so a store mirrored out of
    Seller Hub — every specific blank, which is exactly what the fill is for
    — was never once offered it;

  * an app-made draft carries notes like "exact measurements" and "confirm the
    signature", which no item specific answers. That listing was offered the
    fill forever, ran it, changed nothing, and was suggested again on the next
    refresh.

So the group is built from the specifics that are actually blank, and a
listing the fill has already run on stops being offered it. Press the button
once and it does its work; press it and there is nothing to do, and it stops
asking.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.services import recommender

LIVE = "published"


def _item(**listing):
    """A live listing record with nothing else wrong with it: enough photos,
    and (by default) old enough not to matter either way, since these tests
    read recommend_for's whole list rather than the one rec that wins.

    `created_at` is overridable for the cross-store test, which goes through
    recommendations() — that keeps only the STRONGEST rec per listing, and a
    listing 21 days old earns a price drop at priority 68 that would hide the
    specifics rec at 45 regardless of what this is testing.
    """
    return {"id": listing.pop("id", "L1"), "status": LIVE,
            "created_at": listing.pop("created_at", "2020-01-01T00:00:00+00:00"),
            "listing": {"title": "Vintage bowling trophy",
                        "images": ["1.jpg", "2.jpg", "3.jpg"], **listing}}


def _fresh(**listing):
    """A live listing posted moments ago — no age heuristic fires on it."""
    return _item(created_at=datetime.now(timezone.utc).isoformat(), **listing)


def _types(recs):
    return [r["type"] for r in recs]


# ------------------------------------------------- the listing that was never asked

def test_an_imported_listing_with_blank_specifics_is_finally_offered_the_fill():
    """The whole point. A listing mirrored from eBay has no missing_info at
    all, so under the old rule a store of them produced no "Fill in details"
    at all — while every one of their specifics sat empty."""
    recs = recommender.recommend_for(_item(missing_info=[]),
                                     blank_specifics=14)
    assert "specifics" in _types(recs)


def test_the_group_says_how_many_are_blank():
    rec = next(r for r in recommender.recommend_for(_item(missing_info=[]),
                                                    blank_specifics=14)
               if r["type"] == "specifics")
    assert "14 fields" in rec["reason"]


def test_a_listing_with_one_or_two_blanks_is_left_alone():
    """Every listing has something empty. A rec for two boxes is noise, and it
    costs AI credits to act on."""
    recs = recommender.recommend_for(_item(missing_info=[]), blank_specifics=2)
    assert "specifics" not in _types(recs)


def test_nothing_blank_is_nothing_to_fill():
    recs = recommender.recommend_for(_item(missing_info=[]), blank_specifics=0)
    assert "specifics" not in _types(recs)


# --------------------------------------------------------- the listing that looped

def test_a_listing_the_fill_has_run_on_is_not_asked_again():
    """It has been asked this question and has given its answer. Asking again
    spends the seller's credits to be told the same thing — which is the loop
    that read, correctly, as the button not working."""
    recs = recommender.recommend_for(
        _item(missing_info=["exact measurements"],
              enriched_at="2026-09-04T12:00:00+00:00"),
        blank_specifics=14)
    assert "specifics" not in _types(recs)


def test_what_is_left_after_the_fill_is_a_nudge_to_look():
    """The notes the fill could not answer are still real. They just want the
    seller's eyes, not another vision pass."""
    recs = recommender.recommend_for(
        _item(missing_info=["exact measurements"],
              enriched_at="2026-09-04T12:00:00+00:00"),
        blank_specifics=14)
    assert "verify" in _types(recs)


def test_a_fill_that_never_ran_leaves_the_offer_standing():
    """`enriched_at` means the pass RAN. A listing whose category or photos
    were missing at the time has the fill still ahead of it."""
    recs = recommender.recommend_for(_item(missing_info=[], enriched_at=""),
                                     blank_specifics=14)
    assert "specifics" in _types(recs)


# ------------------------------------------------------------ when nobody counted

def test_without_a_count_it_falls_back_to_the_notes():
    """No category, or the Taxonomy API down. Absence of a count is not
    evidence that nothing is blank, so the old signal still stands."""
    recs = recommender.recommend_for(_item(missing_info=["size"]),
                                     blank_specifics=None)
    assert "specifics" in _types(recs)


def test_an_advisory_note_alone_is_never_a_fill():
    """"Price raised" and "eBay category" are advice to a person; no item
    specific answers them. Unchanged by any of this."""
    recs = recommender.recommend_for(
        _item(missing_info=["Price raised to $40 — see comps"]),
        blank_specifics=None)
    assert "specifics" not in _types(recs)
    assert "verify" in _types(recs)


def test_a_note_the_fill_can_answer_still_earns_it_even_with_few_blanks():
    """The two signals are an OR, not a replacement: a note naming a specific
    is the AI saying it could not read one, and that is worth the pass even
    when the category has little else empty."""
    recs = recommender.recommend_for(_item(missing_info=["size"]),
                                     blank_specifics=1)
    assert "specifics" in _types(recs)


# ------------------------------------------------------------- across the store

def test_the_count_reaches_the_listing_it_was_measured_for():
    items = [_fresh(id="A", missing_info=[]), _fresh(id="B", missing_info=[])]
    recs = recommender.recommendations(items, blanks_by_id={"A": 14},
                                       promotion_known=False)
    by_id = {r["listing_id"]: r["type"] for r in recs}
    assert by_id["A"] == "specifics"
    assert by_id.get("B") != "specifics"

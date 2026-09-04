"""The dashboard's "Fill in details" group: the count that decides it, and
the fact that ends it.

test_a_note_for_a_person_is_not_a_fill covers the first half of this — the
group is decided by item specifics, never by the free-text notes beside them.
This file covers the two things added on top.

THE COUNT. `filled_specifics` is a proxy, and it is blind in one direction:
a listing with Material, Type and Brand filled has three specifics and passes
it, while Subject, Era, Occasion, Packaging and Character sit blank and eBay's
own suggester offers all five to the seller on the very next screen. That is
the listing a seller reported. So the caller counts the real thing where it
can afford to — how many of the aspects eBay publishes for THIS category the
listing holds no value for — and passes it as `blank_specifics`. The proxy
stands wherever it could not be counted.

THE END. Neither count can stop the group asking. A listing whose photos
genuinely cannot answer its category has blank specifics before the fill and
blank specifics after it, so it sits in the group forever and is charged for
on every press: 46 listings, "Enrich all", nothing changed, badge still 46 the
next time the seller looked. `enriched_at` is the difference between "these
specifics are blank" and "these specifics are blank and the AI has already
looked".
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
    assert "14 of eBay's item specifics" in rec["reason"]


def test_the_listing_with_plenty_filled_and_plenty_blank_is_seen():
    """The case the proxy is blind to, and the one a seller reported: three
    specifics filled clears `filled_specifics`, and Subject, Era, Occasion,
    Packaging and Character are still blank with eBay offering all five."""
    listing = _item(missing_info=[], item_specifics=[
        {"name": "Material", "value": "Ceramic"},
        {"name": "Type", "value": "Trophy"},
        {"name": "Color", "value": "Gold"},
    ])
    assert "specifics" not in _types(
        recommender.recommend_for(listing, blank_specifics=None))
    assert "specifics" in _types(
        recommender.recommend_for(listing, blank_specifics=14))


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

def test_without_a_count_the_proxy_still_stands():
    """No category on the listing, the Taxonomy API down, or a store whose
    categories ran past the lookup budget. Absence of a count is not evidence
    that nothing is blank, so the cheap signal keeps deciding."""
    recs = recommender.recommend_for(_item(missing_info=[]),
                                     blank_specifics=None)
    assert "specifics" in _types(recs)


def test_a_real_count_overrides_the_proxy_in_both_directions():
    """Where it can be had, the truth wins: a listing the proxy would pass
    over is offered the fill, and one the proxy would offer it to is left
    alone when its category has almost nothing left to answer."""
    bare = _item(missing_info=[])            # no specifics at all
    assert "specifics" not in _types(
        recommender.recommend_for(bare, blank_specifics=1))
    assert "specifics" in _types(
        recommender.recommend_for(bare, blank_specifics=None))


def test_the_notes_beside_it_decide_nothing():
    """A note is not evidence a specific is blank, and it is not evidence one
    is filled. It earns a nudge to LOOK once the fill has nothing left."""
    recs = recommender.recommend_for(
        _item(missing_info=["Measurements — I can't measure from photos"],
              enriched_at="2026-09-04T12:00:00+00:00"),
        blank_specifics=14)
    assert "specifics" not in _types(recs)
    assert "verify" in _types(recs)


# ------------------------------------------------------------- across the store

def test_the_count_reaches_the_listing_it_was_measured_for():
    # B is counted by nobody and passes the proxy, so only A's count decides.
    filled = [{"name": "Material", "value": "Ceramic"},
              {"name": "Type", "value": "Trophy"},
              {"name": "Color", "value": "Gold"}]
    items = [_fresh(id="A", missing_info=[], item_specifics=filled),
             _fresh(id="B", missing_info=[], item_specifics=filled)]
    recs = recommender.recommendations(items, blanks_by_id={"A": 14},
                                       promotion_known=False)
    by_id = {r["listing_id"]: r["type"] for r in recs}
    assert by_id["A"] == "specifics"
    assert by_id.get("B") != "specifics"

"""Recommendation ranking: the one-rec-per-listing dedupe and the limit that
the grouped dashboard view depends on (pure module, CI-safe)."""
from backend.services import recommender


def _published(i: int) -> dict:
    # No metrics, no photos -> each yields at least the priority-50 "Add more
    # photos" rec (and "Fill in details" at 45, which the dedupe drops).
    return {"id": f"rec{i}", "status": "published", "title": f"Item {i}",
            "listing": {"title": f"Item {i}", "images": []}}


def test_default_limit_caps_at_eight():
    items = [_published(i) for i in range(20)]
    assert len(recommender.recommendations(items)) == 8


def test_raised_limit_returns_full_membership():
    items = [_published(i) for i in range(20)]
    recs = recommender.recommendations(items, limit=50)
    assert len(recs) == 20


def test_one_rec_per_listing_keeps_strongest():
    items = [_published(i) for i in range(20)]
    recs = recommender.recommendations(items, limit=50)
    ids = [r["listing_id"] for r in recs]
    assert len(ids) == len(set(ids))
    # Every item's specifics rec (priority 45) lost to its photos rec (50).
    assert all(r["type"] == "photos" for r in recs)


def test_sorted_by_priority_desc():
    # An UNLISTED draft, not an ended listing: ended records earn nothing now
    # (relisting was dropped as advice), so using one here would leave a
    # single-element list and an assertion that cannot fail.
    items = [_published(0), {"id": "d1", "status": "unlisted", "title": "Draft",
                             "listing": {"title": "Draft"}}]
    recs = recommender.recommendations(items, limit=50)
    priorities = [r["priority"] for r in recs]
    assert len(priorities) == 2, "the ordering is only tested if both items rank"
    assert priorities == sorted(priorities, reverse=True)


# --- the quiet period after a fill ------------------------------------------
#
# A seller opened Home, found "Fill in details · 12" with a button on it,
# pressed it, and waited several minutes while the AI read twelve listings'
# photos and pushed the new specifics to eBay. It worked. And the group they
# had just cleared was replaced, in the same slot, by "Check details · 12" —
# the same twelve listings, still flagged, and this time with no button on the
# group at all: just a list to open one at a time.
#
# From outside that is indistinguishable from the button having done nothing,
# and it was reported as exactly that ("it should fill in all possible missing
# fields... I don't know why you keep showing me a list view... they are not
# updating as far as I can tell"). The notes behind it are real, but they are
# by construction the things the fill just declined to invent — so they wait a
# day before becoming a chore.

from datetime import datetime, timedelta, timezone  # noqa: E402


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=days)).isoformat(timespec="seconds")


def _live(**listing) -> dict:
    return {"id": "L1", "status": "published", "title": "A listing",
            "listing": {"title": "A listing",
                        "item_specifics": [{"name": "Brand", "value": "Pyrex"},
                                           {"name": "Type", "value": "Bowl"},
                                           {"name": "Color", "value": "Blue"},
                                           {"name": "Material", "value": "Glass"}],
                        **listing}}


def _types(item: dict) -> set[str]:
    return {r["type"] for r in recommender.recommend_for(item)}


def test_a_fill_that_just_ran_does_not_come_straight_back_as_a_chore():
    item = _live(missing_info=["Measurements — I can't measure from photos"],
                 enriched_at=_ago(0))
    assert "specifics" not in _types(item)   # the fill has had its go
    assert "verify" not in _types(item)      # and is not re-flagged as a list


def test_the_note_comes_back_once_the_seller_has_had_a_day_with_it():
    item = _live(missing_info=["Measurements — I can't measure from photos"],
                 enriched_at=_ago(recommender.VERIFY_QUIET_DAYS + 0.5))
    assert "verify" in _types(item)


def test_a_listing_the_fill_never_ran_on_still_nudges_immediately():
    """The quiet period is about the minutes AFTER a fill, not about notes in
    general: a listing carrying notes that nothing has ever looked at is a
    real to-do and always was."""
    item = _live(missing_info=["Authentication for this designer piece"])
    assert "verify" in _types(item)


def test_the_fill_itself_is_never_delayed_by_the_quiet_period():
    """Only the follow-up nudge waits. A listing whose specifics are actually
    blank is still offered the button, whatever it carries."""
    item = {"id": "L2", "status": "published", "title": "Blank",
            "listing": {"title": "Blank", "item_specifics": [],
                        "missing_info": ["Measurements"]}}
    assert "specifics" in _types(item)

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


# --- a note the AI left for a person is never a suggestion ------------------
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
# updating as far as I can tell"). A quiet period was tried first and only
# moved the same nag a day later; on a real store it came back as 203 rows
# that nothing could clear in bulk.
#
# So `verify` does not exist. A note is what the AI DECLINED to invent — no
# pass will ever answer it — so it stays on the listing where the person
# holding the item can settle it, and Enrich all retires the leftovers on
# every listing it fills. These tests hold that line at every age: freshly
# filled, filled long ago, and never filled at all.

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


def test_the_note_does_not_come_back_a_day_later_either():
    """The whole failure, one day on. A quiet period only postponed it."""
    item = _live(missing_info=["Measurements — I can't measure from photos"],
                 enriched_at=_ago(30))
    assert "verify" not in _types(item)


def test_a_listing_the_fill_never_ran_on_is_not_nudged_for_its_notes():
    """A note is not a suggestion at any age, filled or not. This listing's
    specifics are healthy; all it carries is something only a person can
    settle, and that is not a row on the dashboard."""
    item = _live(missing_info=["Authentication for this designer piece"])
    assert "verify" not in _types(item)


def test_the_fill_itself_is_never_held_back_by_a_note():
    """A listing whose specifics are actually blank is still offered the
    button, whatever notes it carries."""
    item = {"id": "L2", "status": "published", "title": "Blank",
            "listing": {"title": "Blank", "item_specifics": [],
                        "missing_info": ["Measurements"]}}
    assert "specifics" in _types(item)

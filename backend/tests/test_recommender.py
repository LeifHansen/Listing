"""Recommendation ranking: the one-rec-per-listing dedupe and the limit that
the grouped dashboard view depends on (pure module, CI-safe)."""
from backend.services import recommender


def _published(i: int) -> dict:
    # No metrics, not promoted -> each yields at least the priority-70
    # "Promote" rec (and "Add more photos" at 50, which the dedupe drops).
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
    # Every item's photos rec (priority 50) lost to its promote rec (70).
    assert all(r["type"] == "promote" for r in recs)


def test_sorted_by_priority_desc():
    items = [_published(0), {"id": "e1", "status": "ended", "title": "Ended",
                             "listing": {"title": "Ended"}}]
    recs = recommender.recommendations(items, limit=50)
    priorities = [r["priority"] for r in recs]
    assert priorities == sorted(priorities, reverse=True)

"""An ended listing earns no "Relist" suggestion, because it may be a sold one.

The Dashboard's Suggested actions card used to offer "Relist" for every record
sitting at status `ended`. Two things were wrong with it.

The first is that relisting is a decision, not a chore: the seller reprices,
re-photographs, or decides the item is not worth another run. It was never the
kind of repeated edit the suggestions card exists to collapse.

The second is worse. `ended` is not a synonym for "did not sell". The store
sync reconciles finished listings from `set(sales) | unsold_listing_ids(...)`
(services/listing_sync.py), and `_ids()` there swallows a lookup failure and
returns an empty set. So when eBay's Unsold list lags, or `recent_sales` misses
a sale, a listing that SOLD settles at status `ended` — and the app then offered
to relist something the seller had already shipped.

This holds the removal in place. It is a fact about the rule, not about the
sync: whatever mix of statuses reaches the `ended` bucket, nothing there is
advised.
"""
from __future__ import annotations

from backend.services import recommender


def _ended(**over) -> dict:
    item = {"id": "e1", "status": "ended", "title": "Ended",
            "listing": {"title": "Ended", "images": []}}
    item.update(over)
    return item


def test_an_ended_listing_earns_no_recommendation():
    assert recommender.recommend_for(_ended()) == []


def test_no_ended_listing_reaches_the_ranked_list():
    """Not just the single-listing rule — the ranked list the dashboard reads."""
    items = [_ended(id=f"e{i}") for i in range(5)]
    assert recommender.recommendations(items, limit=50) == []


def test_a_sold_listing_mislabelled_ended_is_not_offered_a_relist():
    """The failure this removal exists for, spelled out.

    A sold item that the sync recorded as `ended` carries a sale price and a
    sold quantity. It must still earn nothing — the whole point is that the
    rule cannot tell it apart from a genuinely unsold one, so it advises on
    neither.
    """
    sold_but_ended = _ended(
        listing={"title": "Sold", "images": ["a.jpg"], "sold_price": 45.0,
                 "sold_quantity": 1})
    assert recommender.recommend_for(sold_but_ended) == []


def test_the_relist_type_is_gone_from_every_rule():
    """No surviving branch emits it — including the metrics-driven ones, which
    a status check alone would not cover."""
    items = [_ended(), {"id": "p1", "status": "published", "title": "Live",
                        "listing": {"title": "Live", "images": []}},
             {"id": "d1", "status": "unlisted", "title": "Draft",
              "listing": {"title": "Draft", "images": []}}]
    recs = recommender.recommendations(
        items, metrics_by_id={"e1": {"views": 200, "watchers": 0}}, limit=50)
    assert recs, "the live and draft items should still be advised"
    assert all(r["type"] != "relist" for r in recs)

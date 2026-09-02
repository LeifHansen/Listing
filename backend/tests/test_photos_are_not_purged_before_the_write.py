"""Archiving a sold listing deletes its photos. The write has to land first.

Two paths file a listing as sold and then reclaim its working copies —
`POST /api/ebay/end-listing` when eBay reports the item already sold, and the
status sweep in `listing_sync.refresh_statuses`. Both purge AFTER a
`db.upsert_listing` whose result they ignored, and that call swallows its
failures by design.

The purge is right when the write lands: a sold listing is archived, eBay
hosts the photos it was published with, and the working copies are dead
weight on a small volume. It is wrong when the write does not: the record
still says the listing is LIVE, so the app offers to edit and revise
something whose photos are gone.

Same rule as the merge, which is the other place this branch found a destroy
running ahead of the write that justifies it. Nothing is deleted for a status
change that did not happen.
"""
from __future__ import annotations

import pytest


# ------------------------------------------------ the status sweep

@pytest.fixture()
def sweep(monkeypatch):
    from backend import db
    from backend.services import ebay_trading, listing_sync, notifications

    def _run(write_lands: bool):
        purged: list[str] = []
        rec = {"id": "s1", "status": "published",
               "listing": {"ebay_listing_id": "556677", "source": "ebay",
                           "title": "A lamp"}}

        monkeypatch.setattr(ebay_trading, "listing_status",
                            lambda *_a, **_k: ("sold", 1, 0))
        monkeypatch.setattr(notifications, "notify_sold", lambda *_a, **_k: None)
        monkeypatch.setattr(db, "enabled", lambda: True)
        monkeypatch.setattr(db, "upsert_listing", lambda *_a, **_k: write_lands)
        monkeypatch.setattr(listing_sync.storage, "purge_session",
                            lambda sid: purged.append(sid))
        # sales={} rather than None, so nothing reaches recent_sales.
        changed = listing_sync.refresh_statuses("tok", "u1", [rec], sales={})
        return changed, purged
    return _run


def test_a_sold_listing_whose_write_failed_keeps_its_photos(sweep):
    changed, purged = sweep(write_lands=False)

    assert purged == [], "archived the photos of a listing still marked live"
    assert changed == 0, "counted a change that was not written"


def test_a_sold_listing_that_was_written_is_archived(sweep):
    changed, purged = sweep(write_lands=True)

    assert purged == ["s1"]
    assert changed == 1

"""The store mirror reports progress, so the client can stop guessing.

The seller-visible failure: "Syncing your eBay store…" spun forever. The
import is one eBay GetItem per listing — minutes on a real store — and it ran
inside the POST the browser was waiting on, so there was nothing to show but a
spinner and no moment at which it was allowed to end.

It runs as a background job now. These cover the two halves that make that
safe: import_active reports which phase it's in and how far it's got, and a
job killed by a restart comes back as a FINISHED store-import (with a reason
that sends the seller to Listings, not to Drafts) rather than a hole the
client polls forever.
"""
from __future__ import annotations

import pytest

from backend.services import ebay_trading, jobstore, listing_sync

ITEMS = [f"1234567890{i:02d}" for i in range(5)]

DETAIL = {
    "title": "Framed piano painting",
    "price": 45.0,
    "quantity": 1,
    "source": "ebay",
    "view_url": "https://www.ebay.com/itm/x",
}


class FakeDb:
    def __init__(self):
        self.records: dict[str, dict] = {}

    def list_listings(self, limit=50, user_id=None, statuses=None, before=None):
        return list(self.records.values())

    def upsert_listing(self, listing_id, listing, status="draft", user_id=None,
                       when=None):
        self.records[listing_id] = {"id": listing_id, "user_id": user_id,
                                    "listing": listing, "status": status}

    def delete_listing(self, listing_id, user_id=None):
        return bool(self.records.pop(listing_id, None))


class FakeTrading:
    # The real exception type, not a stand-in: listing_sync catches
    # RateLimited by identity to decide whether to stop the pass, and a
    # double that omitted it made that branch unreachable in these tests.
    RateLimited = ebay_trading.RateLimited

    def __init__(self, item_ids, bad=()):
        self.item_ids = list(item_ids)
        self.bad = set(bad)

    def active_listing_ids(self, token, limit=0):
        return list(self.item_ids)

    def sold_sales(self, token, limit=0):
        return {}

    def unsold_listing_ids(self, token, limit=0):
        return []

    def get_listing(self, token, item_id):
        if item_id in self.bad:
            raise RuntimeError("eBay said no")
        return dict(DETAIL, ebay_listing_id=item_id)


@pytest.fixture
def ticks(monkeypatch):
    """Run import_active over `n` fake listings; returns the progress calls."""
    def run(item_ids=ITEMS, bad=()):
        monkeypatch.setattr(listing_sync, "db", FakeDb())
        monkeypatch.setattr(listing_sync, "ebay_trading",
                            FakeTrading(item_ids, bad=bad))
        seen: list[tuple[str, int, int]] = []
        result = listing_sync.import_active(
            "token", "u1", on_progress=lambda *a: seen.append(a))
        return seen, result
    return run


def test_progress_covers_every_listing(ticks):
    """One running count, not two passes.

    The import used to fetch every listing and then save every listing, and
    reported a `fetching` count followed by a `saving` one. It is a single
    pass now — each listing is fetched and written down as one unit, so an
    interruption keeps what it has paid for — and two counts no longer
    describe anything that happens. See
    test_an_interrupted_import_keeps_what_it_paid_for.
    """
    seen, result = ticks()
    assert result["found"] == len(ITEMS)
    syncing = [t for t in seen if t[0] == "syncing"]
    # One tick per listing (plus the opening 0), each carrying the total.
    assert [t[1] for t in syncing] == list(range(0, len(ITEMS) + 1))
    assert {t[2] for t in syncing} == {len(ITEMS)}


def test_progress_never_runs_backwards_or_past_the_total(ticks):
    seen, _ = ticks()
    counts = [t[1] for t in seen if t[0] == "syncing"]
    assert counts == sorted(counts)
    assert max(counts) <= len(ITEMS)


def test_a_listing_ebay_refuses_still_ticks(ticks):
    """A failed GetItem is still a listing accounted for — otherwise the count
    stalls short of the total and the sync looks stuck at the end."""
    seen, result = ticks(bad={ITEMS[2]})
    assert result["failed"] == 1
    assert max(t[1] for t in seen if t[0] == "syncing") == len(ITEMS)


def test_import_without_a_progress_callback_still_works(monkeypatch):
    monkeypatch.setattr(listing_sync, "db", FakeDb())
    monkeypatch.setattr(listing_sync, "ebay_trading", FakeTrading(ITEMS))
    assert listing_sync.import_active("token", "u1")["found"] == len(ITEMS)


def test_a_broken_progress_callback_cannot_sink_the_sync(monkeypatch):
    monkeypatch.setattr(listing_sync, "db", FakeDb())
    monkeypatch.setattr(listing_sync, "ebay_trading", FakeTrading(ITEMS))

    def boom(*_a):
        raise RuntimeError("the client went away")

    assert listing_sync.import_active(
        "token", "u1", on_progress=boom)["imported"] == len(ITEMS)


def test_an_interrupted_import_explains_itself_as_a_store_sync():
    """Not as a photo batch: nothing was drafted, and what it did import is
    already in Listings."""
    msg = jobstore.interrupted_message({
        "kind": "ebay-import", "phase": "fetching", "current": 12,
        "total_items": 40,
    })
    assert "12 of 40" in msg
    assert "Listings" in msg
    assert "Drafts" not in msg


def test_an_interrupted_bulk_batch_still_points_at_drafts():
    msg = jobstore.interrupted_message({
        "kind": "bulk", "phase": "identifying", "current": 3, "total_items": 8,
    })
    assert "Drafts" in msg

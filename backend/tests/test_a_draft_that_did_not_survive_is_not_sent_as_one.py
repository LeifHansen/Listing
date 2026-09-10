"""A batch resumed after a restart only calls something a draft if it IS one.

A batch picked back up rebuilds its finished items from their saved listings
(main._bulk_items_from_disk). storage.load_listing is documented to answer
None for a listing.json that is gone or half-written -- a real outcome when
the process died mid-write, or the volume did not come back -- and that None
went straight out as `listing: null` with the item's status left at "draft".

The queue screen reads every DRAFT's blockers off its listing, so one such
item threw during the queue's own render and took the whole batch screen down
to the error boundary: a seller watching a 30-item batch lost the sight of
every draft that had survived, and saw "This screen ran into a problem".

Two things had to be true for that, and this covers both. The listing is
looked for in the database as well as on disk -- the row outlives a volume
that did not come back -- and an item neither of them can produce goes out as
the failure it is, with a sentence the seller can act on.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from backend import main, storage  # noqa: E402
from backend.models import Listing  # noqa: E402


def _done(sid, name="item 0", title="Nike hoodie", status="draft"):
    """A mirrored row for an item the batch had finished (main._compact_item)."""
    return {"session_id": sid, "name": name, "status": status,
            "error": None, "title": title}


def _saved(title="Nike hoodie"):
    sid = storage.new_session_id()
    storage.save_listing(sid, Listing(title=title, price=20,
                                      images=["img_000.jpg"]))
    return sid


def test_an_item_whose_listing_is_gone_is_not_called_a_draft(monkeypatch):
    """The crash, at its source. Nothing on disk and nothing in the database:
    this item is not something the seller can review or publish, and saying
    it is was what handed the queue a draft with no listing."""
    monkeypatch.setattr(main.db, "get_listing_best_effort", lambda sid: None)
    lost = storage.new_session_id()

    items = main._bulk_items_from_disk([_done(lost, name="item 3")])

    assert len(items) == 1
    assert items[0]["status"] == "error", (
        "a draft with no listing behind it takes the batch screen down")
    assert items[0]["listing"] is None
    assert "couldn't be recovered" in items[0]["error"]
    assert "re-upload" in items[0]["error"]
    # It still names itself, so the card is not a blank the seller can't place.
    assert items[0]["title"] == "Nike hoodie"
    assert items[0]["session_id"] == lost


def test_the_database_row_stands_in_for_a_volume_that_did_not_come_back(monkeypatch):
    """Disk is not the only copy. Where a database is configured, the row
    outlives the volume -- so the item comes back as the draft it really is
    rather than as a loss, and the seller keeps the AI they paid for."""
    sid = storage.new_session_id()
    row = {"title": "Canon AE-1", "price": 90.0, "images": ["img_000.jpg"]}
    monkeypatch.setattr(main.db, "get_listing_best_effort",
                        lambda listing_id: {"listing": row}
                        if listing_id == sid else None)

    items = main._bulk_items_from_disk([_done(sid, title="Canon AE-1")])

    assert items[0]["status"] == "draft"
    assert items[0]["listing"] == row
    assert items[0]["error"] is None
    # The thumbnail comes off whichever copy answered.
    assert items[0]["thumb"].endswith("/optimized/img_000.jpg")


def test_the_disk_copy_is_still_read_first(monkeypatch):
    """A database is OPTIONAL (README), so disk has to keep working on a
    machine that has none -- and must not be second-guessed on one that does."""
    called = []
    monkeypatch.setattr(main.db, "get_listing_best_effort",
                        lambda sid: called.append(sid))
    sid = _saved("Nike hoodie")

    items = main._bulk_items_from_disk([_done(sid)])

    assert called == [], "the database was asked about a listing already in hand"
    assert items[0]["status"] == "draft"
    assert items[0]["listing"]["title"] == "Nike hoodie"


def test_one_lost_item_does_not_cost_the_ones_beside_it(monkeypatch):
    """The whole point. A batch is many items; losing one is not losing the
    batch, and the queue has to come back carrying everything that survived."""
    monkeypatch.setattr(main.db, "get_listing_best_effort", lambda sid: None)
    first = _saved("Nike hoodie")
    lost = storage.new_session_id()
    last = _saved("Canon AE-1")

    items = main._bulk_items_from_disk(
        [_done(first), _done(lost, name="item 1", title="item 1"),
         _done(last, name="item 2", title="Canon AE-1")])

    assert [it["status"] for it in items] == ["draft", "error", "draft"]
    assert [it["listing"]["title"] for it in items if it["listing"]] == [
        "Nike hoodie", "Canon AE-1"]


def test_an_error_the_batch_already_recorded_is_not_overwritten(monkeypatch):
    """An item that failed while the batch ran carries the reason it failed.
    That reason is the seller's, and a restart must not replace it with a
    guess about the restart."""
    monkeypatch.setattr(main.db, "get_listing_best_effort", lambda sid: None)
    rec = _done(storage.new_session_id(), status="error")
    rec["error"] = "Out of AI tokens when this item's turn came."

    items = main._bulk_items_from_disk([rec])

    assert items[0]["status"] == "error"
    assert items[0]["error"] == "Out of AI tokens when this item's turn came."

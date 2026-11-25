"""A store sync must not write back the copy of a listing it read minutes ago.

The import reads every record once, then spends minutes on one GetItem per
listing before it writes each one back. The app starts that import by itself
when it opens, so the seller is usually editing while it runs. Writing
`merge(the copy read at the start, eBay's answer)` put that early copy back
over whatever the seller had saved since: a new title, a cost price, a change
saved for the next "Update on eBay". The status sweep did the same thing on
a shorter clock.

Each write now merges against the row as it stands when the write happens.
"""
from __future__ import annotations

import copy

from backend.services import listing_sync

ITEM = "123456789012"
RID = "sess-abc"

DETAIL = {
    "title": "Framed piano painting",
    "description": "A framed painting.",
    "price": 45.0,
    "quantity": 1,
    "condition": "USED_EXCELLENT",
    "source": "ebay",
    "ebay_listing_id": ITEM,
    "view_url": f"https://www.ebay.com/itm/{ITEM}",
    "image_urls": ["https://i.ebayimg.com/images/g/abc/s-l1600.jpg"],
    "watch_count": 7,
}


def _record():
    return {"id": RID, "user_id": "u1", "status": "published",
            "listing": {"title": "Framed piano painting",
                        "description": "A framed painting.",
                        "price": 45.0, "quantity": 1,
                        "images": ["img_000.jpg"], "source": "ebay",
                        "ebay_listing_id": ITEM, "watch_count": 2}}


class Store:
    """A listing table that can read one row back, like backend.db."""

    def __init__(self, records):
        self.records = {r["id"]: copy.deepcopy(r) for r in records}

    def enabled(self):
        return True

    def list_listings(self, limit=50, user_id=None, statuses=None):
        return [copy.deepcopy(r) for r in self.records.values()
                if r.get("user_id") == user_id
                and (statuses is None or r.get("status") in statuses)]

    def get_listing_strict(self, listing_id):
        rec = self.records.get(listing_id)
        return copy.deepcopy(rec) if rec else None

    def upsert_listing(self, listing_id, listing, status="draft", user_id=None,
                       when=None):
        rec = self.records.setdefault(listing_id, {"id": listing_id,
                                                   "user_id": user_id})
        rec.update({"listing": copy.deepcopy(listing), "status": status})
        return True

    def delete_listing(self, listing_id, user_id=None):
        return self.records.pop(listing_id, None) is not None


def _seller_edits(store):
    """What the seller saves from the editor while the sync is running."""
    listing = store.records[RID]["listing"]
    listing["title"] = "Signed framed piano painting, 1960s"
    listing["purchase_price"] = 4.0
    listing["dirty_fields"] = ["title"]


class Trading:
    """eBay's side. Answering for the item is the moment the seller saves."""

    def __init__(self, store):
        self.store = store

    def active_listing_ids(self, token, limit=0):
        return [ITEM]

    def sold_sales(self, token, limit=0):
        return {}

    def unsold_listing_ids(self, token, limit=0):
        return []

    def get_listing(self, token, item_id):
        _seller_edits(self.store)
        return dict(DETAIL)

    def listing_status(self, token, item_id):
        _seller_edits(self.store)
        return "published", 0, 9


def test_an_import_keeps_an_edit_saved_while_it_ran(monkeypatch):
    store = Store([_record()])
    monkeypatch.setattr(listing_sync, "db", store)
    monkeypatch.setattr(listing_sync, "ebay_trading", Trading(store))

    listing_sync.import_active("token", "u1")

    saved = store.records[RID]["listing"]
    assert saved["title"] == "Signed framed piano painting, 1960s"
    assert saved["purchase_price"] == 4.0
    assert "title" in saved["dirty_fields"], "the edit must still go to eBay"
    # ...and eBay's own facts still arrive.
    assert saved["watch_count"] == 7


def test_a_status_sweep_keeps_an_edit_saved_while_it_ran(monkeypatch):
    store = Store([_record()])
    monkeypatch.setattr(listing_sync, "db", store)
    monkeypatch.setattr(listing_sync, "ebay_trading", Trading(store))

    changed = listing_sync.refresh_statuses(
        "token", "u1", store.list_listings(user_id="u1"), sales={})

    assert changed == 1
    saved = store.records[RID]["listing"]
    assert saved["title"] == "Signed framed piano painting, 1960s"
    assert saved["purchase_price"] == 4.0
    assert saved["dirty_fields"] == ["title"]
    assert saved["watch_count"] == 9


def test_a_store_that_cannot_read_one_row_back_still_syncs(monkeypatch):
    """The read is an improvement on the snapshot, never a new way to fail:
    a store with no single-row read (or one whose read fails) writes from the
    copy the pass already holds, exactly as before."""
    store = Store([_record()])
    monkeypatch.setattr(Store, "get_listing_strict",
                        lambda self, _id: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(listing_sync, "db", store)

    class Quiet(Trading):
        def get_listing(self, token, item_id):
            return dict(DETAIL)

    monkeypatch.setattr(listing_sync, "ebay_trading", Quiet(store))
    result = listing_sync.import_active("token", "u1")
    assert result["updated"] == 1
    assert store.records[RID]["listing"]["watch_count"] == 7

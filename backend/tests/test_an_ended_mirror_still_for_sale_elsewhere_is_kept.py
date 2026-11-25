"""An eBay mirror that is still for sale on another marketplace is not deleted.

A mirror of an eBay listing (`ebay-<item>`) is removed the moment its eBay
listing ends, because nothing in it is the seller's own work -- unless it was
crossposted. Then it is the only record of the Etsy (or Depop) listing: delete
it and that listing stays live with nothing in the app pointing at it, so
nothing can end it, sync it, or take it down when the item sells.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services import listing_sync


def _mirror(**markets):
    return {"id": "ebay-110000000001", "status": "ended",
            "listing": {"title": "Brass lamp", "images": [],
                        "ebay_listing_id": "110000000001",
                        "ended_at": (datetime.now(timezone.utc)
                                     - timedelta(days=365)).isoformat(),
                        "marketplaces": markets}}


class _Store:
    def __init__(self):
        self.deleted = []

    def delete_listing(self, rid, user_id=None):
        self.deleted.append(rid)
        return True


def test_a_crossposted_mirror_is_worth_keeping():
    assert listing_sync.keeps_grace(_mirror(etsy={"status": "published",
                                                  "listing_id": "9"}))


def test_the_sweep_never_removes_one_while_it_is_live_elsewhere(monkeypatch):
    store = _Store()
    monkeypatch.setattr(listing_sync, "db", store)
    monkeypatch.setattr(listing_sync, "_purge_photos", lambda _rid: None)
    live = _mirror(etsy={"status": "published", "listing_id": "9"})
    gone = listing_sync.sweep_ended_records([live], "u1")
    assert gone == set() and store.deleted == []


def test_a_plain_mirror_still_goes_at_once(monkeypatch):
    store = _Store()
    monkeypatch.setattr(listing_sync, "db", store)
    monkeypatch.setattr(listing_sync, "_purge_photos", lambda _rid: None)
    ended_elsewhere = _mirror(etsy={"status": "ended", "listing_id": "9"})
    assert not listing_sync.keeps_grace(ended_elsewhere)
    assert listing_sync.sweep_ended_records([ended_elsewhere, _mirror()], "u1") \
        == {"ebay-110000000001"}

"""A listing that ends without selling is removed, not filed.

The seller's report was a card: "Ended", a broken image tile where the photo
should be, a Relist link, and a trash button they were expected to press. Not
one of them — a store that has been syncing for a while collects one per
listing that ever finished, and eBay stops serving the photos of an ended item
a few weeks later, so the older ones are blank rectangles. They asked for them
to go on their own.

So an ending is a removal now, at every door it can come through:

  * the End button (`POST /api/ebay/end-listing`, and the generic route for
    the other marketplaces),
  * the sync noticing that eBay ended a listing (`refresh_statuses`, covered
    in test_end_listing.py),
  * the store import, which used to MIRROR eBay's unsold list in as ended
    records — the source of most of those cards,
  * and a sweep for the ones already stored, because no ending is ever coming
    back for them.

Two things it is not. A SALE is still archived — that is the one finished
state worth keeping, and ending can discover one. And a record that is not on
eBay at all cannot have ended there: it goes back to being a draft, with its
photos, rather than being destroyed by a button that says "End listing".
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.services import listing_sync  # noqa: E402

ITEM = "123456789012"


class Store:
    """The seller's rows, with the deletes and writes made visible."""

    def __init__(self, records):
        self.records = {r["id"]: r for r in records}
        self.deleted: list[str] = []
        self.written: list[tuple[str, str]] = []
        self.refuse_delete = False

    # --- the bits of backend.db these routes touch -----------------------
    def enabled(self):
        return True

    def get_listing(self, listing_id):
        return self.records.get(listing_id)

    def list_listings(self, limit=50, user_id=None, statuses=None,
                      before=None):
        return [r for r in self.records.values()
                if (user_id is None or r.get("user_id") == user_id)
                and (statuses is None or r.get("status") in statuses)][:limit]

    def delete_listing(self, listing_id, user_id=None):
        if self.refuse_delete:
            from backend.errors import StorageUnavailable
            raise StorageUnavailable("the database is unreachable")
        if listing_id not in self.records:
            return False
        del self.records[listing_id]
        self.deleted.append(listing_id)
        return True

    def upsert_listing(self, listing_id, listing, status="draft", user_id=None,
                       when=None):
        rec = self.records.get(listing_id) or {"id": listing_id,
                                               "user_id": user_id}
        rec.update({"listing": listing, "status": status})
        self.records[listing_id] = rec
        self.written.append((listing_id, status))
        return True


def _live(rid="sess-a", status="published", item_id=ITEM):
    return {"id": rid, "user_id": "u1", "status": status,
            "listing": {"title": "A tie-dye shirt", "price": 34.0,
                        "source": "ebay", "ebay_listing_id": item_id}}


@pytest.fixture()
def ending(monkeypatch):
    """A signed-in seller with one live listing, and eBay's answer scripted."""
    def _run(records=None, end_result=None, refuse_delete=False):
        store = Store(records if records is not None else [_live()])
        store.refuse_delete = refuse_delete
        purged: list[str] = []
        monkeypatch.setattr(main.auth, "current_user",
                            lambda request: {"id": "u1"})
        monkeypatch.setattr(main, "_uid", lambda request: "u1")
        monkeypatch.setattr(main, "_ebay_creds_for",
                            lambda request: {"access_token": "tok",
                                             "ebay_username": "seller"})
        monkeypatch.setattr(main, "db", store)
        monkeypatch.setattr(listing_sync, "db", store)
        monkeypatch.setattr(listing_sync, "_purge_photos", purged.append)
        monkeypatch.setattr(
            listing_sync, "end",
            lambda token, listing: dict(end_result
                                        or {"ended": True,
                                            "listing_id": ITEM}))
        monkeypatch.setattr(listing_sync, "recent_sales", lambda _t: {})
        monkeypatch.setattr(main.notifications, "notify_sold",
                            lambda *_a, **_k: None)
        client = TestClient(main.app, raise_server_exceptions=False)
        res = client.post("/api/ebay/end-listing", json={"session_id": "sess-a"})
        return res, store, purged
    return _run


# ---------------------------------------------------------- the End button

def test_ending_a_listing_removes_it(ending):
    res, store, purged = ending()

    assert res.status_code == 200
    body = res.json()
    assert body["removed"] is True and body["status"] == "ended"
    assert store.deleted == ["sess-a"]
    assert "sess-a" not in store.records, "the card the seller ended is gone"
    assert purged == ["sess-a"], "its photos went with it"
    assert store.written == [], "nothing was filed under a status"


def test_a_listing_that_already_ended_on_ebay_is_removed_too(ending):
    """EndItem refuses a finished listing, so `end` probes and reports what
    became of it. An ending is an ending however it is discovered."""
    res, store, _ = ending(end_result={"ended": False, "not_live": True,
                                       "status": "ended",
                                       "message": "This listing had already "
                                                  "ended on eBay."})

    assert res.json()["removed"] is True
    assert store.deleted == ["sess-a"]


def test_a_sale_is_archived_rather_than_removed(ending):
    """The one finished state worth keeping. Ending can discover that the
    item sold, and a sale is what this app's whole archive is for — removing
    it would delete the seller's record of a sale to answer a buyer with."""
    res, store, purged = ending(end_result={"ended": False, "not_live": True,
                                            "status": "sold"})

    body = res.json()
    assert body["status"] == "sold" and not body.get("removed")
    assert store.deleted == []
    assert ("sess-a", "sold") in store.written


def test_a_listing_that_was_never_on_ebay_is_not_destroyed(ending):
    """`not_live` with no status is a record with no item id: there is
    nothing on eBay for it to have ended. Removing it would delete a seller's
    photos and drafted copy over a mis-press on a card that should not have
    offered End at all. It goes back to being a draft."""
    record = _live(item_id="")
    res, store, purged = ending(records=[record])

    assert res.status_code == 200
    assert store.deleted == [] and purged == []
    assert ("sess-a", "draft") in store.written
    assert store.records["sess-a"]["status"] == "draft"


def test_a_removal_the_database_refuses_is_reported(ending):
    """It came off eBay, and the copy here is still standing. Saying `ok`
    would leave a card offering to revise and repromote a listing that is
    gone — the same rule the sold path already follows for its write."""
    res, store, purged = ending(refuse_delete=True)

    assert res.status_code == 503
    assert "don't end it again" in res.json()["detail"]
    assert purged == [], "nothing is purged for a row that is still there"


# ------------------------------------------------- the ones already stored

def test_the_sync_sweeps_ended_records_off_the_store(monkeypatch):
    """The backlog. These were imported as `ended` before the rule existed
    and nothing else will ever revisit them, so the cheap sync that runs
    whenever the app is open clears them."""
    from backend.services import sync_guard

    sync_guard.reset()
    store = Store([_live(), _live("ebay-999", status="ended", item_id="999"),
                   {"id": "sess-sold", "user_id": "u1", "status": "sold",
                    "listing": {"ebay_listing_id": "777"}}])
    purged: list[str] = []
    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": "u1"})
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "tok",
                                         "ebay_username": "seller"})
    monkeypatch.setattr(main, "db", store)
    monkeypatch.setattr(listing_sync, "db", store)
    monkeypatch.setattr(listing_sync, "_purge_photos", purged.append)
    monkeypatch.setattr(main.listing_sync, "reconcile_recent",
                        lambda token, uid, records, account="": (0, set()))
    monkeypatch.setattr(main.listing_sync, "refresh_statuses",
                        lambda token, uid, records, account="": 0)

    body = TestClient(main.app).post("/api/ebay/sync-listings", json={}).json()

    assert body["removed"] == 1
    assert store.deleted == ["ebay-999"] and purged == ["ebay-999"]
    assert "sess-sold" in store.records, "a sale is not swept"
    assert "sess-a" in store.records, "a live listing is not swept"
    sync_guard.reset()


def test_the_import_never_mirrors_ebays_unsold_list(monkeypatch):
    """Where the seller's blank cards came from. Importing an ended listing
    so that a later sweep can delete it is work in a circle, and until one
    ran the card was on the grid."""
    from backend import db
    from backend.services import ebay_trading

    monkeypatch.setattr(ebay_trading, "active_listing_ids",
                        lambda *_a, **_k: [])
    monkeypatch.setattr(listing_sync, "recent_sales", lambda _t: {})
    monkeypatch.setattr(db, "list_listings", lambda **_k: [])

    def _never(*_a, **_k):
        raise AssertionError("the import read eBay's unsold list")

    monkeypatch.setattr(ebay_trading, "unsold_listing_ids", _never)
    result = listing_sync.import_active("tok", "u1")

    assert result["found"] == 0 and result["imported"] == 0

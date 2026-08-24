"""Connecting a SECOND eBay account must not drag the first one's store along.

Listing records belong to the app user, not to an eBay account, so nothing
about connecting a different eBay account moves or hides what the previous one
left behind. Worse, eBay's GetItem answers for any seller's item, so the status
sweeps kept confirming the old account's listings as live and healthy under the
new one — which is what "it pulled my other account's items into this one"
actually was.

`Listing.ebay_account` is what settles it: every record eBay touches is stamped
with the account it belongs to, and every read or write is scoped by it.
"""
from __future__ import annotations

import pytest

from backend.services import listing_sync

OLD_ITEM = "111111111111"
NEW_ITEM = "222222222222"


class FakeDb:
    def __init__(self, records):
        self.records = {r["id"]: r for r in records}
        self.deleted: list[str] = []

    def list_listings(self, limit=50, user_id=None):
        return [r for r in self.records.values() if r.get("user_id") == user_id]

    def upsert_listing(self, listing_id, listing, status="draft", user_id=None,
                       when=None):
        rec = self.records.get(listing_id) or {"id": listing_id, "user_id": user_id}
        rec.update({"listing": listing, "status": status})
        self.records[listing_id] = rec

    def delete_listing(self, listing_id, user_id=None):
        if listing_id in self.records:
            del self.records[listing_id]
            self.deleted.append(listing_id)
            return True
        return False


class FakeTrading:
    """The NEW account's store: one item, which the old account never had."""

    def __init__(self):
        self.status_probes: list[str] = []

    def active_listing_ids(self, token, limit=0):
        return [NEW_ITEM]

    def sold_sales(self, token, limit=0):
        return {}

    def unsold_listing_ids(self, token, limit=0):
        return []

    def get_listing(self, token, item_id):
        return {"title": "A thing on the new account", "price": 20.0,
                "quantity": 1, "condition": "USED_GOOD", "source": "ebay",
                "ebay_listing_id": item_id, "image_urls": []}

    def listing_status(self, token, item_id):
        self.status_probes.append(item_id)
        return "published", 0, 3


def _record(rid, item_id, account, status="published"):
    return {"id": rid, "user_id": "u1", "status": status,
            "listing": {"title": f"item {item_id}", "source": "ebay",
                        "ebay_listing_id": item_id, "ebay_account": account}}


@pytest.fixture
def wired(monkeypatch):
    def run(records):
        db = FakeDb(records)
        trading = FakeTrading()
        monkeypatch.setattr(listing_sync, "db", db)
        monkeypatch.setattr(listing_sync, "ebay_trading", trading)
        return db, trading
    return run


# --- belongs_to -------------------------------------------------------------

def test_a_record_from_another_account_is_out_of_scope():
    assert not listing_sync.belongs_to({"ebay_account": "old-seller"}, "new-seller")


def test_a_record_from_this_account_is_in_scope():
    assert listing_sync.belongs_to({"ebay_account": "new-seller"}, "new-seller")


def test_an_unstamped_record_still_counts_as_this_account():
    """Records predate the field; the only account they can belong to is the
    one that was connected when they were written."""
    assert listing_sync.belongs_to({"ebay_account": ""}, "new-seller")
    assert listing_sync.belongs_to({}, "new-seller")


def test_no_connected_account_scopes_nothing_out():
    """An unreadable account name must not quietly exclude the whole store."""
    assert listing_sync.belongs_to({"ebay_account": "old-seller"}, "")


# --- import -----------------------------------------------------------------

def test_import_leaves_the_other_accounts_records_untouched(wired):
    old = _record("ebay-" + OLD_ITEM, OLD_ITEM, "old-seller")
    db, _ = wired([old])
    listing_sync.import_active("token", "u1", account="new-seller")

    kept = db.records["ebay-" + OLD_ITEM]["listing"]
    assert kept["ebay_account"] == "old-seller", "not re-labelled"
    assert kept["title"] == f"item {OLD_ITEM}", "not overwritten"
    assert "ebay-" + OLD_ITEM not in db.deleted, "not deleted either"


def test_import_stamps_the_connected_account_on_what_it_writes(wired):
    db, _ = wired([])
    listing_sync.import_active("token", "u1", account="new-seller")
    fresh = db.records["ebay-" + NEW_ITEM]["listing"]
    assert fresh["ebay_account"] == "new-seller"


def test_import_labels_an_unstamped_record_it_re_syncs(wired):
    """A record from before the field existed gets its account written the
    first time this account's sync sees the same item."""
    db, _ = wired([_record("sess-1", NEW_ITEM, "")])
    listing_sync.import_active("token", "u1", account="new-seller")
    assert db.records["sess-1"]["listing"]["ebay_account"] == "new-seller"


# --- status sweeps ----------------------------------------------------------

def test_status_sweep_never_probes_another_accounts_listing(wired):
    """The bug in one line: GetItem answers for anyone's item, so without the
    scope the old account's listings were re-confirmed as live forever."""
    db, trading = wired([])
    changed = listing_sync.refresh_statuses(
        "token", "u1", [_record("ebay-" + OLD_ITEM, OLD_ITEM, "old-seller")],
        account="new-seller")
    assert trading.status_probes == []
    assert changed == 0


def test_status_sweep_still_probes_this_accounts_listings(wired):
    _db, trading = wired([])
    listing_sync.refresh_statuses(
        "token", "u1", [_record("ebay-" + NEW_ITEM, NEW_ITEM, "new-seller")],
        account="new-seller")
    assert trading.status_probes == [NEW_ITEM]


def test_reconcile_skips_the_other_accounts_records(wired):
    _db, trading = wired([])
    changed, covered = listing_sync.reconcile_recent(
        "token", "u1", [_record("ebay-" + OLD_ITEM, OLD_ITEM, "old-seller")],
        account="new-seller")
    assert (changed, covered) == (0, set())
    assert trading.status_probes == []

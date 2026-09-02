"""The store sync must not re-import listings this app published.

A listing published from the app lives under its session id with eBay's item
id stamped on it; the sync writes imported listings under "ebay-<item>". When
the sync matched on that mirror id alone, every app-published listing came
back as a SECOND card on the next sync — one "Thryft", one "eBay", same item.
"""
from __future__ import annotations

import pytest

from backend.services import listing_sync


class FakeDb:
    """Just enough of backend.db for import_active: a listing table."""

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
    """eBay's side: one active item, with details."""

    def __init__(self, item_id, detail):
        self.item_id = item_id
        self.detail = detail

    def active_listing_ids(self, token, limit=0):
        return [self.item_id]

    def sold_sales(self, token, limit=0):
        return {}

    def unsold_listing_ids(self, token, limit=0):
        return []

    def get_listing(self, token, item_id):
        return dict(self.detail)


ITEM = "123456789012"

# What eBay reports back for the listing the app published.
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
}


def _app_record(**listing_extra):
    """A listing this app created and published through Trading."""
    listing = {
        "title": "Original framed piano painting — signed",
        "description": "Written by the AI here.",
        "price": 45.0,
        "images": ["img_000.jpg"],
        "purchase_price": 4.0,
        "source": "ebay",
        "ebay_listing_id": ITEM,
    }
    listing.update(listing_extra)
    return {"id": "sess-abc", "user_id": "u1", "status": "published",
            "listing": listing}


def _mirror_record():
    """The duplicate an earlier sync imported for the same item."""
    return {"id": listing_sync.record_id(ITEM), "user_id": "u1",
            "status": "published", "listing": dict(DETAIL)}


@pytest.fixture
def synced(monkeypatch):
    """Run import_active against fake eBay + DB; returns (db, result)."""
    def run(records):
        fake_db = FakeDb(records)
        monkeypatch.setattr(listing_sync, "db", fake_db)
        monkeypatch.setattr(listing_sync, "ebay_trading",
                            FakeTrading(ITEM, DETAIL))
        result = listing_sync.import_active("token", "u1")
        return fake_db, result
    return run


def test_published_listing_is_not_imported_a_second_time(synced):
    fake_db, result = synced([_app_record()])
    assert set(fake_db.records) == {"sess-abc"}, "the app record must be reused"
    assert result["imported"] == 0
    assert result["updated"] == 1


def test_sync_refreshes_the_app_record_without_losing_local_work(synced):
    fake_db, _ = synced([_app_record()])
    listing = fake_db.records["sess-abc"]["listing"]
    # eBay owns the live facts...
    assert listing["image_urls"] == DETAIL["image_urls"]
    assert listing["quantity"] == 1
    # ...the seller owns the rest.
    assert listing["title"] == "Original framed piano painting — signed"
    assert listing["images"] == ["img_000.jpg"]
    assert listing["purchase_price"] == 4.0


def test_existing_duplicate_is_cleaned_up(synced):
    fake_db, result = synced([_app_record(), _mirror_record()])
    assert listing_sync.record_id(ITEM) in fake_db.deleted
    assert set(fake_db.records) == {"sess-abc"}
    assert result["deduped"] == 1


def test_duplicate_is_cleaned_up_even_when_ebay_stops_returning_it(synced):
    """An ended duplicate never comes back in the active list, so the cleanup
    can't wait for eBay to mention the item again."""
    old = "999888777666"
    app = dict(_app_record(), id="sess-old")
    app["listing"] = dict(app["listing"], ebay_listing_id=old)
    mirror = {"id": listing_sync.record_id(old), "user_id": "u1",
              "status": "ended", "listing": {"ebay_listing_id": old}}
    fake_db, result = synced([app, mirror])
    assert listing_sync.record_id(old) in fake_db.deleted
    assert result["deduped"] == 1


def test_a_real_ebay_listing_still_imports(synced):
    fake_db, result = synced([])
    assert set(fake_db.records) == {listing_sync.record_id(ITEM)}
    assert result["imported"] == 1
    assert result["deduped"] == 0


def test_resync_updates_the_mirror_in_place(synced):
    fake_db, result = synced([_mirror_record()])
    assert set(fake_db.records) == {listing_sync.record_id(ITEM)}
    assert result["updated"] == 1
    assert result["deduped"] == 0


def test_another_sellers_record_is_never_matched(synced):
    other = dict(_app_record(), user_id="u2")
    fake_db, result = synced([other])
    assert result["imported"] == 1
    assert set(fake_db.records) == {"sess-abc", listing_sync.record_id(ITEM)}


# ---------------------------------------------------------------- unit bits

def test_item_id_falls_back_to_the_view_url():
    assert listing_sync._item_id_of({"ebay_listing_id": ITEM}) == ITEM
    assert listing_sync._item_id_of(
        {"view_url": f"https://www.ebay.com/itm/{ITEM}"}) == ITEM
    assert listing_sync._item_id_of(
        {"view_url": f"https://www.ebay.com/itm/framed-art/{ITEM}?hash=x"}) == ITEM
    assert listing_sync._item_id_of({"view_url": "https://example.com/"}) == ""
    assert listing_sync._item_id_of({}) == ""


def test_app_record_wins_over_the_mirror_either_way_round():
    app, mirror = _app_record(), _mirror_record()
    assert listing_sync._index_by_item([app, mirror])[ITEM] is app
    assert listing_sync._index_by_item([mirror, app])[ITEM] is app


def test_the_dedupe_reads_past_a_big_sellers_whole_store(monkeypatch):
    """The read that feeds the dedupe used to be sized off this run's job count
    (max(1000, jobs * 2)). A seller with a few hundred listings plus their
    ended/sold mirrors can hold more rows than that — and a record the read
    misses is one the dedupe can't match, which puts the duplicate pair right
    back on screen. It has to see everything.

    The fake truncates exactly like the real query, so an undersized limit
    fails here rather than only in a big production account."""
    class TruncatingDb(FakeDb):
        def list_listings(self, limit=50, user_id=None):
            mine = [r for r in self.records.values() if r.get("user_id") == user_id]
            return mine[:limit]

    # One app-published listing for the item eBay reports, buried behind more
    # unrelated rows than the old formula would have read.
    filler = [{"id": f"sess-{i}", "user_id": "u1", "status": "published",
               "listing": {"title": f"Other thing {i}",
                           "ebay_listing_id": f"9{i:011d}"}}
              for i in range(2500)]
    records = [*filler, _app_record(), _mirror_record()]

    fake_db = TruncatingDb(records)
    monkeypatch.setattr(listing_sync, "db", fake_db)
    monkeypatch.setattr(listing_sync, "ebay_trading", FakeTrading(ITEM, DETAIL))
    result = listing_sync.import_active("token", "u1")

    assert listing_sync.record_id(ITEM) in fake_db.deleted, \
        "the duplicate survived because the dedupe never saw the app record"
    assert result["deduped"] == 1
    assert result["imported"] == 0


def test_merge_keeps_an_app_records_own_edit_path():
    # Published through the Inventory API: no source, and it has to stay that
    # way or later edits go to Trading's ReviseItem, which eBay refuses there.
    classic = {"title": "Mine", "ebay_listing_id": ITEM, "source": ""}
    merged = listing_sync._merge(classic, dict(DETAIL), own_source=True)
    assert merged["source"] == ""
    # An imported record is Trading-routed, as before.
    assert listing_sync._merge({"title": "Mine"}, dict(DETAIL))["source"] == "ebay"


# ------------------------------------------------- the account the sync runs as

# What every listing this app PUBLISHES carries: eBay's immutable account id,
# stamped at publish time (marketplaces/ebay_provider). `owns` decides on it
# whenever a record has one, with NO username fallback — so a sync that knows
# only the username matches none of these, and re-imports every one of them.
ACCOUNT_ID = "1234567890"
ACCOUNT_NAME = "thryftshop"
CREDS = {"access_token": "token", "ebay_user_id": ACCOUNT_ID,
         "ebay_username": ACCOUNT_NAME}


def _published_record():
    """An app-published listing as it really looks: stamped with both halves
    of the account it went live on."""
    return _app_record(ebay_account=ACCOUNT_NAME, ebay_account_id=ACCOUNT_ID)


@pytest.fixture
def synced_as(monkeypatch):
    """`synced`, but running as a named eBay account."""
    def run(records, account):
        fake_db = FakeDb(records)
        monkeypatch.setattr(listing_sync, "db", fake_db)
        monkeypatch.setattr(listing_sync, "ebay_trading",
                            FakeTrading(ITEM, DETAIL))
        return fake_db, listing_sync.import_active("token", "u1",
                                                   account=account)
    return run


def test_a_published_listing_is_matched_when_the_sync_knows_who_it_is(synced_as):
    """The duplicate pair in its real form.

    Every fixture above syncs with no account at all, which makes `owns` fall
    back to the username and match anything. Production never looks like that:
    the record carries an immutable id and the sync runs as a connected
    account. Given the creds bundle, the id matches and the app's own record
    is updated in place."""
    fake_db, result = synced_as([_published_record()], CREDS)
    assert set(fake_db.records) == {"sess-abc"}, "the app record must be reused"
    assert result["imported"] == 0
    assert result["updated"] == 1


def test_a_name_alone_cannot_see_the_records_it_has_to_match(synced_as):
    """Why the route had to change, held in place.

    Passing the username is not a smaller version of passing the bundle — it
    is a caller with no id, and `owns` refuses to match an id-stamped record
    for one. The sync then imports the very listing it published as a second
    card. If this ever passes, the dedupe has stopped depending on the
    identity it is given and the fix has been undone somewhere."""
    fake_db, result = synced_as([_published_record()], ACCOUNT_NAME)
    assert result["imported"] == 1
    assert listing_sync.record_id(ITEM) in fake_db.records


def test_the_duplicates_already_in_the_store_are_cleaned_up(synced_as):
    """The pairs a seller is looking at right now go on the next sync — the
    fix has to heal the store, not just stop adding to it."""
    fake_db, result = synced_as([_published_record(), _mirror_record()], CREDS)
    assert listing_sync.record_id(ITEM) in fake_db.deleted
    assert set(fake_db.records) == {"sess-abc"}
    assert result["deduped"] == 1


def test_the_mirror_it_writes_carries_the_immutable_id(synced_as):
    """A mirror stamped with the username alone can only ever be matched by a
    handle the seller can rename. The item came out of this account's own
    selling lists, so the id is a fact about it."""
    fake_db, _ = synced_as([], CREDS)
    written = fake_db.records[listing_sync.record_id(ITEM)]["listing"]
    assert written["ebay_account_id"] == ACCOUNT_ID
    assert written["ebay_account"] == ACCOUNT_NAME


def test_another_accounts_listing_is_still_left_alone(synced_as):
    """The scoping this all rests on: a record stamped with a DIFFERENT id is
    not matched, however similar it looks — it is another seller's."""
    theirs = _app_record(ebay_account="someone-else",
                         ebay_account_id="9999999999")
    fake_db, result = synced_as([theirs], CREDS)
    assert result["imported"] == 1
    assert set(fake_db.records) == {"sess-abc", listing_sync.record_id(ITEM)}

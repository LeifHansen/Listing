"""A database that cannot be read is not a seller with nothing in it.

`db.list_listings` answered `[]` on any exception. Every read in db.py does,
and for most of them it is a survivable degradation. For this one it is not,
because "the seller's store" is the input to decisions that WRITE.

The worst is the eBay import. It reads every record the seller has in order to
match incoming eBay items against listings this app already holds; anything it
does not find, it imports as new. A read that failed therefore looked exactly
like a seller with an empty store — so one Postgres blip during a sync would
import a second copy of the seller's ENTIRE eBay store, silently, and report
it as a successful sync. The duplicates then have to be found and merged by
hand, and each one is a real listing on eBay.

The others are quieter but the same shape: a release pass that reports
"released 0" as success, a status sweep that reports checking a store it never
read, and a migration that reports nothing to migrate.

So the store read is now strict — it raises rather than inventing an empty
answer. Callers that genuinely tolerate an empty result ask for it by name.

Deliberately NOT changed: an app running with no database at all. That is a
configuration, not a failure; `db.enabled()` is false, /api/health says so,
and the store really is empty.
"""
from __future__ import annotations

import pytest

from backend.errors import StorageUnavailable


@pytest.fixture()
def broken_db(monkeypatch):
    """Every engine call raises, the way a Neon outage presents."""
    from backend import db

    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(db, "_get_engine", lambda: _Boom())
    return db


@pytest.fixture()
def no_db(monkeypatch):
    from backend import db

    monkeypatch.setattr(db, "_get_engine", lambda: None)
    return db


# --------------------------------------------------------------- the read

def test_a_failed_read_raises_rather_than_reporting_an_empty_store(broken_db):
    with pytest.raises(StorageUnavailable):
        broken_db.list_listings(user_id="u1")


def test_an_app_with_no_database_still_reports_an_empty_store(no_db):
    """Not a failure: nothing is configured, so there is nothing to read."""
    assert no_db.list_listings(user_id="u1") == []


def test_the_lenient_read_is_still_available_by_name(broken_db):
    """Callers that genuinely tolerate an empty answer have to say so, which
    is the point — it puts the decision at the call site."""
    assert broken_db.list_listings_best_effort(user_id="u1") == []


# ------------------------------------------------- the import that duplicated

def test_a_failed_read_does_not_reimport_the_sellers_whole_store(monkeypatch):
    """The finding. With the known-records read failing, every eBay item is
    unmatched, so every one is imported again."""
    from backend import db
    from backend.services import ebay_trading, listing_sync

    monkeypatch.setattr(ebay_trading, "active_listing_ids",
                        lambda *a, **k: ["110", "111"])
    monkeypatch.setattr(ebay_trading, "unsold_listing_ids", lambda *a, **k: [])
    monkeypatch.setattr(listing_sync, "recent_sales", lambda _t: {})

    def _unavailable(**_k):
        raise StorageUnavailable("db down")
    monkeypatch.setattr(db, "list_listings", _unavailable)

    saved: list = []
    monkeypatch.setattr(db, "upsert_listing",
                        lambda rid, *a, **k: saved.append(rid))

    with pytest.raises(StorageUnavailable):
        listing_sync.import_active("tok", "u1")

    assert saved == [], \
        "a failed store read imported the seller's listings a second time"


def test_the_import_still_works_when_the_read_succeeds(monkeypatch):
    """The guard must not be a blanket refusal: a genuinely empty store still
    imports, which is the first sync after connecting."""
    from backend import db
    from backend.services import ebay_trading, listing_sync

    monkeypatch.setattr(ebay_trading, "active_listing_ids",
                        lambda *a, **k: [])
    monkeypatch.setattr(ebay_trading, "unsold_listing_ids", lambda *a, **k: [])
    monkeypatch.setattr(listing_sync, "recent_sales", lambda _t: {})
    monkeypatch.setattr(db, "list_listings", lambda **_k: [])

    assert listing_sync.import_active("tok", "u1")["imported"] == 0

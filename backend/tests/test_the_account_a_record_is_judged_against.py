"""Which eBay account a record is judged against, and what a wrong answer costs.

Two failures, one subject. Both come from a caller reading the raw owner field
instead of the rule that interprets it.

The duplicate pairs sellers were looking at — one card badged Thryft, one
badged eBay, same photo, same title, same price — were made here.

Ownership is decided on eBay's immutable user id wherever a record carries
one, with no username fallback (`listing_sync.owns`, and deliberately so: a
handle can be renamed and re-registered). Every listing this app publishes
carries one, stamped at publish time. But the import route handed the sync
`creds["ebay_username"]` — a caller with no id — so `owns` refused to match a
single app-published record. They were dropped from the dedupe's field of
view as "another account's", and the sync then imported each of them again as
an `ebay-<item>` mirror. Every sync. The log line for it read

    sync: user=... skipping 1 record(s) from another eBay account

about the seller's own listings.

The module was never wrong; the argument was. So this covers the argument,
which is the part a test of listing_sync cannot see.

The second is the ending route. A record whose owner could not be named is
stamped with the UNKNOWN_ACCOUNT sentinel — "we cannot prove whose this is",
not "it is someone else's". Compared as though it were a username, it never
matches the connected seller, so End refused with "this is on your other eBay
account (@previous account)": a store that does not exist and cannot be
reconnected, on a listing that is then un-endable here for good. `owns` and
the publish path both already draw that line; this route did not.
"""
from __future__ import annotations

import threading

import pytest

# Importing backend.main pulls the whole app in. The lint+unit job has none of
# these, so it skips this file; the smoke job's "API tests" step runs it, and
# that step fails on a skip so it can never quietly stop running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.services import listing_sync  # noqa: E402

ACCOUNT_ID = "1234567890"
ACCOUNT_NAME = "thryftshop"
CREDS = {"access_token": "tok", "ebay_user_id": ACCOUNT_ID,
         "ebay_username": ACCOUNT_NAME}

# A listing this app published: stamped with both halves of the account it
# went live on, exactly as marketplaces/ebay_provider leaves it.
PUBLISHED = {"title": "Soriano Ceramics Siamese Cat Art Tile",
             "source": "ebay", "ebay_listing_id": "123456789012",
             "ebay_account": ACCOUNT_NAME, "ebay_account_id": ACCOUNT_ID}


@pytest.fixture
def started(monkeypatch):
    """Run POST /api/ebay/import-listings and return the `account` the sync
    was actually handed."""
    got: dict = {}
    ran = threading.Event()

    def _import_active(token, user_id, limit=0, on_progress=None, account=""):
        got["account"] = account
        ran.set()
        return {"found": 0, "imported": 0, "updated": 0, "deduped": 0,
                "failed": 0}

    monkeypatch.setattr(main.auth, "current_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main, "_ebay_creds_for", lambda _r: dict(CREDS))
    monkeypatch.setattr(main.db, "enabled", lambda: True)
    monkeypatch.setattr(main.listing_sync, "import_active", _import_active)

    res = TestClient(main.app).post("/api/ebay/import-listings")
    assert res.status_code == 200, res.text
    assert ran.wait(5), "the import job never reached the sync"
    return got["account"]


def test_the_sync_is_told_which_account_it_is(started):
    """Not just what it is called. Fails against the old route, which passed
    the username string and left the id unknowable."""
    account_id, account_name = listing_sync._identity(started)
    assert account_id == ACCOUNT_ID
    assert account_name == ACCOUNT_NAME


def test_the_sync_can_recognise_the_listings_this_app_published(started):
    """The consequence, stated as the thing that actually broke: given what
    the route passes, a record this app published is the seller's own. When
    this fails, every one of those listings comes back as a second card."""
    assert listing_sync.owns(PUBLISHED, started) is True


# ------------------------------------------- ending a listing we can't place

def _endable(monkeypatch, owner: str):
    """Drive POST /api/listings/{id}/end for a record stamped `owner`, and
    report whether the ending was refused over the account."""
    ended: list[str] = []
    rec = {"id": "sess-abc", "user_id": "u1", "status": "published",
           "listing": {"title": "Simon Pearce Hand Blown Glass Heart",
                       "source": "ebay", "ebay_listing_id": "123456789012",
                       "ebay_account": owner}}
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main.auth, "current_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main, "_ebay_creds_for", lambda _r: dict(CREDS))
    monkeypatch.setattr(main.db, "get_listing", lambda _i: rec)
    monkeypatch.setattr(main.db, "upsert_listing",
                        lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)
    monkeypatch.setattr(main.listing_sync, "end", lambda token, listing: (
        ended.append(listing.ebay_listing_id) or {"ended": True}))
    return TestClient(main.app).post("/api/ebay/end-listing",
                                     json={"session_id": "sess-abc"}), ended


def test_a_listing_whose_owner_we_could_not_name_can_still_be_ended(monkeypatch):
    """The sentinel is not a rival account. Fails against the old route, which
    compared it as a username and answered 400."""
    res, ended = _endable(monkeypatch, listing_sync.UNKNOWN_ACCOUNT)
    assert res.status_code == 200, res.text
    assert ended == ["123456789012"]


def test_a_listing_on_a_named_other_account_is_still_refused(monkeypatch):
    """The check itself stays: a NAMED other store is real evidence, and
    EndItem there would act on another seller's live listing."""
    res, ended = _endable(monkeypatch, "someone-else")
    assert res.status_code == 400
    assert "someone-else" in res.json()["detail"]
    assert ended == []


def test_the_connected_accounts_own_listing_ends(monkeypatch):
    res, ended = _endable(monkeypatch, ACCOUNT_NAME)
    assert res.status_code == 200, res.text
    assert ended == ["123456789012"]

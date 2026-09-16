"""Dismiss all, and the same answered question does not come back tomorrow.

The duplicate card is rebuilt from scratch on every Dashboard load, so a
seller who looks at a pair, decides the two really are different items and
waves them away gets asked again on the next load, and the next. That is the
shape that turns an advisory into a nag -- and there is no press that clears
it the way finishing the work clears a suggestion, because the honest answer
here is often "nothing needs doing".

So the dismissal has to persist. The danger in persisting it is the opposite
failure: a "never show me this again" that hides a REAL duplicate for the life
of the account, silently, after one stray thumb. The resolution is that a
dismissal is pinned to the pair AS THE SELLER JUDGED IT -- which items, what
each costs, how each sells. Edit any of that and the question is a new one, so
it comes back; leave it alone and it stays gone.

These tests are mostly about that seam: what counts as an edit, what is just
the sync moving underneath, and what a dismissal must never reach.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit
from backend.services import duplicates

TITLE = "A. Buitron 2008 Signed Original Painting Tiger Cat Folk Art"


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "dupes@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("dupes@example.com")["id"]
    return client, dbmod, uid


def _list(dbmod, uid, lid, item_id, *, title=TITLE, price=145.0,
          fmt="FIXED_PRICE", started="2026-09-13T09:00:00Z", **extra):
    assert dbmod.upsert_listing(lid, {
        "title": title, "price": price, "ebay_listing_id": item_id,
        "listing_format": fmt, "ebay_start_time": started,
        "view_url": f"https://www.ebay.com/itm/{item_id}", **extra,
    }, status="published", user_id=uid)


def _pair(dbmod, uid, rows=("sess-a", "ebay-158295185672"), **kw):
    """The shape in the screenshot: one pair, two live eBay items."""
    _list(dbmod, uid, rows[0], "158289510185", **kw)
    _list(dbmod, uid, rows[1], "158295185672", **kw)


def _groups(client):
    res = client.get("/api/ebay/duplicates")
    assert res.status_code == 200
    return res.json()


# ------------------------------------------------- the fingerprint itself

def test_the_same_pair_twice_running_digests_the_same():
    """Nothing in the digest may come from the scan rather than the data --
    a fingerprint that drifted between two reads of an unchanged pair would
    make every dismissal last exactly one page load."""
    records = [
        {"id": "sess-a", "status": "published",
         "listing": {"title": TITLE, "price": 145.0,
                     "ebay_listing_id": "111", "listing_format": "FIXED_PRICE",
                     "ebay_start_time": "2026-09-13T09:00:00Z"}},
        {"id": "ebay-222", "status": "published",
         "listing": {"title": TITLE, "price": 145.0,
                     "ebay_listing_id": "222", "listing_format": "FIXED_PRICE",
                     "ebay_start_time": "2026-09-15T09:00:00Z"}},
    ]
    first = duplicates.find(records)[0]["fingerprint"]
    # Read back in the other order, as a different page of the same store
    # would hand them over.
    second = duplicates.find(list(reversed(records)))[0]["fingerprint"]
    assert first == second


def test_the_watch_count_does_not_move_the_digest():
    """eBay reporting a new watcher is not the seller changing their mind.

    This is the one that matters most: watch counts tick on their own, on
    every sweep, so a digest that included them would re-raise every settled
    pair within a day and the dismissal would look broken rather than absent.
    """
    def group(watchers):
        return duplicates.find([
            {"id": "sess-a", "status": "published",
             "listing": {"title": TITLE, "price": 145.0, "watch_count": watchers,
                         "ebay_listing_id": "111", "listing_format": "FIXED_PRICE"}},
            {"id": "ebay-222", "status": "published",
             "listing": {"title": TITLE, "price": 145.0, "watch_count": watchers,
                         "ebay_listing_id": "222", "listing_format": "FIXED_PRICE"}},
        ])[0]["fingerprint"]

    assert group(0) == group(9)


# ------------------------------------------------ dismissing, and what sticks

def test_dismiss_all_takes_the_card_away_and_keeps_it_away(seller):
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    before = _groups(client)
    assert before["total"] == 1

    res = client.post("/api/ebay/duplicates/dismiss",
                      json={"fingerprints": [g["fingerprint"]
                                             for g in before["groups"]]})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "dismissed": 1}

    after = _groups(client)
    assert after["groups"] == []
    # Said, not merely absent: the scan still finds the pair, and the card
    # needs to be able to tell "nothing here" from "you've seen this".
    assert after["dismissed"] == 1


def test_nothing_is_ended_on_ebay(seller):
    """A dismissal is a note to ourselves. Every listing stays live."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    client.post("/api/ebay/duplicates/dismiss", json={})
    live = dbmod.list_listings(limit=50, user_id=uid,
                               statuses=duplicates.LIVE_STATUSES)
    assert sorted(r["id"] for r in live) == ["ebay-158295185672", "sess-a"]


def test_editing_a_price_brings_the_pair_back(seller):
    """The seller changed one of the things they judged, so the judgement no
    longer covers what is on screen."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    client.post("/api/ebay/duplicates/dismiss", json={})
    assert _groups(client)["total"] == 0

    _list(dbmod, uid, "sess-a", "158289510185", price=120.0)
    back = _groups(client)
    assert back["total"] == 1
    assert back["dismissed"] == 0


def test_switching_one_to_an_auction_brings_the_pair_back(seller):
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    client.post("/api/ebay/duplicates/dismiss", json={})
    _list(dbmod, uid, "sess-a", "158289510185", fmt="AUCTION")
    assert _groups(client)["total"] == 1


def test_a_third_listing_under_the_same_title_brings_it_back(seller):
    """Dismissing a pair is not dismissing the title. A duplicate of the
    duplicate is a new thing to look at."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    client.post("/api/ebay/duplicates/dismiss", json={})
    _list(dbmod, uid, "sess-c", "158299999999")
    assert _groups(client)["total"] == 1


def test_a_sweep_that_only_notes_watchers_does_not_bring_it_back(seller):
    """The whole point of the narrow digest, end to end: the store sync
    rewrites these rows constantly, and none of that is a seller edit."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    client.post("/api/ebay/duplicates/dismiss", json={})
    _list(dbmod, uid, "sess-a", "158289510185", watch_count=4)
    _list(dbmod, uid, "ebay-158295185672", "158295185672", watch_count=7)
    assert _groups(client)["total"] == 0


def test_one_seller_dismissal_does_not_reach_another_seller(seller, dbmod):
    """The ledger hangs off the user row, and the digest is only a digest --
    two accounts whose pairs fingerprint IDENTICALLY must still be asked
    separately, or one seller's judgement silences another's."""
    client, _dbmod, uid = seller
    _pair(dbmod, uid)
    client.post("/api/ebay/duplicates/dismiss", json={})

    other = TestClient(main.app)
    assert other.post("/api/auth/signup",
                      json={"email": "other@example.com",
                            "password": "password123"}).status_code < 400
    other_uid = dbmod.get_user_by_email("other@example.com")["id"]
    # Different rows, same items and price, so the same fingerprint.
    _pair(dbmod, other_uid, rows=("sess-b", "ebay-b"))
    mine, theirs = _groups(client), _groups(other)
    assert mine["total"] == 0 and mine["dismissed"] == 1
    assert theirs["total"] == 1 and theirs["dismissed"] == 0
    assert mine["groups"] == [] and theirs["groups"][0]["title"] == TITLE


# --------------------------------------------- what a dismissal must not do

def test_only_what_the_card_had_on_screen_is_dismissed(seller):
    """A group that turned up between the load and the press is one the
    seller never saw. Waving away an unseen duplicate is the one thing this
    button must not do."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    seen = [g["fingerprint"] for g in _groups(client)["groups"]]

    # A second pair lands while the card sits there.
    _list(dbmod, uid, "sess-x", "158200000001", title="Tammy Antezana Pour Set")
    _list(dbmod, uid, "ebay-158200000002", "158200000002",
          title="Tammy Antezana Pour Set")

    client.post("/api/ebay/duplicates/dismiss", json={"fingerprints": seen})
    left = _groups(client)
    assert [g["title"] for g in left["groups"]] == ["Tammy Antezana Pour Set"]


def test_a_stale_fingerprint_dismisses_nothing(seller):
    """The pair moved under the card between the load and the press: the
    seller's judgement was about the old state, so it buys nothing."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    stale = [g["fingerprint"] for g in _groups(client)["groups"]]
    _list(dbmod, uid, "sess-a", "158289510185", price=99.0)

    res = client.post("/api/ebay/duplicates/dismiss",
                      json={"fingerprints": stale})
    assert res.json()["dismissed"] == 0
    assert _groups(client)["total"] == 1


def test_a_logged_out_press_is_refused(seller):
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    anon = TestClient(main.app)
    assert anon.post("/api/ebay/duplicates/dismiss", json={}).status_code == 401


def test_the_ledger_does_not_grow_past_the_groups_that_exist(seller):
    """Every dismissal writes into one JSON column on the user row. Digests
    for pairs that no longer exist are dropped as they are written, so the
    ledger tracks the account rather than growing for the life of it."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    client.post("/api/ebay/duplicates/dismiss", json={})
    assert len(dbmod.duplicate_dismissals(uid)) == 1

    # The pair is resolved -- one ended -- and a different pair turns up.
    assert dbmod.delete_listing("sess-a", user_id=uid)
    _list(dbmod, uid, "sess-x", "158200000001", title="Tammy Antezana Pour Set")
    _list(dbmod, uid, "ebay-158200000002", "158200000002",
          title="Tammy Antezana Pour Set")
    client.post("/api/ebay/duplicates/dismiss", json={})

    ledger = dbmod.duplicate_dismissals(uid)
    assert len(ledger) == 1
    assert list(ledger) == [_groups_all(dbmod, uid)[0]["fingerprint"]]


def _groups_all(dbmod, uid):
    return duplicates.find(dbmod.list_listings(
        limit=500, user_id=uid, statuses=duplicates.LIVE_STATUSES))


def test_a_write_that_did_not_land_is_not_reported_as_a_dismissal(seller,
                                                                  monkeypatch):
    """A "don't remind me again" that quietly did not persist is worse than
    not offering one: the seller learns it is broken only when the card they
    dismissed is back tomorrow."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)

    def boom(*_a, **_kw):
        raise errors.StorageUnavailable("nope")

    monkeypatch.setattr(dbmod, "save_prefs", boom)
    res = client.post("/api/ebay/duplicates/dismiss", json={})
    assert res.status_code == 503
    assert _groups(client)["total"] == 1


def test_a_read_that_failed_does_not_erase_what_was_dismissed(seller,
                                                              monkeypatch):
    """The read before the write is strict on purpose: `{}` out of a broken
    read would not hide a card, it would wipe the ledger."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)
    client.post("/api/ebay/duplicates/dismiss", json={})
    _list(dbmod, uid, "sess-x", "158200000001", title="Tammy Antezana Pour Set")
    _list(dbmod, uid, "ebay-158200000002", "158200000002",
          title="Tammy Antezana Pour Set")

    working = dbmod.get_prefs

    def boom(*_a, **_kw):
        raise errors.StorageUnavailable("nope")

    monkeypatch.setattr(dbmod, "get_prefs", boom)
    assert client.post("/api/ebay/duplicates/dismiss",
                       json={}).status_code == 503
    # Read the ledger back with the storage working again: the question is
    # what the failed write left behind, not what a second failure reports.
    monkeypatch.setattr(dbmod, "get_prefs", working)
    # Nothing was written, so the first dismissal is still in force.
    assert [g["title"] for g in _groups(client)["groups"]] \
        == ["Tammy Antezana Pour Set"]


def test_a_scan_that_cannot_read_the_store_hides_nothing(seller, monkeypatch):
    """The advisory read is best-effort, and it must fail towards SHOWING the
    seller their duplicates rather than towards swallowing them."""
    client, dbmod, uid = seller
    _pair(dbmod, uid)

    def boom(*_a, **_kw):
        raise errors.StorageUnavailable("nope")

    monkeypatch.setattr(dbmod, "get_prefs_best_effort", lambda *_a, **_k: {})
    monkeypatch.setattr(dbmod, "list_listings", boom)
    assert _groups(client) == {"groups": [], "total": 0, "dismissed": 0,
                               "listings": 0}

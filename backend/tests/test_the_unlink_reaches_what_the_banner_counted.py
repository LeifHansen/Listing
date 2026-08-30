"""The banner counts in SQL. The button that answers it read a page.

Settings shows "N listings here are linked to an eBay account that isn't the
one connected" and offers to unlink them. The number comes from
`db.count_foreign_listings` -- a `COUNT(*)` over the whole store, no cap. The
button calls `/api/ebay/release-foreign-listings`, which read
`db.list_listings(limit=LIST_CAP)` and filtered it in Python.

So the two halves of one screen disagree on a store bigger than the page. The
list is ordered newest-first and a record from a previous eBay account is, by
definition, old: the seller is told twelve listings are linked to the old
account, presses the button, and is told seven were unlinked. Nothing explains
the other five, and pressing it again finds the same seven.

Two changes, and both are about saying true things:

  * the candidates are selected in SQL -- foreign records, and unowned ones
    when the seller explicitly asks -- so the button reaches the same records
    the banner counted. The SQL is deliberately a SUPERSET of
    `ebay_account.releasable`, which stays the one place the decision is made;

  * the pass is still bounded, because each release is a write and a request
    that walks an entire store outlives the gateway. It is bounded EXPLICITLY
    now and the remainder is reported, the way the bulk reprice reports
    `deferred` -- a run that did part of the job says so instead of looking
    like one that finished.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit

CONNECTED = "current-seller"


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "switch@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("switch@example.com")["id"]
    monkeypatch.setattr(dbmod, "get_ebay_account",
                        lambda u: {"ebay_username": CONNECTED,
                                   "refresh_token": "t"})
    return client, dbmod, uid


def _put(dbmod, uid: str, lid: str, **data) -> None:
    assert dbmod.upsert_listing(lid, {"title": lid, **data},
                                status="published", user_id=uid)


def test_the_candidates_are_the_ones_the_banner_counts(seller):
    _client, dbmod, uid = seller
    _put(dbmod, uid, "old-1", ebay_account="previous-seller",
         ebay_listing_id="1")
    _put(dbmod, uid, "old-2", ebay_account="previous-seller",
         ebay_listing_id="2")
    _put(dbmod, uid, "mine", ebay_account=CONNECTED, ebay_listing_id="3")
    _put(dbmod, uid, "local-draft")

    assert dbmod.count_foreign_listings(uid, CONNECTED) == 2
    got = dbmod.list_releasable_listings(uid, CONNECTED)
    assert sorted(r["id"] for r in got) == ["old-1", "old-2"], (
        "the button has to reach exactly what the banner counted")


def test_a_record_with_no_owner_only_comes_back_when_asked_for(seller):
    """The seller's explicit call, because only they know whether the store
    behind an unlabelled record is still the one connected."""
    _client, dbmod, uid = seller
    _put(dbmod, uid, "legacy", ebay_listing_id="9")

    assert dbmod.list_releasable_listings(uid, CONNECTED) == []
    assert [r["id"] for r in
            dbmod.list_releasable_listings(uid, CONNECTED, include_unowned=True)
            ] == ["legacy"]


def test_it_is_a_superset_of_the_predicate_that_decides(seller):
    """The SQL narrows the read; `releasable` still makes the call. A local
    draft with no eBay identity is selected by the widened query and refused
    by the predicate -- which is the division of labour, asserted."""
    from backend.services import ebay_account

    _client, dbmod, uid = seller
    _put(dbmod, uid, "local-draft")
    rows = dbmod.list_releasable_listings(uid, CONNECTED, include_unowned=True)
    assert [r["id"] for r in rows] == ["local-draft"]
    assert not ebay_account.releasable(rows[0]["listing"], CONNECTED,
                                       include_unowned=True)


def test_a_read_we_could_not_do_is_not_an_empty_selection(seller, monkeypatch):
    """Zero to release, reported as a completed unlink, leaves the banner up
    and the seller with no idea why."""
    _client, dbmod, uid = seller

    class Broken:
        def __enter__(self):
            raise RuntimeError("connection reset")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dbmod, "Session", lambda *a, **k: Broken())
    with pytest.raises(errors.StorageUnavailable):
        dbmod.list_releasable_listings(uid, CONNECTED)


def test_the_route_reaches_a_record_older_than_any_page(seller, monkeypatch):
    """The bug, end to end: the foreign record is the OLDEST one, so a
    newest-first page of two never sees it."""
    client, dbmod, uid = seller
    _put(dbmod, uid, "old", ebay_account="previous-seller", ebay_listing_id="1")
    for i in range(5):
        _put(dbmod, uid, f"new-{i}", ebay_account=CONNECTED,
             ebay_listing_id=f"9{i}")
    monkeypatch.setattr(main, "LIST_CAP", 2, raising=False)

    body = client.post("/api/ebay/release-foreign-listings", json={}).json()
    assert body["released"] == 1, "the unlink missed what the banner counted"
    assert dbmod.count_foreign_listings(uid, CONNECTED) == 0


def test_a_run_that_did_part_of_the_job_says_so(seller, monkeypatch):
    """Each release is a write, so the pass stays bounded -- but a bounded run
    that looks like a finished one is the thing this branch keeps removing."""
    client, dbmod, uid = seller
    for i in range(4):
        _put(dbmod, uid, f"old-{i}", ebay_account="previous-seller",
             ebay_listing_id=str(i))
    monkeypatch.setattr(main, "RELEASE_CAP", 3, raising=False)

    body = client.post("/api/ebay/release-foreign-listings", json={}).json()
    assert body["released"] == 3
    assert body["remaining"] == 1, "a partial unlink reported itself complete"

    again = client.post("/api/ebay/release-foreign-listings", json={}).json()
    assert (again["released"], again["remaining"]) == (1, 0)


def test_the_route_does_not_read_the_whole_store(seller, monkeypatch):
    client, dbmod, uid = seller
    _put(dbmod, uid, "old", ebay_account="previous-seller", ebay_listing_id="1")

    def _no(*a, **k):
        raise AssertionError("release-foreign-listings read the whole store")

    monkeypatch.setattr(dbmod, "list_listings", _no)
    monkeypatch.setattr(dbmod, "list_listings_best_effort", _no)
    assert client.post("/api/ebay/release-foreign-listings",
                       json={}).json()["released"] == 1

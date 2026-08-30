"""The delete dialog's numbers are counted, not measured off a page.

`/api/account/summary` answers the most consequential dialog in the app: how
much is about to be erased, and how many of those listings STAY LIVE on eBay
afterwards, still taking orders the seller will no longer be able to see. Both
numbers came from `db.list_listings(limit=LIST_CAP)` and `len()`.

That has two costs, and the comment above it named the first honestly:

  "a lower cap here would quietly under-report the very seller this dialog
   exists to warn -- someone with more listings than the cap is told they are
   about to delete fewer than they have, live ones included."

Using the largest cap in the app does not fix that, it moves it: a seller past
that cap is still told a smaller number than the truth, and nothing in the
answer says it is a floor. The second cost is what it takes to produce two
integers -- every listing's whole JSON blob, parsed in Python, fetched over a
cross-region link, for a dialog that shows two numbers.

Both go away with the thing a database is for. `count_foreign_listings`
already counts in SQL and says why in its own comment; this is the same move
on the page where being wrong matters most.

The raising half is not incidental. A count that could not be read must never
come back as zero: zero live listings is exactly what suppresses the "these
stay up on eBay" warning, which is the reason `counted` exists at all.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit


@pytest.fixture()
def store(dbmod, monkeypatch):
    """A seller whose store is bigger than any page this app hands out."""
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    r = client.post("/api/auth/signup",
                    json={"email": "counts@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    uid = dbmod.get_user_by_email("counts@example.com")["id"]
    for i in range(7):
        assert dbmod.upsert_listing(f"draft-{i}", {"title": f"d{i}"},
                                    status="draft", user_id=uid)
    for i in range(4):
        assert dbmod.upsert_listing(f"live-{i}", {"title": f"l{i}"},
                                    status="published", user_id=uid)
    return client, dbmod, uid


def test_the_count_is_not_bounded_by_how_many_rows_a_page_shows(store):
    _client, dbmod, uid = store
    # A page shorter than the store, which is the state every seller past the
    # list cap is permanently in.
    assert len(dbmod.list_listings(limit=3, user_id=uid)) == 3

    assert dbmod.count_listings(uid) == 11
    assert dbmod.count_listings(uid, statuses=("published", "live")) == 4


def test_a_count_is_scoped_to_its_owner(store):
    """The same rule every other read here follows, asserted rather than
    assumed: a count is a number about ONE seller's store."""
    _client, dbmod, uid = store
    other = dbmod.create_user("other-id", "someone-else@example.com", "x" * 60)
    assert isinstance(other, dict), "fixture assumption: a second account exists"
    assert dbmod.upsert_listing("theirs", {"title": "t"}, status="published",
                                user_id=other["id"])
    assert dbmod.count_listings(uid) == 11
    assert dbmod.count_listings(other["id"]) == 1


def test_a_count_we_could_not_read_is_not_zero(store, monkeypatch):
    """Zero live listings is the answer that suppresses the eBay warning."""
    _client, dbmod, uid = store

    class Broken:
        def __enter__(self):
            raise RuntimeError("connection reset")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dbmod, "Session", lambda *a, **k: Broken())
    with pytest.raises(errors.StorageUnavailable):
        dbmod.count_listings(uid)


def test_the_summary_endpoint_reports_the_whole_store(store, monkeypatch):
    """End to end, on a store deliberately larger than the page size."""
    client, _dbmod, _uid = store
    monkeypatch.setattr(main, "LIST_CAP", 2, raising=False)
    body = client.get("/api/account/summary").json()
    assert body["counted"] is True
    assert body["listings"] == 11, "the dialog under-reported what it will erase"
    assert body["live_listings"] == 4, "the dialog under-reported what stays live"


def test_a_summary_that_cannot_count_says_so_rather_than_zero(store, monkeypatch):
    client, dbmod, _uid = store

    def _break(*a, **k):
        raise errors.StorageUnavailable("nope")

    monkeypatch.setattr(dbmod, "count_listings", _break)
    body = client.get("/api/account/summary").json()
    assert body["counted"] is False
    assert body["live_listings"] == 0, "the shape stays; the flag carries the doubt"
    assert body["listings"] == 0


def test_the_summary_does_not_read_the_whole_store_to_count_it(store, monkeypatch):
    """The other half of the change, pinned so it cannot quietly come back.

    A statement count would be the stronger check but needs a real engine
    hook; what this asserts is the thing that actually cost the round trips --
    that answering this endpoint no longer fetches listing rows at all.
    """
    client, dbmod, _uid = store

    def _no(*a, **k):
        raise AssertionError("/api/account/summary fetched listing rows to "
                             "produce two integers")

    monkeypatch.setattr(dbmod, "list_listings", _no)
    monkeypatch.setattr(dbmod, "list_listings_best_effort", _no)
    assert client.get("/api/account/summary").json()["counted"] is True

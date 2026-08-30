"""A seller past the list cap can reach the rest of their store.

`/api/listings` returns the newest `LIST_CAP` records and says so. The saying
so is honest and was the whole of `242b914`; what it does not do is give the
seller any way to see the others. The listings page's search filters the
records already loaded, so for a seller with 4,000 listings **1,000 of them do
not exist in this app**: not on the page, not in the tabs, not findable, not
openable.

This is P1-10's cursor pagination, in the form the audit asks for and no
larger. Keyset, not OFFSET: the list is ordered `updated_at DESC, id DESC`,
and a save between two page loads shifts every row an OFFSET would count past
-- the seller would skip a listing and see another twice, which on a screen
whose checkboxes drive a bulk reprice is worse than not paging at all. A
cursor names the last row of the previous page, so what comes back is what
follows THAT ROW, whatever has moved.

The cursor is the server's own words handed back: it is validated, scoped to
the caller's own store by the same `user_id` filter as every other read, and a
malformed one is refused rather than silently ignored -- an ignored cursor
returns page one again, which reads to the client as "no more listings" and
hides the rest of the store just as effectively as having no cursor at all.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "pages@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("pages@example.com")["id"]
    for i in range(7):
        assert dbmod.upsert_listing(f"l{i}", {"title": f"t{i}"},
                                    status="draft", user_id=uid)
    return client, dbmod, uid


def _page(client, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get(f"/api/listings?{q}")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_whole_store_can_be_walked_three_rows_at_a_time(seller):
    client, _dbmod, _uid = seller
    seen: list[str] = []
    body = _page(client, limit=3)
    guard = 0
    while True:
        seen.extend(i["id"] for i in body["listings"])
        if not body.get("next_cursor"):
            break
        guard += 1
        assert guard < 10, "the walk did not terminate"
        body = _page(client, limit=3, before=body["next_cursor"])

    assert len(seen) == len(set(seen)), "a listing came back on two pages"
    assert sorted(seen) == [f"l{i}" for i in range(7)], (
        "the walk did not reach the seller's whole store")


def test_the_last_page_offers_no_cursor(seller):
    """The signal the client stops on. A cursor here would loop for ever."""
    client, _dbmod, _uid = seller
    body = _page(client, limit=50)
    assert body["truncated"] is False
    assert body.get("next_cursor") is None


def test_a_row_added_between_pages_does_not_shift_the_next_one(seller):
    """Why keyset and not OFFSET. The new listing sorts to the top, which an
    OFFSET would count past -- pushing one row off page two entirely."""
    client, dbmod, uid = seller
    first = _page(client, limit=3)
    assert dbmod.upsert_listing("brand-new", {"title": "new"},
                                status="draft", user_id=uid)

    second = _page(client, limit=3, before=first["next_cursor"])
    ids = [i["id"] for i in second["listings"]]
    assert "brand-new" not in ids, "a row inserted above leaked into page two"
    assert not set(ids) & {i["id"] for i in first["listings"]}, (
        "page two repeated a row from page one")


def test_a_cursor_cannot_read_another_sellers_store(seller):
    """Same ownership rule as every other read: the cursor says WHERE to
    start, never WHOSE store to start in."""
    client, dbmod, _uid = seller
    other = dbmod.create_user("other-id", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", {"title": "t"}, status="draft",
                                user_id=other["id"])
    first = _page(client, limit=3)
    seen: set[str] = set()
    body = first
    for _ in range(6):
        seen |= {i["id"] for i in body["listings"]}
        if not body.get("next_cursor"):
            break
        body = _page(client, limit=3, before=body["next_cursor"])
    assert "theirs" not in seen


def test_a_malformed_cursor_is_refused_not_ignored(seller):
    """An ignored cursor answers with page one, which the client reads as the
    listings that follow -- so the store looks like it ends where it began."""
    client, _dbmod, _uid = seller
    r = client.get("/api/listings?limit=3&before=not-a-cursor")
    assert r.status_code == 400, r.text
    assert "listing" in r.json()["detail"].lower()


def test_an_empty_cursor_is_simply_the_first_page(seller):
    """Clients send "" for "from the start"; that is not a malformed cursor."""
    client, _dbmod, _uid = seller
    assert _page(client, limit=3, before="")["listings"]

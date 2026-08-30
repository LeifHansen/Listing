"""A bulk action reads the listings it was asked about, not the whole store.

`POST /api/listings/lower-prices` takes the ids the seller ticked -- at most
twenty get repriced in one pass -- and found them by reading the seller's
newest 3,000 records and filtering. Two things follow from that, and the
second is the one that matters:

  * it fetches every one of those records' whole JSON blob, across a
    cross-region link, to keep twenty of them; and

  * anything outside that window is reported back to the seller as
    **"Listing not found."** The list is ordered newest-first, so on a store
    past the cap a ticked listing only has to be edited past by 3,000 others
    between opening the page and pressing the button. It is not missing. It
    was outside the page we happened to read -- which is the same sentence
    this branch has now removed from the listing lookup, the store list, the
    dashboard tiles and the delete dialog.

`db.get_listings(ids, user_id)` asks for the ids. "Not found" then means what
it says: no such row, or not this seller's -- which is a real answer and stays
in the report.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "bulk@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("bulk@example.com")["id"]
    # The route refuses without a connection. Nothing here reaches eBay: every
    # listing it is pointed at is either a draft (skipped before the revise)
    # or absent, so `provider.publish` is never called.
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {"access_token": "t"})
    return client, dbmod, uid


def test_it_asks_for_the_ids_it_was_given(seller):
    _client, dbmod, uid = seller
    for i in range(5):
        assert dbmod.upsert_listing(f"mine-{i}", {"title": f"t{i}"},
                                    status="published", user_id=uid)

    got = dbmod.get_listings(["mine-3", "mine-1"], uid)
    assert sorted(r["id"] for r in got) == ["mine-1", "mine-3"]


def test_it_does_not_hand_over_another_sellers_listing(seller):
    """The same ownership rule the single-listing routes enforce, in the read
    itself -- so a caller cannot forget it."""
    _client, dbmod, uid = seller
    other = dbmod.create_user("other-id", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", {"title": "t"}, status="published",
                                user_id=other["id"])
    assert dbmod.get_listings(["theirs"], uid) == []
    assert [r["id"] for r in dbmod.get_listings(["theirs"], other["id"])] == ["theirs"]


def test_an_empty_ask_asks_nothing(seller):
    _client, dbmod, uid = seller
    assert dbmod.get_listings([], uid) == []


def test_a_lookup_we_could_not_do_is_not_an_empty_selection(seller, monkeypatch):
    """An unreadable store must not report every ticked listing as missing --
    which, in this route, reads back as 'Listing not found.' twenty times."""
    _client, dbmod, uid = seller

    class Broken:
        def __enter__(self):
            raise RuntimeError("connection reset")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dbmod, "Session", lambda *a, **k: Broken())
    with pytest.raises(errors.StorageUnavailable):
        dbmod.get_listings(["mine-0"], uid)


def test_the_route_does_not_read_the_whole_store_to_find_twenty_rows(
        seller, monkeypatch):
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("mine-0", {"title": "t", "price": 40.0},
                                status="draft", user_id=uid)

    def _no(*a, **k):
        raise AssertionError("lower-prices read the whole store to find the "
                             "handful of listings it was handed")

    monkeypatch.setattr(dbmod, "list_listings", _no)
    monkeypatch.setattr(dbmod, "list_listings_best_effort", _no)
    r = client.post("/api/ebay/lower-prices",
                    json={"percent": 10, "listing_ids": ["mine-0"]})
    assert r.status_code == 200, r.text
    # Not live, so it is skipped -- which is the honest answer and proves the
    # record was found and read rather than reported missing.
    assert r.json()["results"]["skipped"][0]["message"].startswith(
        "No longer live")


def test_the_ask_itself_is_bounded(seller):
    """The read is by id now, so the SIZE of the ask reaches the database.

    Under the old code a huge `listing_ids` cost nothing extra -- the store
    read was capped and the list was only a filter. Asking for the ids means
    an unbounded body becomes an unbounded `IN (...)`, which is a way to make
    one request expensive for everybody. At most BULK_PRICE_CAP are repriced
    in a pass anyway, so nothing useful is lost by refusing to look up more
    than a pass could act on.
    """
    client, _dbmod, _uid = seller
    many = [f"id-{i}" for i in range(5000)]
    r = client.post("/api/ebay/lower-prices",
                    json={"percent": 10, "listing_ids": many})
    assert r.status_code == 400, r.text
    assert "too many" in r.json()["detail"].lower()


def test_a_selection_a_pass_could_act_on_is_still_accepted(seller):
    """The bound has to sit above what the screen can actually select, or it
    refuses ordinary work."""
    client, _dbmod, _uid = seller
    ids = [f"id-{i}" for i in range(main.BULK_PRICE_CAP)]
    r = client.post("/api/ebay/lower-prices",
                    json={"percent": 10, "listing_ids": ids})
    assert r.status_code == 200, r.text


def test_the_read_itself_refuses_an_unbounded_ask(seller):
    """The backstop under the route's own cap.

    A future route could call this with whatever a client sent, which is how
    the unbounded `IN (...)` gets back in. It REFUSES rather than truncating:
    `mark_notifications_read` can silently cap because a seller who wanted the
    other twenty taps again, but a truncated lookup here comes back to them as
    "Listing not found." per listing -- a read limitation reported as absence,
    which is the exact sentence this branch keeps removing. A caller that
    forgets to bound its input should fail loudly in a test, not quietly in
    production.
    """
    _client, dbmod, uid = seller
    with pytest.raises(ValueError):
        dbmod.get_listings([f"id-{i}" for i in range(10_000)], uid)


def test_an_id_that_is_not_the_sellers_is_still_reported_missing(
        seller, monkeypatch):
    """The real 'not found' survives: this is the answer the route is entitled
    to give, and narrowing the read must not lose it."""
    client, dbmod, uid = seller
    r = client.post("/api/ebay/lower-prices",
                    json={"percent": 10, "listing_ids": ["never-existed"]})
    assert r.status_code == 200, r.text
    skipped = r.json()["results"]["skipped"]
    assert [s["listing_id"] for s in skipped] == ["never-existed"]
    assert skipped[0]["message"] == "Listing not found."

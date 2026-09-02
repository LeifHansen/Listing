""""Listing not found" is a claim about the seller's account.

The ownership guard already knows this. `_assert_session_owner` reads
`db.get_listing_strict` and spells the reasoning out: the check answers from
the database, "so if a read failure were treated like 'no such listing', one
Neon blip would quietly disable the guard on every session-scoped endpoint at
once". It refuses with a 503.

Ten route handlers a few hundred lines below do the same lookup for a
different purpose through `db.get_listing`, which collapses "no such listing"
and "the read could not be performed" into `None` — and then:

    rec = db.get_listing(listing_id)
    if not rec:
        raise HTTPException(404, "Listing not found")

So during an outage the promote, relist, end, merge, delete, prepare and
label routes all told the seller their listing did not exist. It is the
`/media` bug one layer up — a photo we could not sign for answering 404 for
an object that is there — and the client acts on 404 by treating the thing as
gone.

The reasoning was written once and applied to one caller. This checks the
rest.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit

LID = "sess-1"


@pytest.fixture()
def seller(dbmod):
    assert dbmod.enabled()
    ratelimit.reset()
    client = TestClient(main.app, raise_server_exceptions=False)
    r = client.post("/api/auth/signup",
                    json={"email": "gone@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    return client


@pytest.fixture()
def unreadable(monkeypatch):
    """The store answers "could not do that", however it is asked.

    The delete route is in here too and does not read first -- it calls
    `db.delete_listing`, whose `False` the route turns into the same 404. An
    unreachable database saying "not found" while somebody is deleting is the
    same claim from the other direction.
    """
    def gone(*_a, **_k):
        raise errors.StorageUnavailable(
            "We couldn’t reach your listings just now. Try again in a moment.")

    # Only the read underneath: db.get_listing is the real thing, so what is
    # being checked is what it does with an unreadable store rather than what
    # a double says it does.
    monkeypatch.setattr(main.db, "get_listing_strict",
                        lambda _id: main.db.UNAVAILABLE)
    monkeypatch.setattr(main.db, "delete_listing", gone)


def _listing_routes():
    """Every GET/POST/DELETE that names a listing in its path."""
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        if path.startswith("/api/admin/"):
            # The console's cross-user read. Its 404 to THIS sweep's caller
            # (an ordinary seller) is the access gate speaking — deliberately
            # indistinguishable from the route not existing — not a claim
            # about the listing. The claim this sweep polices is pinned for
            # the caller who can actually reach the route, in
            # test_admin_requires_a_superadmin.py: a superadmin over an
            # unreadable store gets 503, never "not found".
            continue
        if "{listing_id}" not in path and "{session_id}" not in path:
            continue
        for method in sorted((getattr(route, "methods", None) or set())
                             - {"HEAD", "OPTIONS"}):
            yield method, path


def test_the_sweep_reaches_the_routes_it_is_about():
    found = list(_listing_routes())
    assert len(found) >= 8, f"only found {len(found)} listing-scoped routes"


def test_no_listing_route_says_not_found_when_it_could_not_look(seller, unreadable):
    said_missing = []
    for method, path in _listing_routes():
        url = path.replace("{listing_id}", LID).replace("{session_id}", LID)
        if "{" in url:
            continue
        res = seller.request(method, url, json={})
        if res.status_code == 404:
            said_missing.append(f"{method} {path} -> 404 {res.text[:80]}")
    assert not said_missing, (
        "these told the seller their listing does not exist, on a read that "
        "never ran:\n  " + "\n  ".join(said_missing))


def test_a_listing_that_really_is_missing_still_answers_404(seller, monkeypatch):
    """The other half. A 503 for every unknown id would be its own lie, and
    would hide a genuinely bad link behind "try again in a moment"."""
    monkeypatch.setattr(main.db, "get_listing_strict", lambda _id: None)
    monkeypatch.setattr(main.db, "get_listing", lambda _id: None)

    res = seller.get(f"/api/listings/{LID}")
    assert res.status_code == 404


def test_the_media_route_already_learned_this():
    """Named so the pattern is visible: this is the same fix `/media` got when
    a presign failure was answering 404 for an object that exists."""
    import inspect
    src = inspect.getsource(main)
    assert "get_listing_strict" in src


def test_deleting_when_the_write_cannot_run_is_not_not_found(seller, unreadable):
    """Called out on its own because this route never reads first.

    `db.delete_listing` answered False for "no such row", "not yours" AND
    "the write failed", and the route turns False into 404 — so a seller
    deleting a listing during an outage was told it did not exist. It does;
    they can see it on the screen behind the dialog.
    """
    res = seller.delete(f"/api/listings/{LID}")
    assert res.status_code != 404, res.text
    assert res.status_code == 503, res.text


def test_deleting_something_that_really_is_not_there_still_404s(seller):
    res = seller.delete("/api/listings/never-existed")
    assert res.status_code == 404

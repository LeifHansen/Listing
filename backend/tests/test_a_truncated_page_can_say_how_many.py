"""A page that was cut can now say what it was cut from.

`242b914` made `/api/listings` admit when it is not the whole store, and
deliberately named no total:

  "A probe row rather than a COUNT(*) because this is the busiest route in
   the app, and because the question the seller has is 'is this all of them?'
   -- `truncated` answers that honestly, where a total this endpoint does not
   have would have to be invented."

That was the right call with no counter to hand. There is one now
(`db.count_listings`, added for the delete dialog), and the trade can be had
both ways: the probe row still decides, for free, on every load -- and the
COUNT only runs for the seller who is actually past the cap, who is rare and
for whom "3,000 of 4,812" is a materially better answer than "there are more".
Nobody under the cap pays anything.

A total that cannot be taken is left out rather than guessed. The page is
already honest without it, so a failed count costs the sentence a number, not
the seller a truth -- which is why this one read is allowed to be tolerant.
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
                       json={"email": "page@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("page@example.com")["id"]
    for i in range(7):
        assert dbmod.upsert_listing(f"l{i}", {"title": f"t{i}"},
                                    status="draft", user_id=uid)
    return client, dbmod, uid


def test_a_page_that_fits_pays_for_no_count(seller, monkeypatch):
    """The busiest route in the app. Nobody under the cap pays a COUNT(*)."""
    client, dbmod, _uid = seller

    def _no(*a, **k):
        raise AssertionError("counted the store on a page that was not cut")

    monkeypatch.setattr(dbmod, "count_listings", _no)
    body = client.get("/api/listings?limit=50").json()
    assert body["truncated"] is False
    assert body.get("total") is None, "a complete page needs no total"


def test_a_cut_page_says_what_it_was_cut_from(seller):
    client, _dbmod, _uid = seller
    body = client.get("/api/listings?limit=3").json()
    assert body["truncated"] is True
    assert len(body["listings"]) == 3
    assert body["total"] == 7


def test_a_total_we_could_not_take_is_left_out_not_guessed(seller, monkeypatch):
    """The page is honest without it. A count that failed costs the sentence a
    number -- it must never cost it a number that is wrong."""
    client, dbmod, _uid = seller

    def _break(*a, **k):
        raise errors.StorageUnavailable("nope")

    monkeypatch.setattr(dbmod, "count_listings", _break)
    body = client.get("/api/listings?limit=3").json()
    assert body["truncated"] is True, "the honest part still has to arrive"
    assert body.get("total") is None
    assert len(body["listings"]) == 3

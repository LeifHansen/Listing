""""Here are your listings" is a claim about all of them.

`/api/listings` clamps to LISTING_LIST_CAP (3000) and returns whatever fits,
with nothing saying a cut was made. A seller past the cap gets a page that
looks exactly like a complete store, and the whole screen is built on it: the
counts, the tabs, the dashboard groups, the duplicate advisory, and the
checkboxes a bulk reprice runs over. None of them can tell they are working
from a partial view, and neither can the seller.

That is the same finding this branch already fixed twice on the eBay side --
the awaiting-shipment list that showed 50 of 80 orders, and the sampled status
sweep that reported `checked` without saying out of how many. The rule that
came out of those is the rule here: an answer that could not show everything
has to say so, and must never invent the part it could not see.

Cheap on purpose. It asks for one row more than it will return and reports
whether that row existed -- one extra row on the hottest route in the app,
rather than a COUNT(*) per page load. That answers the question the seller
actually has ("is this all of them?"); it deliberately does not claim a total,
because it does not have one.
"""
from __future__ import annotations

import pytest

# backend.main pulls in the AI and image stacks at import, so this skips in
# the light `checks` job and runs in `smoke` — which is why the file is in
# gates.yml's API-tests list, where a skip fails the job.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from backend import main

    monkeypatch.setattr(main.db, "db_status", lambda: {"connected": True})

    def _serve(rows, user={"id": "u1"}):
        asked = {}

        def _list(limit=50, user_id=None, statuses=None, before=None):
            # `before` is recorded, not honoured: these tests are about the
            # probe row and the cap, and the walk itself is covered against a
            # real database in test_the_older_listings_can_be_reached.
            asked["limit"] = limit
            asked["before"] = before
            return rows[:limit]

        monkeypatch.setattr(main.auth, "current_user", lambda _r: user)
        monkeypatch.setattr(main.db, "list_listings", _list)
        return TestClient(main.app), asked
    return _serve


def _rows(n: int) -> list[dict]:
    return [{"id": f"s{i}", "status": "draft", "listing": {"title": f"#{i}"}}
            for i in range(n)]


def test_a_complete_store_says_it_is_complete(client):
    api, _ = client(_rows(3))
    body = api.get("/api/listings?limit=10").json()

    assert len(body["listings"]) == 3
    assert body["truncated"] is False


def test_a_store_past_the_cap_says_it_was_cut(client):
    """The finding. This used to be indistinguishable from a complete store."""
    api, _ = client(_rows(50))
    body = api.get("/api/listings?limit=10").json()

    assert len(body["listings"]) == 10, "the page still honours the limit"
    assert body["truncated"] is True


def test_the_probe_row_is_never_returned(client):
    """Exactly at the limit is not truncated, and the extra row asked for must
    not leak into the page -- an off-by-one here shows a listing the client
    did not ask for and reports a complete store as cut."""
    api, _ = client(_rows(10))
    body = api.get("/api/listings?limit=10").json()

    assert len(body["listings"]) == 10
    assert body["truncated"] is False


def test_it_asks_for_exactly_one_row_more(client):
    """One row, not a second query and not a COUNT(*). This runs on every page
    load of the busiest screen in the app."""
    api, asked = client(_rows(3))
    api.get("/api/listings?limit=10")

    assert asked["limit"] == 11


def test_the_cap_still_bounds_a_greedy_caller(client):
    from backend import main

    api, asked = client(_rows(main.LIST_CAP + 100))
    body = api.get(f"/api/listings?limit={main.LIST_CAP * 10}").json()

    assert len(body["listings"]) == main.LIST_CAP
    assert asked["limit"] == main.LIST_CAP + 1
    assert body["truncated"] is True


def test_a_logged_out_caller_is_not_told_their_store_was_cut(client):
    """No user, no store read, nothing to have been cut. `truncated: true`
    here would be a claim about a store nobody looked at."""
    api, _ = client(_rows(50), user=None)
    body = api.get("/api/listings").json()

    assert body["listings"] == []
    assert body["truncated"] is False

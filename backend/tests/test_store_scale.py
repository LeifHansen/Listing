"""Two endpoints that have to stay honest on a store bigger than a handful.

A mirrored eBay store is thousands of records, and both of these read the
whole list. On a small test store any cap and any cooldown look the same as
none at all, which is exactly why neither of these regressions was visible.

  * /api/account/summary counts what deleting the account would destroy. It
    read a lower cap than every other consumer of the list, so a seller with
    more listings than that cap was told they were about to lose fewer than
    they have — live ones included, which stay up on eBay afterwards and are
    the thing the dialog exists to warn about. Raising it to the shared cap
    only moved the boundary; the numbers are counted in SQL now, so the store
    below is deliberately LARGER than any page this app hands out — a size no
    cap can answer correctly.

  * /api/ebay/sync-listings rations eBay's per-day Trading quota through a
    cooldown. It asked for the cooldown before knowing whether it had any
    per-item work, so a sync with nothing to sweep still started the whole
    six-hour window and the next sync — the one with real work — was refused.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.services import sync_guard  # noqa: E402


def _rows(n: int, live_every: int = 2, user_id: str = "u1") -> list[dict]:
    """`n` listing records, every `live_every`-th one live on eBay."""
    out = []
    for i in range(n):
        live = i % live_every == 0
        out.append({
            "id": f"s{i:05d}",
            "user_id": user_id,
            "status": "published" if live else "draft",
            "listing": {"title": f"Item {i}",
                        "ebay_listing_id": f"1{i:011d}" if live else ""},
        })
    return out


# --- /api/account/summary ---------------------------------------------------

@pytest.fixture()
def summary_client(monkeypatch):
    """A signed-in user whose store is bigger than the app's largest page.

    `LIST_CAP + 500`, not `LIST_CAP - 1`: the endpoint counts in SQL now, so
    the interesting store is one no page can hold. The doubles below are the
    counters for the same reason -- and `list_listings` is left deliberately
    un-doubled, so a future rewrite that goes back to measuring a page fails
    here rather than passing with a short answer.
    """
    store = _rows(main.LIST_CAP + 500)

    monkeypatch.setattr(main.auth, "current_user",
                        lambda request: {"id": "u1", "email": "a@b.co"})
    monkeypatch.setattr(main.db, "db_status", lambda: {"configured": True,
                                                       "connected": True})
    monkeypatch.setattr(main.db, "get_ebay_account", lambda uid: {"refresh_token": "t"})

    def _count(user_id, statuses=None):
        return sum(1 for r in store if r["user_id"] == user_id
                   and (statuses is None or r["status"] in statuses))

    monkeypatch.setattr(main.db, "count_listings", _count)
    c = TestClient(main.app)
    c.store = store
    return c


def test_summary_counts_the_whole_store(summary_client):
    """No page bounds this. A summary that stops at one under-reports
    precisely the sellers with the most to lose."""
    assert len(summary_client.store) > main.LIST_CAP, (
        "the point of this store is that no page in the app can hold it")
    body = summary_client.get("/api/account/summary").json()
    assert body["counted"] is True
    assert body["listings"] == len(summary_client.store)
    assert body["live_listings"] == sum(
        1 for r in summary_client.store if r["status"] == "published")


def test_summary_reports_unknown_rather_than_zero_when_the_db_is_down(
        summary_client, monkeypatch):
    """A silent 0 would suppress the warning. `counted` is how the dialog
    tells "nothing to lose" apart from "we couldn't ask"."""
    monkeypatch.setattr(main.db, "db_status", lambda: {"configured": True,
                                                       "connected": False})
    body = summary_client.get("/api/account/summary").json()
    assert body["counted"] is False and body["listings"] == 0


def test_summary_needs_a_login(monkeypatch):
    monkeypatch.setattr(main.auth, "current_user", lambda request: None)
    assert TestClient(main.app).get("/api/account/summary").status_code == 401


# --- /api/ebay/sync-listings ------------------------------------------------

@pytest.fixture()
def sync_client(monkeypatch):
    """A connected seller whose live listings were all settled by the cheap
    finished-list pass, leaving the per-item sweeps nothing to do."""
    sync_guard.reset()
    store = _rows(6, live_every=1)
    handled = {r["id"] for r in store}

    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": "u1"})
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "tok",
                                         "ebay_username": "seller"})
    # Honours `statuses` because the real read does: the sweep asks for live
    # listings, and every record here is one.
    monkeypatch.setattr(
        main.db, "list_listings",
        lambda limit=50, user_id=None, statuses=None: [
            r for r in store
            if statuses is None or r["status"] in statuses][:limit])
    # The cheap pass claims everything, so nothing reaches the sweeps.
    monkeypatch.setattr(main.listing_sync, "reconcile_recent",
                        lambda token, uid, records, account="": (0, set(handled)))
    # Anything that would spend an eBay call is a test failure, not a stub.
    def _never(*a, **k):
        raise AssertionError("a sweep ran when there was nothing to sweep")

    monkeypatch.setattr(main.listing_sync, "refresh_statuses", _never)
    yield TestClient(main.app)
    sync_guard.reset()


def test_a_sync_with_nothing_to_sweep_leaves_the_cooldown_alone(sync_client):
    """sweep_due() STARTS the cooldown on the call that says yes. Asking
    speculatively therefore spent the whole six-hour window on zero eBay
    calls, and the next sync — the one that did have work — was refused."""
    assert sync_client.post("/api/ebay/sync-listings", json={}).status_code == 200
    # Untouched: a later caller with real work still gets its turn.
    assert sync_guard.sweep_due("u1") is True


def test_a_sync_that_does_sweep_still_starts_the_cooldown(sync_client,
                                                          monkeypatch):
    """The other half of the same rule — the rationing must still happen for
    a run that actually spends the quota."""
    swept = []
    monkeypatch.setattr(main.listing_sync, "reconcile_recent",
                        lambda token, uid, records, account="": (0, set()))
    monkeypatch.setattr(main.listing_sync, "refresh_statuses",
                        lambda token, uid, records, account="": swept.append(len(records)) or 0)
    assert sync_client.post("/api/ebay/sync-listings", json={}).status_code == 200
    assert sync_guard.sweep_due("u1") is False, "the cooldown never started"

"""The console's numbers are counts of what exists, not inventions.

Two halves. The aggregates must count seeded data correctly — including the
sold-money rules borrowed from lib/sales.js (sold_price falls back to the
asking price and is COUNTED as approximate; a sale with no parseable date is
excluded from the window rather than guessed into it). And a read that
fails must RAISE rather than answer zeros: "0 users, $0 sold" during an
outage is not a quiet failure, it is a false report about the platform.
"""
from __future__ import annotations

import datetime as _dt

import pytest

pytest.importorskip("sqlalchemy")

from backend.errors import StorageUnavailable


def _iso(days_ago: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def seeded(dbmod):
    db = dbmod
    db.create_user("u1", "a@example.com", "hash")
    db.create_user("u2", "b@example.com", "hash")
    db.create_user("u3", "c@example.com", "hash")
    db.set_user_disabled("u3", True)

    assert db.upsert_listing("l1", {"title": "draft one"}, "draft", "u1")
    assert db.upsert_listing("l2", {"title": "live one", "price": 30.0},
                             "live", "u1")
    assert db.upsert_listing(
        "l3", {"title": "sold dated", "price": 20.0, "sold_price": 25.0,
               "sold_at": _iso(2), "currency": "USD"}, "sold", "u1")
    # No sold_price: the asking price stands in, counted as approximate.
    assert db.upsert_listing(
        "l4", {"title": "sold approx", "price": 40.0,
               "sold_at": _iso(3), "currency": "USD"}, "sold", "u1")
    # No parseable date: excluded from the window, counted as undated.
    assert db.upsert_listing(
        "l5", {"title": "sold undated", "sold_price": 99.0}, "sold", "u2")
    # Sold long before the window: dated, but outside it.
    assert db.upsert_listing(
        "l6", {"title": "sold old", "sold_price": 10.0,
               "sold_at": _iso(400)}, "sold", "u2")

    assert db.token_credit("u1", 50, ref="stripe-1", kind="purchase")
    assert db.token_credit("u2", 10, ref="admin:seed", kind="grant")
    return db


def test_the_kpis_count_the_seeded_platform(seeded):
    kpis = seeded.admin_platform_kpis(30)

    assert kpis["users"]["total"] == 3
    assert kpis["users"]["signups"] == 3
    assert sum(p["count"] for p in kpis["users"]["signup_series"]) == 3
    assert kpis["users"]["active"] == 2          # u1 and u2; u3 did nothing

    assert kpis["listings"]["by_status"] == {"draft": 1, "live": 1, "sold": 4}
    assert kpis["listings"]["total"] == 6

    sales = kpis["sales"]
    assert sales["count"] == 2                   # l3 and l4; l5 undated, l6 old
    assert sales["value"] == pytest.approx(65.0)  # 25 + 40
    assert sales["approx"] == 1                  # l4 used its asking price
    assert sales["undated"] == 1                 # l5
    assert sales["currency"] == "USD"
    assert sales["mixed_currency"] is False

    kinds = kpis["tokens"]["by_kind"]
    assert kinds["purchase"]["tokens"] == 50
    assert kinds["grant"]["tokens"] == 10


def test_the_users_page_walks_without_repeats_or_skips(dbmod):
    db = dbmod
    for i in range(7):
        db.create_user(f"u{i}", f"u{i}@example.com", "hash")

    seen: list[str] = []
    before = None
    while True:
        page = db.admin_list_users(limit=3, before=before)
        if not page:
            break
        seen.extend(u["id"] for u in page)
        last = page[-1]
        before = (_dt.datetime.fromisoformat(last["created_at"]), last["id"])
    assert sorted(seen) == sorted(f"u{i}" for i in range(7))
    assert len(seen) == len(set(seen)), "the cursor repeated a row"


def test_search_narrows_and_never_ships_the_hash(seeded):
    rows = seeded.admin_list_users(q="a@example")
    assert [u["id"] for u in rows] == ["u1"]
    assert "password_hash" not in rows[0]
    assert seeded.admin_list_users(q="u2") != []          # exact id works
    assert seeded.admin_list_users(q="nobody@nowhere") == []


def test_rollups_come_grouped_not_per_row(seeded):
    rollups = seeded.admin_user_rollups(["u1", "u2", "u3"])
    assert rollups["u1"]["listings"] == 4
    assert rollups["u1"]["tokens"]["purchased"] == 50
    assert rollups["u2"]["listings"] == 2
    assert rollups["u3"]["listings"] == 0
    assert rollups["u3"]["tokens"] is None


def test_the_listing_summaries_never_ship_the_blob(seeded):
    rows = seeded.admin_list_listings(status="sold")
    assert len(rows) == 4
    assert all("data" not in r and "listing" not in r for r in rows)
    by_id = {r["id"]: r for r in rows}
    assert by_id["l3"]["sold_price"] == 25.0
    assert by_id["l3"]["user_id"] == "u1"


def test_a_broken_read_raises_rather_than_answering_zero(seeded, monkeypatch):
    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(seeded, "_get_engine", lambda: _Boom())
    for read in (lambda: seeded.admin_platform_kpis(30),
                 lambda: seeded.admin_list_users(),
                 lambda: seeded.admin_user_rollups(["u1"]),
                 lambda: seeded.admin_ledger(),
                 lambda: seeded.admin_list_listings(),
                 lambda: seeded.admin_audit_list()):
        with pytest.raises(StorageUnavailable):
            read()


def test_no_database_is_a_configuration_not_a_failure(dbmod, monkeypatch):
    """Same rule as list_listings: nothing is persisted, so the platform
    genuinely holds nothing — empty answers, no raise."""
    monkeypatch.setattr(dbmod, "_get_engine", lambda: None)
    assert dbmod.admin_list_users() == []
    assert dbmod.admin_platform_kpis(30) == {"available": False}
    assert dbmod.admin_count_users() == 0

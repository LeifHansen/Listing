""""Nothing yet" is a claim about the seller's sales, not about the database.

The notifications bell is where "your item sold — ship it" arrives. Its empty
state reads:

    Nothing yet — when an item sells, it lands here so you can ship it fast.

`db.list_notifications` and `db.unread_notification_count` both answered `[]`
and `0` on ANY exception, so a Neon blip during the 60s poll produced exactly
that sentence — on the surface a seller checks to find out whether they owe
somebody a parcel, where being wrong costs a late-shipment defect.

The transport half was already right: a failed fetch keeps the previous list
rather than blanking the bell. What was missing is the case where the request
SUCCEEDS and the answer is empty because the read underneath it did not.

Same fix as the store read that reported an empty store and the price lookup
that reported an empty market: raise on failure, and let the one caller say
whether it got to look.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from backend.errors import StorageUnavailable  # noqa: E402


@pytest.fixture
def uid(dbmod):
    from backend import auth
    rec = dbmod.create_user("u-bell", "bell@example.com",
                            auth.hash_password("hunter2hunter2"))
    assert rec not in (None, dbmod.EMAIL_TAKEN)
    return rec["id"]


def _break_the_engine(dbmod, monkeypatch):
    """The `dbmod` fixture RELOADS backend.db, so the module under test is the
    one the fixture hands back — patching an import taken at module scope
    would patch a different object and the test would pass on broken code."""
    def _boom():
        raise RuntimeError("connection to Neon reset by peer")
    monkeypatch.setattr(dbmod, "_get_engine", _boom)


def test_an_unreadable_list_is_not_an_empty_one(dbmod, uid, monkeypatch):
    dbmod.add_notification(uid, "sold", "Sold: Vintage Levi's 501",
                           body="Ship it!", listing_id="ebay-1")
    _break_the_engine(dbmod, monkeypatch)
    with pytest.raises(StorageUnavailable):
        dbmod.list_notifications(uid)


def test_an_uncountable_badge_is_not_a_zero(dbmod, uid, monkeypatch):
    dbmod.add_notification(uid, "sold", "Sold: Vintage Levi's 501",
                           listing_id="ebay-2")
    _break_the_engine(dbmod, monkeypatch)
    with pytest.raises(StorageUnavailable):
        dbmod.unread_notification_count(uid)


def test_no_database_configured_is_still_quiet(dbmod, uid, monkeypatch):
    """Not a failure — a deployment without a database has no notifications
    to show and never will. Only a broken READ is unknown."""
    monkeypatch.setattr(dbmod, "_get_engine", lambda: None)
    assert dbmod.list_notifications(uid) == []
    assert dbmod.unread_notification_count(uid) == 0


def test_a_real_read_still_answers(dbmod, uid):
    dbmod.add_notification(uid, "sold", "Sold: Vintage Levi's 501",
                           listing_id="ebay-3")
    assert len(dbmod.list_notifications(uid)) == 1
    assert dbmod.unread_notification_count(uid) == 1

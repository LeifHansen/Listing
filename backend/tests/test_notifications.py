"""Sold notifications — persistence, dedupe, and the sync hooks.

Runs against a temporary SQLite database (same approach as the account-
deletion tests): the dedupe guarantee lives in the DB's unique constraint,
which a mock can't exercise.
"""
from __future__ import annotations

import importlib

import pytest

from backend import auth


@pytest.fixture
def dbmod(monkeypatch, tmp_path):
    """A fresh db module bound to a scratch SQLite file."""
    from backend import config, db

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    importlib.reload(db)
    monkeypatch.setattr(db.config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    return db


@pytest.fixture
def uid(dbmod):
    rec = dbmod.create_user("u-1", "seller@example.com",
                            auth.hash_password("hunter2hunter2"))
    assert rec not in (None, dbmod.EMAIL_TAKEN)
    return rec["id"]


def test_add_list_and_mark_read(dbmod, uid):
    n = dbmod.add_notification(uid, "sold", "Sold: Vintage Levi's 501",
                               body="Ship it!", listing_id="ebay-1")
    assert n and n["kind"] == "sold" and n["read"] is False
    assert dbmod.unread_notification_count(uid) == 1

    rows = dbmod.list_notifications(uid)
    assert [r["id"] for r in rows] == [n["id"]]

    assert dbmod.mark_notifications_read(uid, [n["id"]]) == 1
    assert dbmod.unread_notification_count(uid) == 0
    assert dbmod.list_notifications(uid)[0]["read"] is True
    assert dbmod.list_notifications(uid, unread_only=True) == []


def test_dedupe_key_blocks_the_second_write(dbmod, uid):
    first = dbmod.add_notification(uid, "sold", "Sold", dedupe_key="sold:x:1")
    second = dbmod.add_notification(uid, "sold", "Sold", dedupe_key="sold:x:1")
    assert first is not None and second is None
    assert len(dbmod.list_notifications(uid)) == 1


def test_mark_all_read_is_scoped_to_the_owner(dbmod, uid):
    other = dbmod.create_user("u-2", "other@example.com", "hash")["id"]
    dbmod.add_notification(uid, "sold", "Mine")
    dbmod.add_notification(other, "sold", "Theirs")

    assert dbmod.mark_notifications_read(uid) == 1
    assert dbmod.unread_notification_count(other) == 1


def test_delete_user_clears_notifications(dbmod, uid):
    dbmod.add_notification(uid, "sold", "Sold")
    dbmod.delete_user(uid)
    assert dbmod.list_notifications(uid) == []


def test_notify_sold_writes_once_per_sale(dbmod, uid, monkeypatch):
    from backend.services import notifications

    monkeypatch.setattr(notifications, "db", dbmod)
    listing = {"title": "Nike Dunk Low", "price": 85.0, "ebay_listing_id": "42"}
    notifications.notify_sold(uid, "ebay-42", listing, sold_quantity=1)
    notifications.notify_sold(uid, "ebay-42", listing, sold_quantity=1)  # re-sync
    rows = dbmod.list_notifications(uid)
    assert len(rows) == 1
    assert rows[0]["listing_id"] == "ebay-42"
    assert "$85.00" in rows[0]["body"]

    # A second unit of a multi-quantity listing is a NEW sale event.
    notifications.notify_sold(uid, "ebay-42", listing, sold_quantity=2)
    assert len(dbmod.list_notifications(uid)) == 2


def test_notify_sold_never_raises(dbmod, monkeypatch):
    from backend.services import notifications

    monkeypatch.setattr(notifications, "db", dbmod)
    notifications.notify_sold(None, "x", {})       # anonymous — no-op
    notifications.notify_sold("u-1", "", {})       # no record — no-op
    notifications.notify_sold("u-1", "x", {"price": "not-a-number"})

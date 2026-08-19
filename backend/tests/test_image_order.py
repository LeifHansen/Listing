"""PATCH /api/listings/{id}/images/order.

Reordering used to ride on POST /api/save with the whole listing attached, and
the client swallowed the failure. So a drag could overwrite a field edited
elsewhere with a stale copy, and a rejected save left the new order on screen
and nowhere else -- the seller drags their main photo into place, and a reload
puts it back.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import db, main  # noqa: E402

PHOTOS = ["img_1.jpg", "img_2.jpg", "img_3.jpg"]


@pytest.fixture()
def client(monkeypatch):
    """A stand-in listing store: these tests are about the endpoint's rules,
    not about SQLAlchemy."""
    row = {"id": "s1", "status": "draft",
           "listing": {"title": "A jacket", "images": list(PHOTOS)}}

    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main, "_uid", lambda request: "u1")
    monkeypatch.setattr(db, "get_listing", lambda sid: row if sid == "s1" else None)

    def _mutate(sid, fn, status=None, user_id=None):
        if sid != "s1":
            return None
        row["listing"] = fn(dict(row["listing"]))
        if status:
            row["status"] = status
        return row["listing"]

    monkeypatch.setattr(db, "mutate_listing_data", _mutate)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)
    client = TestClient(main.app)
    client.row = row
    return client


def _patch(client, images):
    return client.patch("/api/listings/s1/images/order", json={"images": images})


def test_a_new_order_is_persisted(client):
    res = _patch(client, ["img_3.jpg", "img_1.jpg", "img_2.jpg"])
    assert res.status_code == 200
    assert res.json()["images"] == ["img_3.jpg", "img_1.jpg", "img_2.jpg"]
    assert client.row["listing"]["images"] == ["img_3.jpg", "img_1.jpg", "img_2.jpg"]


def test_nothing_else_on_the_listing_is_touched(client):
    """The whole reason for a dedicated endpoint: a drag reorders photos and
    does not carry a stale copy of every other field along with it."""
    _patch(client, ["img_2.jpg", "img_1.jpg", "img_3.jpg"])
    assert client.row["listing"]["title"] == "A jacket"


def test_dropping_a_photo_is_rejected_rather_than_obeyed(client):
    """The guard that matters. A client working from a stale list would
    otherwise DELETE the photos missing from it -- 'my photos vanished when I
    dragged one' being a considerably worse bug than the one being fixed."""
    res = _patch(client, ["img_1.jpg", "img_2.jpg"])
    assert res.status_code == 409
    assert client.row["listing"]["images"] == PHOTOS


def test_inventing_a_photo_is_rejected(client):
    assert _patch(client, PHOTOS + ["img_9.jpg"]).status_code == 409
    assert client.row["listing"]["images"] == PHOTOS


def test_reordering_a_listing_that_does_not_exist_is_a_409_not_a_crash(client):
    res = client.patch("/api/listings/nope/images/order", json={"images": PHOTOS})
    assert res.status_code == 409


def test_the_same_order_again_is_a_no_op(client, monkeypatch):
    """A drag that ends where it started must not churn the row -- it would
    bump updated_at and reshuffle every 'most recent' list in the app."""
    called = []
    monkeypatch.setattr(db, "mutate_listing_data",
                        lambda *a, **k: called.append(1))
    res = _patch(client, PHOTOS)
    assert res.status_code == 200
    assert res.json()["images"] == PHOTOS
    assert not called, "an unchanged order still wrote to the database"


def test_a_live_listing_stays_published_after_a_reorder(client, monkeypatch):
    """Reordering photos on a live listing is an edit, not an unpublish."""
    client.row["status"] = "published"
    _patch(client, ["img_2.jpg", "img_1.jpg", "img_3.jpg"])
    assert client.row["status"] == "published"

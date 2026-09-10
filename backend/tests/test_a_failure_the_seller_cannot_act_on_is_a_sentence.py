"""An exception's own text does not reach the seller's screen.

The app's rule (main._lookup_failed) is that a failure a seller cannot act
on gets a sentence they can use, with a reference that joins it to the log.
Nine sites still said `f"Couldn't rotate that photo: {exc}"` and the like:
Pillow's "cannot identify image file <_io.BytesIO object at 0x7f...>", an
OSError carrying the volume's path, pydantic's whole report with its URL.
Each now says what could not be done and quotes the reference; the detail
is in the log beside the same reference.

Validation is the one exception: a value the caller sent and the rule it
broke IS something they can act on, so the field and the rule are kept and
only the report around them goes.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402

REFERENCE = re.compile(r"quote [0-9a-f]{8} to support")


@pytest.fixture()
def photos(monkeypatch, tmp_path):
    opt = tmp_path / "optimized"
    opt.mkdir()
    (opt / "img_000.jpg").write_bytes(b"this is not a JPEG")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "optimized_dir", lambda sid: opt)
    monkeypatch.setattr(main.storage, "original_dir", lambda sid: tmp_path / "original")
    monkeypatch.setattr(main.storage, "snapshot_image", lambda *a, **k: None)
    monkeypatch.setattr(main.objstore, "enabled", lambda: False)
    monkeypatch.setattr(main, "_in_background", lambda *a, **k: None)
    return TestClient(main.app)


def test_a_photo_that_cannot_be_rotated_is_a_sentence_not_pillows_complaint(photos):
    res = photos.post("/api/rotate-image", json={"session_id": "s1", "name": "img_000.jpg"})
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "rotate that photo" in detail and REFERENCE.search(detail), detail
    assert "cannot identify" not in detail and "BytesIO" not in detail


def test_a_photo_that_cannot_be_restored_is_a_sentence(photos, monkeypatch, tmp_path):
    (tmp_path / "original").mkdir()
    (tmp_path / "original" / "src_000.jpg").write_bytes(b"nor is this")
    res = photos.post("/api/image/restore-original",
                      data={"session_id": "s1", "name": "img_000.jpg"})
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert "restore that photo" in detail and REFERENCE.search(detail), detail
    assert "cannot identify" not in detail and "src_000" not in detail


def test_an_edit_that_cannot_be_read_is_a_sentence(photos):
    res = photos.post("/api/edit-image",
                      data={"session_id": "s1", "name": "img_000.jpg"},
                      files={"file": ("img_000.jpg", b"not a picture", "image/jpeg")})
    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert "edited photo" in detail and REFERENCE.search(detail), detail
    assert "cannot identify" not in detail and "BytesIO" not in detail


def test_a_value_that_fails_validation_keeps_the_field_and_the_rule(monkeypatch):
    from backend import db
    stored = {"id": "lst1", "listing": {"title": "A", "price": 10, "images": []}}
    monkeypatch.setattr(db, "get_listing", lambda lid: dict(stored) if lid == "lst1" else None)
    monkeypatch.setattr(db, "enabled", lambda: True)
    monkeypatch.setattr(db, "upsert_listing", lambda *a, **k: True)
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    client = TestClient(main.app)

    res = client.patch("/api/listings/lst1", json={"price": "forty-five"})

    assert res.status_code == 400, res.text
    detail = res.json()["detail"]
    assert detail.startswith("That value isn't valid: price: "), detail
    assert "pydantic" not in detail and "validation error" not in detail
    assert "input_value" not in detail


def test_the_summary_of_something_that_is_not_pydantics_still_reads():
    assert main._validation_summary(RuntimeError("boom")) == "check it and try again."

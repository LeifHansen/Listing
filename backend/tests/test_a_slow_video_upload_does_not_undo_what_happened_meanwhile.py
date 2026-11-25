"""The video upload worker writes eBay's answer onto the listing as it is NOW.

It reads the listing, spends minutes pushing up to 150MB to eBay, then
recorded the result by writing back its copy of the whole video list -- and
the status it read at the start. Meanwhile, the natural flow is: add a video,
then publish. So:

  * the publish that landed during the upload was written back as "draft",
    and the live listing dropped out of Active;
  * a video the seller deleted during the upload came back, pointing at a
    file already gone; one they added was wiped.

The same applies to the photo-order write: a list write never changes the
stage.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main  # noqa: E402

UID = "u-video"


def _videos(*names):
    return [{"file": n, "size": 10, "ebay_video_id": "", "status": "",
             "message": ""} for n in names]


def test_the_worker_stamps_ebays_id_without_undoing_the_seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    assert dbmod.upsert_listing("v1", {"title": "Lamp", "videos": _videos("a.mp4")},
                                status="draft", user_id=UID)
    read_at_start = dbmod.get_listing("v1")

    # While the upload runs, the seller publishes, removes a.mp4 and adds
    # b.mp4 in its place.
    assert dbmod.upsert_listing("v1", {"title": "Lamp", "videos": _videos("b.mp4")},
                                status="published", user_id=UID)

    # The worker's copy: a.mp4, now with eBay's id.
    done = _videos("a.mp4")
    done[0].update(ebay_video_id="VID-1", status="PROCESSING")
    main._stamp_video_state("v1", read_at_start, done, UID)

    rec = dbmod.get_listing("v1")
    assert rec["status"] == "published"
    assert [v["file"] for v in rec["listing"]["videos"]] == ["b.mp4"]


def test_the_worker_stamps_an_entry_that_is_still_there(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    assert dbmod.upsert_listing("v2", {"title": "Lamp", "videos": _videos("a.mp4")},
                                status="draft", user_id=UID)
    rec = dbmod.get_listing("v2")
    done = _videos("a.mp4")
    done[0].update(ebay_video_id="VID-2", status="PROCESSING", size=999)

    main._stamp_video_state("v2", rec, done, UID)

    (video,) = dbmod.get_listing("v2")["listing"]["videos"]
    assert video["ebay_video_id"] == "VID-2"
    assert video["status"] == "PROCESSING"
    assert video["size"] == 10, "only eBay's fields are taken from the worker"


def test_a_photo_order_write_keeps_the_stage(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    assert dbmod.upsert_listing("p1", {"title": "Lamp", "images": ["a.jpg", "b.jpg"]},
                                status="draft", user_id=UID)
    stale = dbmod.get_listing("p1")
    assert dbmod.upsert_listing("p1", {"title": "Lamp", "images": ["a.jpg", "b.jpg"]},
                                status="published", user_id=UID)

    main._save_image_order("p1", stale, ["b.jpg", "a.jpg"], UID)

    rec = dbmod.get_listing("p1")
    assert rec["status"] == "published"
    assert rec["listing"]["images"] == ["b.jpg", "a.jpg"]

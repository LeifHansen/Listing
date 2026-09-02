"""POST /api/delete-image — and the drag that comes after it.

Deleting a photo used to unlink the file and stop there. The name stayed in
the saved listing, so a reload brought the tile back pointing at bytes that no
longer existed, and a publish handed eBay a photo URL that 404s. It also broke
the NEXT action: the editor's list (short one photo) and the stored list (still
holding it) could never agree again, so every reorder from then on was refused
with "this listing's photos changed somewhere else" -- a listing nothing else
had touched.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import db, main  # noqa: E402

PHOTOS = ["img_1.jpg", "img_2.jpg", "img_3.jpg"]


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """A session whose photos are real files, and a stand-in listing store."""
    opt = tmp_path / "optimized"
    opt.mkdir()
    for name in PHOTOS:
        (opt / name).write_bytes(b"jpeg")

    row = {"id": "s1", "status": "draft",
           "listing": {"title": "A jacket", "images": list(PHOTOS)}}
    saved: list[list[str]] = []

    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main, "_uid", lambda request: "u1")
    monkeypatch.setattr(db, "get_listing", lambda sid: row if sid == "s1" else None)

    def _mutate(sid, fn, status=None, user_id=None):
        if sid != "s1":
            return None
        row["listing"] = fn(dict(row["listing"]))
        return row["listing"]

    monkeypatch.setattr(db, "mutate_listing_data", _mutate)
    monkeypatch.setattr(main.storage, "optimized_dir", lambda sid: opt)
    monkeypatch.setattr(main.storage, "list_optimized",
                        lambda sid: sorted(p.name for p in opt.iterdir()))
    monkeypatch.setattr(main.storage, "save_listing",
                        lambda sid, listing: saved.append(list(listing.images)))
    monkeypatch.setattr(main.objstore, "enabled", lambda: False)

    c = TestClient(main.app)
    c.row, c.opt, c.saved = row, opt, saved
    return c


def _delete(client, name):
    return client.post("/api/delete-image",
                       json={"session_id": "s1", "name": name})


def _reorder(client, images):
    return client.patch("/api/listings/s1/images/order", json={"images": images})


def test_the_photo_leaves_the_saved_listing_not_just_the_disk(client):
    res = _delete(client, "img_2.jpg")
    assert res.status_code == 200
    assert res.json()["images"] == ["img_1.jpg", "img_3.jpg"]
    assert client.row["listing"]["images"] == ["img_1.jpg", "img_3.jpg"]
    assert not (client.opt / "img_2.jpg").exists()


def test_the_disk_copy_is_kept_in_step(client):
    """listing.json is what an unconfigured database leaves the app reading,
    and what eBay is served photos from."""
    _delete(client, "img_2.jpg")
    assert client.saved == [["img_1.jpg", "img_3.jpg"]]


def test_a_drag_right_after_a_delete_is_not_refused(client):
    """The regression this route caused. The editor drops the deleted photo
    from its list; the stored list kept it, so the permutation guard on the
    reorder could never match again."""
    assert _delete(client, "img_2.jpg").status_code == 200
    res = _reorder(client, ["img_3.jpg", "img_1.jpg"])
    assert res.status_code == 200, res.text
    assert client.row["listing"]["images"] == ["img_3.jpg", "img_1.jpg"]


def test_nothing_else_on_the_listing_is_touched(client):
    _delete(client, "img_2.jpg")
    assert client.row["listing"]["title"] == "A jacket"


def test_a_listing_we_could_not_write_keeps_its_photo(client, monkeypatch):
    """The file is unlinked only after the record says so. A delete that
    reported success while the write failed would leave the listing pointing
    at a photo that no longer exists -- the exact state this route created."""
    monkeypatch.setattr(db, "mutate_listing_data",
                        lambda *a, **k: None)  # write refused
    assert _delete(client, "img_2.jpg").status_code == 503
    assert (client.opt / "img_2.jpg").exists()
    assert client.row["listing"]["images"] == PHOTOS


def test_deleting_a_photo_the_listing_never_had_still_removes_the_file(client):
    """A file on the volume that the saved order doesn't mention: there is
    nothing to write, and the delete is still a delete."""
    (client.opt / "img_9.jpg").write_bytes(b"jpeg")
    res = _delete(client, "img_9.jpg")
    assert res.status_code == 200
    assert not (client.opt / "img_9.jpg").exists()
    assert client.row["listing"]["images"] == PHOTOS
    assert client.saved == []

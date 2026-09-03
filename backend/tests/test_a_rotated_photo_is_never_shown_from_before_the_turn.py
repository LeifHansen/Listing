"""POST /api/rotate-image answers with a version no load of the photo has used.

The editor cache-busts a photo's URL with a version. That version was a
per-open counter -- 0, then 1, then 2 -- so every open of the editor asked
for "?v=0" again and a rotate asked for "?v=1" again. /media is served
no-cache, but a browser reuses an image it has ALREADY loaded in the same
page for an identical URL without asking the server: "?v=1" was answered
with the bytes of an earlier edit, the tile's optimistic turn came off over
that picture, and the seller watched the photo rotate and then rotate
straight back while the file on the server stayed turned.

The route now returns the rotated file's own timestamp for the client to
use as the version: a value that changes with every rotate and that no
earlier load can have cached.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from backend import main  # noqa: E402


@pytest.fixture()
def client(monkeypatch, tmp_path):
    opt = tmp_path / "optimized"
    opt.mkdir()
    Image.new("RGB", (8, 4), "white").save(opt / "img_1.jpg", "JPEG")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "optimized_dir", lambda sid: opt)
    monkeypatch.setattr(main.storage, "snapshot_image", lambda *a, **k: None)
    monkeypatch.setattr(main.objstore, "enabled", lambda: False)
    monkeypatch.setattr(main, "_in_background", lambda *a, **k: None)
    c = TestClient(main.app)
    c.opt = opt
    return c


def _rotate(client):
    res = client.post("/api/rotate-image", json={"session_id": "s1", "name": "img_1.jpg"})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_answer_carries_the_rotated_files_own_version(client):
    body = _rotate(client)
    assert body["ok"] is True
    stat = (client.opt / "img_1.jpg").stat()
    assert body["version"] == int(stat.st_mtime * 1000)
    # And the file really turned: 8x4 became 4x8.
    with Image.open(client.opt / "img_1.jpg") as img:
        assert img.size == (4, 8)


def test_two_rotates_never_answer_with_the_same_version(client):
    first = _rotate(client)["version"]
    time.sleep(0.01)
    second = _rotate(client)["version"]
    assert second != first

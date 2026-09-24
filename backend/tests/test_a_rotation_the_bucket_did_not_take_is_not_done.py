"""A rotation the bucket did not take is not reported as done.

eBay is handed the R2 copy of a photo at publish, and _ensure_local pulls the
R2 copy back down once the machine recycles — so a rotate that turned the
local file and not the bucket's copy looks right in the tile and is simply
gone later, or goes live sideways. The route awaits the push for exactly that
reason and answers 502 when it fails.

It detected the failure with try/except around objstore.upload. upload never
raises: it logs and answers None. So the 502 could not fire and a failed push
answered 200 — the one outcome the await existed to rule out. edit-image and
restore-original beside it read the return value; rotate reads it now too.
"""
from __future__ import annotations

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
    monkeypatch.setattr(main.deps, "assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "optimized_dir", lambda sid: opt)
    monkeypatch.setattr(main.storage, "snapshot_image", lambda *a, **k: None)
    monkeypatch.setattr(main.objstore, "enabled", lambda: True)
    monkeypatch.setattr(main, "_in_background", lambda *a, **k: None)
    return TestClient(main.app)


def _rotate(client):
    return client.post("/api/rotate-image",
                       json={"session_id": "s1", "name": "img_1.jpg"})


def test_a_push_the_bucket_refused_is_a_502(client, monkeypatch):
    # What objstore.upload really does on a failure: log, and answer None.
    monkeypatch.setattr(main.objstore, "upload", lambda *a, **k: None)
    res = _rotate(client)
    assert res.status_code == 502, res.text
    assert "didn't update" in res.json()["detail"]


def test_a_push_the_bucket_took_is_done(client, monkeypatch):
    pushed = []
    monkeypatch.setattr(main.objstore, "upload",
                        lambda path, key, *a, **k: pushed.append(key)
                        or f"https://r2.example/{key}")
    res = _rotate(client)
    assert res.status_code == 200, res.text
    assert pushed and pushed[0].endswith("img_1.jpg")

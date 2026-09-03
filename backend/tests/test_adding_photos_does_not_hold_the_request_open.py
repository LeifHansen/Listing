"""POST /api/upload-more answers as soon as the files are saved; the work is a job.

"Adding photos to an existing listing is taking forever / not working." The
route saved the files and then, still inside the request, ran the orientation
pass (a vision call) and the optimize/cutout pass -- single-flight inference,
queued behind whatever bulk batch held the lock, on a model that takes a
minute to warm. Every other upload path had already moved that work to a job
the client polls; this one had not, and past the client's deadline the request
was abandoned with the photos and the tokens lost to work still running.

Now the request returns a job id the moment the originals are on disk, the
job reports "photo 2 of 4" as it goes, and its result is what the synchronous
answer used to be: the new filenames, the full photo list, and which cutouts
kept their background.
"""
from __future__ import annotations

import io
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from backend import main  # noqa: E402


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (120, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def client(monkeypatch, tmp_path):
    orig = tmp_path / "original"
    opt = tmp_path / "optimized"
    orig.mkdir()
    opt.mkdir()
    (opt / "img_000.jpg").write_bytes(_jpeg())      # the listing's one photo
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main, "_uid", lambda request: "u1")
    monkeypatch.setattr(main.storage, "original_dir", lambda sid: orig)
    monkeypatch.setattr(main.storage, "optimized_dir", lambda sid: opt)
    monkeypatch.setattr(main.storage, "list_optimized",
                        lambda sid: sorted(p.name for p in opt.iterdir()))
    monkeypatch.setattr(main.orient, "detect_rotations", lambda paths: {})
    monkeypatch.setattr(main, "_in_background", lambda *a, **k: None)
    seen: dict = {}

    def optimize_batch(jobs, remove_bg=False, progress=None, **kw):
        seen["remove_bg"] = remove_bg
        out = []
        for i, (src, dst, _rot) in enumerate(jobs):
            dst.write_bytes(src.read_bytes())
            if progress:
                progress(i + 1, len(jobs))
            out.append({"file": dst.name, "bg_error": None})
        return out
    monkeypatch.setattr(main.images, "optimize_batch", optimize_batch)
    c = TestClient(main.app)
    c.opt, c.seen = opt, seen
    return c


def _finish(client, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            return body
        time.sleep(0.02)
    raise AssertionError("the add-photos job never finished")


def _add(client, n=2, remove_bg="false"):
    files = [("files", (f"phone_{i}.jpg", _jpeg(), "image/jpeg")) for i in range(n)]
    res = client.post("/api/upload-more/s1", files=files, data={"remove_bg": remove_bg})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_request_hands_back_a_job_and_the_job_hands_back_the_photos(client):
    start = _add(client, n=2)
    assert start["job_id"] and start["total"] == 2

    body = _finish(client, start["job_id"])
    assert not body.get("error"), body["error"]
    result = body["result"]
    assert result["added"] == ["img_001.jpg", "img_002.jpg"]
    assert result["optimized"] == ["img_000.jpg", "img_001.jpg", "img_002.jpg"]
    assert result["optimize_results"] == []
    assert (client.opt / "img_002.jpg").is_file()
    # The job counted the photos off as it went.
    assert body["total_photos"] == 2 and body["current"] == 2


def test_the_background_choice_reaches_the_job(client):
    start = _add(client, n=1, remove_bg="true")
    _finish(client, start["job_id"])
    assert client.seen["remove_bg"] is True


def test_a_pile_nothing_could_be_made_of_fails_the_job_not_the_request(client, monkeypatch):
    monkeypatch.setattr(main.images, "optimize_batch",
                        lambda jobs, remove_bg=False, progress=None, **kw:
                        [{"file": dst.name, "error": "corrupt"} for _s, dst, _r in jobs])
    start = _add(client, n=1)
    body = _finish(client, start["job_id"])
    assert "Could not process" in body["error"]
    assert not (client.opt / "img_001.jpg").exists()

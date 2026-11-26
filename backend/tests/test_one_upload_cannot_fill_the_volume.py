"""A single-listing upload is bounded in total bytes, not just per file.

/api/upload needs no login and capped each file (60MB) and the file count
(40), but not the request: 2.4GB of writes onto a 1GB volume every seller
shares. The bulk route already had a per-batch total; this one now has the
same, and refusing leaves nothing behind on the volume.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402


def test_an_upload_over_the_batch_total_is_refused_and_cleaned_up(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(main, "MAX_BULK_BATCH_BYTES", 2500)
    made = []
    real_new = main.storage.new_session_id
    monkeypatch.setattr(main.storage, "new_session_id",
                        lambda: made.append(real_new()) or made[-1])
    client = TestClient(main.app)
    files = [("files", (f"p{i}.jpg", b"x" * 900, "image/jpeg")) for i in range(3)]

    res = client.post("/api/upload", files=files)

    assert res.status_code == 413, res.text[:200]
    assert made and not main.storage.session_dir(made[0]).exists()


def test_an_upload_under_the_total_is_accepted(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(main, "MAX_BULK_BATCH_BYTES", 2500)
    client = TestClient(main.app)
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (16, 16), "white").save(buf, "JPEG")
    files = [("files", ("p.jpg", buf.getvalue(), "image/jpeg"))]

    res = client.post("/api/upload", files=files)

    assert res.status_code < 400, res.text[:200]

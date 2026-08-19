"""Readiness and the busy path.

Two failure modes that used to look identical from the outside -- "the server
is fine but full" and "the server is broken" -- and both showed up as a
spinner that never resolved.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main, storage  # noqa: E402
from backend.services import images  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_ready_reports_the_things_that_stop_photo_work(client):
    body = client.get("/api/ready").json()
    assert set(body["checks"]) == {"storage_writable", "disk_space", "database"}
    assert "image_engine" in body


def test_ready_answers_503_when_it_cannot_do_the_work(client, monkeypatch):
    """The point of a readiness probe: a machine that can't write photos has
    to SAY so, in a status code, not report 200 and fail the next upload."""
    monkeypatch.setattr(storage, "writable", lambda: False)
    res = client.get("/api/ready")
    assert res.status_code == 503
    assert res.json()["ready"] is False
    assert res.json()["checks"]["storage_writable"] is False


def test_low_disk_is_not_ready(client, monkeypatch):
    monkeypatch.setattr(storage, "disk_free_bytes", lambda: 5 * 1_000_000)
    assert client.get("/api/ready").status_code == 503


def test_a_configured_but_unreachable_database_is_not_ready(client, monkeypatch):
    monkeypatch.setattr(main.db, "db_status",
                        lambda: {"configured": True, "connected": False})
    assert client.get("/api/ready").status_code == 503


def test_no_database_configured_is_a_valid_setup(client, monkeypatch):
    """Filesystem-only is a supported deployment, not a fault."""
    monkeypatch.setattr(main.db, "db_status",
                        lambda: {"configured": False, "connected": False})
    assert client.get("/api/ready").status_code == 200


def test_health_stays_up_even_when_not_ready(client, monkeypatch):
    """Liveness and readiness are different questions. A full disk means stop
    sending work here; it does not mean restart the process."""
    monkeypatch.setattr(storage, "writable", lambda: False)
    assert client.get("/api/health").status_code == 200


# --- the busy path ----------------------------------------------------------
def test_a_full_inference_queue_gives_up_instead_of_hanging(monkeypatch):
    """The 'appears to hang' bug. One inference runs at a time, and the wait
    for that slot used to be unbounded -- so a studio cutout queued behind a
    forty-photo batch sat there alive and idle, forever."""
    from PIL import Image

    monkeypatch.setattr(images, "INFER_WAIT_SECONDS", 0.05)
    images._INFER_LOCK.acquire()
    try:
        with pytest.raises(images.CutoutBusy):
            images._alpha_mask(Image.new("RGB", (64, 64), (200, 200, 200)))
    finally:
        images._INFER_LOCK.release()


def test_busy_is_retryable_not_a_crash():
    """CutoutBusy has to be its own type: main maps it to 503 + Retry-After,
    while an engine's own complaint is a 422 about the photo. Collapsing them
    tells the seller their photo is bad when the machine was merely busy."""
    assert issubclass(images.CutoutBusy, RuntimeError)
    assert not issubclass(images.CutoutBusy, ValueError)


def test_the_engine_state_a_probe_reads_is_cheap_and_complete():
    state = images.engine_state()
    assert set(state) == {"model", "loaded", "busy", "last_inference_seconds"}
    assert state["busy"] is False

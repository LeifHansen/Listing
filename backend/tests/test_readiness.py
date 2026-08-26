"""Readiness and the busy path.

Two failure modes that used to look identical from the outside -- "the server
is fine but full" and "the server is broken" -- and both showed up as a
spinner that never resolved.
"""
from __future__ import annotations

import threading

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
    forty-photo batch sat there alive and idle, forever.

    An interactive caller passes its own (short) deadline, because there a
    prompt "busy, try again" is the useful answer."""
    from PIL import Image

    images._INFER_LOCK.acquire()
    try:
        with pytest.raises(images.CutoutBusy):
            images._alpha_mask(Image.new("RGB", (64, 64), (200, 200, 200)),
                               wait=0.05)
    finally:
        images._INFER_LOCK.release()


def test_a_batch_photo_queues_for_the_model_instead_of_giving_up(monkeypatch):
    """The other half of that trade, and the more expensive one to get wrong.

    A photo in a background batch has nobody watching it, so giving up buys
    nothing: there is no retry, the photo is just SAVED WITH ITS BACKGROUND
    STILL ON. One deadline used to serve both callers at 25s -- shorter than a
    single inference on a loaded box -- so in a batch running two photos at a
    time, the queued one was guaranteed to time out and keep its background.
    The batch deadline has to outlast a real queue."""
    from PIL import Image

    assert images.BATCH_INFER_WAIT_SECONDS >= 120, (
        "the batch deadline has to outlast a slow inference, or queued photos "
        "silently keep their backgrounds")
    assert images.BATCH_INFER_WAIT_SECONDS > images.INFER_WAIT_SECONDS

    # With the slot held, a batch caller (no explicit wait) must still be
    # waiting when the interactive deadline would already have given up.
    monkeypatch.setattr(images, "BATCH_INFER_WAIT_SECONDS", 30)
    monkeypatch.setattr(images, "INFER_WAIT_SECONDS", 0.05)
    settled = threading.Event()
    outcome = []

    def _queued_photo() -> None:
        try:
            images._alpha_mask(Image.new("RGB", (64, 64), (200, 200, 200)))
            outcome.append("got-the-slot")
        except images.CutoutBusy:
            outcome.append("gave-up")
        except Exception:  # noqa: BLE001 - got the slot, then found no rembg
            outcome.append("got-the-slot")
        settled.set()

    images._INFER_LOCK.acquire()
    try:
        threading.Thread(target=_queued_photo, daemon=True).start()
        # Well past the interactive deadline, comfortably short of the batch
        # one. Neither outcome yet -- still queued -- is the whole point.
        assert not settled.wait(0.5), f"batch photo settled early: {outcome}"
    finally:
        images._INFER_LOCK.release()
    settled.wait(5)
    assert outcome == ["got-the-slot"]


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


# --- the housekeeping daemon's decision ------------------------------------
#
# reclaim_space() had tests for what it frees; nothing covered when the daemon
# decides to run it, which is where the defect was.


def test_a_full_or_unreadable_volume_counts_as_low():
    """disk_free_bytes() reports 0 for BOTH a genuinely full volume and a stat
    it could not take -- it swallows the error and returns 0. Neither is
    evidence of room, so both have to escalate.

    The guard used to be `bool(free) and free < limit`, meaning to say "if we
    know the free space". Its actual effect was to read 0 as "no reason to
    hurry" and switch aggressive reclaim off at the one moment it exists for.
    """
    aggressive, delay = main._reclaim_plan(0)
    assert aggressive is True
    assert delay == main._RECLAIM_INTERVAL_LOW


def test_a_low_volume_is_revisited_sooner():
    """The docstring has always promised "sooner when the volume is running
    low"; the loop slept three hours either way, so a volume that filled
    mid-batch stayed broken until the next pass came round on its own."""
    aggressive, delay = main._reclaim_plan(main._LOW_DISK_BYTES - 1)
    assert aggressive is True
    assert delay == main._RECLAIM_INTERVAL_LOW
    assert delay < main._RECLAIM_INTERVAL


def test_a_healthy_volume_keeps_the_slow_pass():
    """The other half of the trade: room to spare must NOT shorten the TTLs.
    Aggressive mode drops originals after 15 minutes, so leaving it on costs
    every subsequent edit a round trip to R2 for bytes that were local."""
    aggressive, delay = main._reclaim_plan(main._LOW_DISK_BYTES + 1)
    assert aggressive is False
    assert delay == main._RECLAIM_INTERVAL

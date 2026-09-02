"""Stopping a bulk batch that the seller no longer wants to wait for.

The failure this closes: a batch had no off switch. Once the photos were in,
the only way out of the progress bar was to let it finish — and a batch that
had stopped moving (a wedged provider call, a machine chewing through 250
cutouts) left the seller watching a bar that would never fill, with the AI
still spending on their account.

Two halves, and they are deliberately separate. The REQUEST settles the job
immediately, so the client is freed even if the worker is stuck somewhere it
cannot answer from. The WORKER stands down at its next checkpoint — between
photos, between items — so nothing is abandoned half-charged and every item
already drafted stays exactly where it was saved.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.services import images, jobstore  # noqa: E402


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A jobstore with a data root of its own — the dict is process-wide."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    jobstore.reset()
    yield jobstore
    jobstore.reset()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(main, "_uid", lambda request: "u1")
    return TestClient(main.app)


def _running(store, job_id="job1", uid="u1", **fields):
    store.register(job_id, {"phase": "identifying", "done": False,
                            "current": 1, "total_items": 4, **fields}, uid=uid)
    return job_id


# ------------------------------------------------- the request settles it

def test_stopping_finishes_the_job_for_the_client(store):
    """Marked done by the REQUEST, not by the worker noticing. The batch a
    seller wants to stop is usually the one that stopped answering."""
    job = _running(store)
    assert store.request_cancel(job, "u1") == "stopping"
    snap = store.snapshot(job, "u1")
    assert snap["done"] is True
    assert snap["cancelled"] is True


def test_the_worker_is_told_to_stand_down(store):
    job = _running(store)
    assert store.cancel_requested(job) is False
    store.request_cancel(job, "u1")
    assert store.cancel_requested(job) is True


def test_a_finished_job_says_so_instead_of_stopping(store):
    """Not an error — the seller asked for it to be over and it is. But it
    must not be re-flagged, or the record would claim it was called off."""
    job = _running(store, done=True, phase="done")
    assert store.request_cancel(job, "u1") == "finished"
    assert store.cancel_requested(job) is False
    assert store.snapshot(job, "u1").get("cancelled") is None


def test_an_unknown_job_cannot_be_stopped(store):
    assert store.request_cancel("nope", "u1") is None


def test_someone_elses_batch_keeps_running(store):
    """Same rule as reading the status: another account's job is invisible,
    and invisible is not stoppable."""
    job = _running(store, uid="owner")
    assert store.request_cancel(job, "intruder") is None
    assert store.cancel_requested(job) is False
    assert store.snapshot(job, "owner")["done"] is False


# ------------------------------------------------------------- the route

def test_the_route_stops_the_sellers_batch(client, store):
    job = _running(store)
    body = client.post(f"/api/bulk/cancel/{job}").json()
    assert body == {"ok": True, "stopped": True, "already_finished": False}
    assert store.snapshot(job, "u1")["cancelled"] is True


def test_the_route_reports_a_batch_that_had_already_finished(client, store):
    job = _running(store, done=True, phase="done")
    body = client.post(f"/api/bulk/cancel/{job}").json()
    assert body == {"ok": True, "stopped": False, "already_finished": True}


def test_the_route_is_a_404_for_an_id_it_cannot_see(client, store):
    """Unknown and not-yours answer the same way, so the reply never confirms
    that another account's id exists."""
    assert client.post("/api/bulk/cancel/nope").status_code == 404
    other = _running(store, job_id="job2", uid="someone-else")
    assert client.post(f"/api/bulk/cancel/{other}").status_code == 404
    assert store.cancel_requested(other) is False


# ------------------------------------------------------- the worker leaves

def test_the_worker_leaves_at_its_next_checkpoint(store):
    job = _running(store)
    main._stop_if_cancelled(job)          # still running: carries on
    store.request_cancel(job, "u1")
    with pytest.raises(main._BatchStopped):
        main._stop_if_cancelled(job)


def test_the_photo_pass_stops_between_photos(tmp_path):
    """The cutouts are where a long batch spends its time — one local
    inference is ~100s — so the stop has to reach inside that pass rather
    than waiting for the whole pile."""
    pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "original"
    src.mkdir()
    for i in range(3):
        Image.new("RGB", (200, 200), (200, 200, 200)).save(src / f"src_{i}.jpg")
    with pytest.raises(images.Stopped):
        images.optimize_all(src, tmp_path / "optimized", False,
                            should_stop=lambda: True)


def test_a_stopped_batch_is_not_picked_back_up(store, monkeypatch, tmp_path):
    """A restart is not a second chance to run work the seller said no to."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    staging = storage.new_session_id()
    originals = storage.original_dir(staging)
    (originals / "src_000.jpg").write_bytes(b"not really a jpeg")
    record = {"id": "job1", "phase": "optimizing", "done": True,
              "_staging_id": staging, "_resumes": 0}
    # The same record without the flag IS resumable — otherwise this test
    # would pass on a batch that was never resumable to begin with.
    monkeypatch.setattr(main, "_run_bulk_job", lambda *a, **k: None)
    assert main._resume_interrupted_batches([dict(record)]) == {"job1"}
    assert main._resume_interrupted_batches([{**record, "_cancel": True}]) == set()

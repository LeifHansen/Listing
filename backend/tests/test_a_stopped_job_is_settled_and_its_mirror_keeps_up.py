"""What a restart reads back about a job is the job's LAST state, and it is acted on.

Three seams between the mirror on disk and the boot that reads it:

  * A job the seller STOPPED is marked done by the request, before its worker
    has settled anything -- and a pipeline paused for the seller's notes has
    no worker at all. Its up-front AI charge (`_refunds`) was then never
    given back: the boot settles only jobs it finds still running, and this
    one already said done.
  * The mirror is written outside the store's lock, so two writes of one job
    could land in the wrong order: a progress tick snapshotted before the
    seller pressed Stop, written after it, put back a mirror with no stop in
    it -- and the next boot would pick up a batch the seller had called off.
  * The boot handed every interrupted job to the resume passes already marked
    done, and the crosspost pass reads done as "finished, leave it" -- so no
    crosspost was ever picked back up after a restart.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from backend import config
from backend.services import jobstore


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    jobstore.reset()
    yield jobstore
    jobstore.reset()


RECEIPTS = [{"entry_id": "spend-1", "tokens": 1}]


def test_a_stopped_job_still_holding_a_charge_is_handed_over_to_settle(store):
    store.register("job-1", {"kind": "pipeline", "phase": jobstore.AWAITING_NOTES,
                             "done": False, "_refunds": RECEIPTS}, uid="u1")
    assert store.request_cancel("job-1", "u1") == "stopping"

    jobstore.reset()                              # the restart
    settle = store.adopt_mirrors()
    assert [r["id"] for r in settle] == ["job-1"]
    assert settle[0]["_refunds"] == RECEIPTS and settle[0]["_cancel"] is True


def test_a_job_that_finished_and_settled_is_not_paid_back(store):
    store.register("job-1", {"kind": "identify", "done": False,
                             "_refunds": RECEIPTS}, uid="u1")
    store.request_cancel("job-1", "u1")
    store.update("job-1", done=True, phase="done")  # the worker delivered
    jobstore.reset()
    assert store.adopt_mirrors() == []


def test_a_tick_written_late_does_not_undo_a_stop(store, monkeypatch, tmp_path):
    store.register("job-1", {"phase": "optimizing", "done": False,
                             "_staging_id": "s1"}, uid="u1")
    real = jobstore._write_mirror
    gate = threading.Event()

    def _slow(job_id, record, *a, **k):
        if record.get("current") == 1 and not record.get("_cancel"):
            gate.wait(5)                          # the tick from before the stop
        return real(job_id, record, *a, **k)

    monkeypatch.setattr(jobstore, "_write_mirror", _slow)
    tick = threading.Thread(target=store.update, args=("job-1",),
                            kwargs={"current": 1})
    tick.start()
    for _ in range(500):                          # until the tick has snapshotted
        if jobstore._JOBS["job-1"].get("current") == 1:
            break
        time.sleep(0.01)
    store.request_cancel("job-1", "u1")
    gate.set()
    tick.join()

    mirror = json.loads((tmp_path / "jobs" / "job-1.json").read_text())
    assert mirror.get("_cancel") is True and mirror["done"] is True


def test_an_interrupted_job_reaches_the_resume_passes_as_it_was(store):
    store.register("job-1", {"kind": "crosspost-etsy", "phase": "publishing",
                             "done": False, "_ids": ["a"], "_done": []}, uid="u1")
    jobstore.reset()
    [record] = store.adopt_mirrors()
    assert record["done"] is False            # what the crosspost pass checks
    assert "restarted" in record["error"]
    assert store.snapshot("job-1", "u1")["done"] is True  # the client's answer

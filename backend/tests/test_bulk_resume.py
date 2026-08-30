"""Which interrupted batches startup may pick back up.

The other half of test_photo_resume.py. Resuming is only safe for a batch that
had not started drafting: past that point each finished item is already saved
AND already billed per item, so re-running would duplicate both.
"""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402

from backend import main, storage  # noqa: E402


def _photos(dir_, n=3):
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = Image.new("RGB", (300, 300), (240, 240, 240))
        img.paste(Image.new("RGB", (120, 120), (30, 60, 120)), (90, 90))
        img.save(dir_ / f"src_{i:03d}.jpg", "JPEG")
    return dir_


@pytest.fixture
def resumed(monkeypatch):
    """Capture what _resume_interrupted_batches decides to restart, without
    actually re-entering the photo pipeline."""
    calls = []
    started = threading.Event()

    def _fake(job_id, staging_id, strip_bg, uid, resumed=False):
        calls.append({"job_id": job_id, "staging_id": staging_id,
                      "strip_bg": strip_bg, "uid": uid, "resumed": resumed})
        started.set()

    monkeypatch.setattr(main, "_run_bulk_job", _fake)

    def _run(records, expect=True):
        # The RETURN VALUE decides, not a race with a worker thread.
        # `not started.wait(0.3)` could only ever fail in the safe direction:
        # a machine slow enough to take 300ms to start the thread turned a
        # batch that WAS wrongly resumed into a pass. _resume_interrupted_
        # batches says what it decided, so ask it.
        picked = main._resume_interrupted_batches(records)
        assert bool(picked) is expect, (
            f"resumed={sorted(picked)} (expected {'one' if expect else 'none'})")
        if expect:
            # It also has to actually run, which is a thread and does need a
            # wait -- but a generous one, because a slow start is not a bug.
            assert started.wait(5), "decided to resume, then never ran the job"
        return calls

    return _run


def _interrupted(staging, phase="optimizing", **extra):
    return {"id": "job-1", "phase": phase, "done": True,
            "_staging_id": staging, "_strip_bg": True, "_uid": "owner",
            "total_photos": 40, **extra}


def test_a_batch_stopped_during_the_photo_pass_is_picked_back_up(resumed, tmp_path):
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging))

    calls = resumed([_interrupted(staging)])
    assert calls[0]["staging_id"] == staging
    assert calls[0]["resumed"] is True, (
        "a resumed run must know it is one — the background-removal tokens "
        "were already charged on the first attempt")
    assert calls[0]["strip_bg"] is True and calls[0]["uid"] == "owner"


@pytest.mark.parametrize("phase", ["uploading", "optimizing", "grouping"])
def test_every_phase_before_drafting_can_resume(resumed, phase):
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging))
    assert resumed([_interrupted(staging, phase=phase)])


def test_a_batch_that_had_started_drafting_is_left_alone(resumed):
    """Once identifying starts, finished items are saved as drafts AND have
    been billed per item. Re-running would duplicate both."""
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging))
    resumed([_interrupted(staging, phase="identifying", current=4)], expect=False)


def test_a_batch_whose_photos_are_gone_is_not_resumed(resumed):
    """Swept staging, or a volume that didn't come back. Nothing to resume
    from, and re-running would optimize an empty directory."""
    resumed([_interrupted(storage.new_session_id())], expect=False)


def test_checking_for_photos_does_not_recreate_a_swept_session(resumed):
    """storage.original_dir() creates the tree it is asked about. Using it to
    ask "are the photos still there?" would answer yes every time AND re-make
    the directories the orphan sweep had just reclaimed."""
    staging = storage.new_session_id()
    resumed([_interrupted(staging)], expect=False)
    assert not storage.session_dir(staging).exists(), \
        "asking about a swept batch put its directories back"


def test_a_batch_that_keeps_dying_is_eventually_left_alone(resumed, monkeypatch):
    """A batch that dies, resumes and dies again is hitting something it will
    keep hitting. Restarting into the same doomed batch forever would mean the
    machine never gets to serve anyone else."""
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging))
    monkeypatch.setattr(main, "BULK_MAX_RESUMES", 2)
    resumed([_interrupted(staging, _resumes=2)], expect=False)


def test_other_kinds_of_job_are_not_treated_as_photo_batches(resumed):
    """pipeline/identify/ebay-import jobs each have their own worker; handing
    one to the bulk worker would run the wrong pipeline over its photos."""
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging))
    resumed([_interrupted(staging, kind="pipeline")], expect=False)


def test_resuming_marks_the_job_running_again_for_its_poller(monkeypatch):
    """The browser is still polling. A resumed batch has to stop reporting the
    interrupted error, or the seller closes a tab that was about to finish."""
    from backend.services import jobstore

    staging = storage.new_session_id()
    _photos(storage.original_dir(staging))
    monkeypatch.setattr(main, "_run_bulk_job", lambda *a, **k: None)

    jobstore.register("job-1", {"phase": "optimizing", "done": True,
                                "error": "The server restarted..."}, uid="owner")
    main._resume_interrupted_batches([_interrupted(staging)])

    snap = jobstore.snapshot("job-1", "owner")
    assert snap["done"] is False and snap["error"] is None
    assert snap["resumed"] is True


# --- settling the charge of a job that will never finish -------------------
def test_a_job_that_died_before_delivering_gets_its_charge_back(monkeypatch):
    """The seller paid up front for a draft that was never saved, and the
    receipt died with the process, so no finally was ever going to give it
    back."""
    from backend.services import tokens

    refunded = []
    monkeypatch.setattr(tokens, "refund_all",
                        lambda rs: refunded.extend(rs or []) or len(rs or []))

    main._settle_interrupted_jobs([
        {"id": "job-9", "kind": "identify", "phase": "identifying",
         "_refunds": [{"ok": True, "entry_id": "e1", "user_id": "u1"}]},
    ])
    assert refunded == [{"ok": True, "entry_id": "e1", "user_id": "u1"}]


def test_a_batch_being_resumed_keeps_its_charge(resumed_settle, monkeypatch):
    """The other side of the same coin: that work is about to be delivered, so
    refunding it would hand back tokens for a draft the seller then gets."""
    from backend.services import tokens

    refunded = []
    monkeypatch.setattr(tokens, "refund_all",
                        lambda rs: refunded.extend(rs or []) or len(rs or []))

    staging = storage.new_session_id()
    _photos(storage.original_dir(staging))
    main._settle_interrupted_jobs([
        dict(_interrupted(staging),
             _refunds=[{"ok": True, "entry_id": "e1", "user_id": "u1"}]),
    ])
    assert refunded == [], "refunded a batch that is being finished"


@pytest.fixture
def resumed_settle(monkeypatch):
    """_settle_interrupted_jobs runs the real resume path; stub the worker so
    it decides to resume without re-entering the photo pipeline."""
    monkeypatch.setattr(main, "_run_bulk_job", lambda *a, **k: None)


def test_finishing_a_job_settles_its_recorded_charge():
    """The invariant the startup pass depends on: reaching done means the
    worker already settled up in process, so nothing is left to give back."""
    from backend.services import jobstore

    jobstore.register("job-8", {"phase": "identifying", "done": False,
                                "_refunds": [{"ok": True, "entry_id": "e1",
                                              "user_id": "u1"}]}, uid="owner")
    jobstore.update("job-8", phase="category")
    assert jobstore._JOBS["job-8"].get("_refunds"), "cleared too early"

    jobstore.update("job-8", done=True, result={})
    assert not jobstore._JOBS["job-8"].get("_refunds")

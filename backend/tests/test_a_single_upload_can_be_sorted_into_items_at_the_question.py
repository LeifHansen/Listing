"""Sorting a single upload into items at the guidance step.

The uploader has a bulk toggle, and the seller who forgot to tick it reaches
the question with every photo of two items under ONE item. Until now the only
way out was through: write the listing, then split it -- a draft paid for, a
split, and two more drafts. Nothing has been drafted or charged for at the
question, so the way back has to cost nothing: the pile is sorted the way a
batch would have sorted it, the job becomes a paused batch with every tool a
batch has at that step, and the one charge the upload took up front goes back
because a batch charges per item.

A sort that finds one item, or fails, must put the single-item question back
exactly as it was and say why -- the seller has lost nothing, least of all
the upload.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.services import jobstore  # noqa: E402

JOB = "job-p"
RECEIPT = {"ok": True, "entry_id": "spend-1", "user_id": "owner"}


def _photos(dir_, n):
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (60, 60), (240, 240, 240)).save(
            dir_ / f"src_{i:03d}.jpg", "JPEG")


@pytest.fixture(autouse=True)
def _own_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    jobstore.reset()
    yield
    for job_id in list(jobstore._JOBS):
        jobstore.update(job_id, done=True)
    jobstore.reset()


@pytest.fixture(autouse=True)
def _no_bucket(monkeypatch):
    monkeypatch.setattr(main.objstore, "upload_optimized", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _im_the_owner(monkeypatch):
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")


@pytest.fixture(autouse=True)
def refuses_to_draft(monkeypatch):
    def never(*a, **k):
        raise AssertionError("the AI was asked to draft while the seller was "
                             "still sorting out which photos are one item")

    monkeypatch.setattr(main.claude_ai, "identify", never)


@pytest.fixture(autouse=True)
def inline_threads(monkeypatch):
    """The sort runs on a thread the client polls. Run it to completion
    inside the request instead, so the test reads where it left the job."""
    class Inline:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._run = lambda: target(*args, **(kwargs or {}))

        def start(self):
            self._run()

    monkeypatch.setattr(main.threading, "Thread", Inline)


@pytest.fixture
def refunds(monkeypatch):
    """What was given back, as the receipts the refund was asked for."""
    given = []
    monkeypatch.setattr(main.tokens, "refund",
                        lambda receipt, units=None: given.append(receipt) or True)
    return given


def _sorted_by(monkeypatch, groups, asked=None):
    """Stand in for the grouping pass. `asked` collects the notes it saw."""
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")

    def group(thumbs, notes=""):
        if asked is not None:
            asked.append(notes)
        return {"groups": groups}

    monkeypatch.setattr(main.claude_ai, "group_photos", group)


@pytest.fixture
def two_items(monkeypatch):
    """Photos 0-1 are one item and 2-3 the other."""
    asked = []
    _sorted_by(monkeypatch, [{"name": "a blue polo", "indices": [0, 1]},
                             {"name": "a champagne bottle", "indices": [2, 3]}],
               asked)
    return asked


def _paused_upload(photos=4, notes=""):
    """A single upload run to the question: one item, every photo under it,
    the identify charge taken up front and written on the job."""
    session = storage.new_session_id()
    _photos(storage.original_dir(session), photos)
    storage.save_notes(session, notes)
    jobstore.register(JOB, {"id": JOB, "kind": "pipeline", "phase": "optimizing",
                            "done": False, "error": None, "result": None,
                            "_session_id": session, "_refunds": [RECEIPT]},
                      uid="owner")
    main._run_pipeline_job(JOB, session, "owner", False, None, None)
    assert jobstore.snapshot(JOB, "owner")["phase"] == main._AWAITING_NOTES
    return session


def _split(notes="", job=JOB):
    return TestClient(main.app).post(f"/api/bulk/notes/{job}/split",
                                     json={"notes": notes})


def _status():
    return jobstore.snapshot(JOB, "owner")


def _names_of(rows):
    return [[p.rsplit("/", 1)[-1] for p in r["photos"]] for r in rows]


def test_the_pile_becomes_a_batch_sorted_into_its_items(two_items, refunds):
    session = _paused_upload()
    one = _status()["pending_items"]
    assert len(one) == 1 and one[0]["photo_count"] == 4

    resp = _split()

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "job_id": JOB}
    job = _status()
    # Paused again, on two items this time, with each item's own photos --
    # served from the session, which is the pile the batch drafts out of.
    assert job["phase"] == main._AWAITING_NOTES
    assert job["done"] is False and job["split_error"] is None
    rows = job["pending_items"]
    assert [r["name"] for r in rows] == ["a blue polo", "a champagne bottle"]
    assert [r["gi"] for r in rows] == [0, 1]
    assert _names_of(rows) == [["img_000.jpg", "img_001.jpg"],
                               ["img_002.jpg", "img_003.jpg"]]
    assert all(p.startswith(f"/media/{session}/optimized/")
               for r in rows for p in r["photos"])
    assert job["total_items"] == 2 and job["total_photos"] == 4
    # A batch now, in everything that tells one apart from a single upload.
    seen = jobstore.internal(JOB, "owner")
    assert seen["kind"] is None
    assert seen["_staging_id"] == session
    assert seen["_names"] == ["img_000.jpg", "img_001.jpg", "img_002.jpg",
                              "img_003.jpg"]
    assert [g["indices"] for g in seen["_groups"]] == [[0, 1], [2, 3]]
    # The one draft the upload paid for up front is given back: each item is
    # charged for as it is drafted, like any batch.
    assert refunds == [RECEIPT]
    assert seen["_refunds"] == []


def test_the_answer_then_drafts_each_item_from_its_own_photos(two_items,
                                                              monkeypatch):
    session = _paused_upload()
    assert _split().status_code == 200
    runs = []
    monkeypatch.setattr(main, "_run_bulk_job",
                        lambda *a, **k: runs.append((a, k)))

    resp = TestClient(main.app).post(
        f"/api/bulk/notes/{JOB}", json={"notes": {"1": "the bottle is empty"}})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "items": 2}
    (job_id, staging, _strip, uid), kw = runs[0]
    # Drafted out of the session the photos are already in, from the plan
    # the sort wrote, with what the seller typed under each item.
    assert job_id == JOB and staging == session and uid == "owner"
    assert kw["resumed"] is True
    assert kw["resume_from"]["remaining"] == [0, 1]
    assert [g["name"] for g in kw["resume_from"]["groups"]] == [
        "a blue polo", "a champagne bottle"]
    assert kw["item_notes"] == {1: "the bottle is empty"}


def test_what_was_typed_is_read_as_the_inventory_of_the_pile(two_items):
    asked = two_items
    session = _paused_upload(notes="bought at an estate sale")

    assert _split(notes="a blue polo, a champagne bottle").status_code == 200

    # The uploader's own notes and the line typed at the question, together:
    # the one hint grouping is worth the most to.
    assert asked == ["bought at an estate sale, a blue polo, a champagne bottle"]
    # ...and saved to the pile, so every item drafted out of it inherits both
    # (a Start over months later must not forget them either).
    assert storage.load_notes(session) == (
        "bought at an estate sale, a blue polo, a champagne bottle")


def test_the_batch_tools_apply_once_sorted(two_items):
    _paused_upload()
    assert _split().status_code == 200
    rows = _status()["pending_items"]

    # The grouping was wrong after all: fold the two back into one.
    resp = TestClient(main.app).post(
        f"/api/bulk/notes/{JOB}/merge-items",
        json={"items": [{"gi": r["gi"], "photo": r["photos"][0]} for r in rows]})

    assert resp.status_code == 200, resp.text
    after = _status()["pending_items"]
    assert len(after) == 1 and after[0]["photo_count"] == 4


def test_a_sort_that_still_sees_one_item_puts_the_question_back(monkeypatch,
                                                                refunds):
    _sorted_by(monkeypatch, [{"name": "a polo", "indices": [0, 1, 2, 3]}])
    _paused_upload()
    before = _status()["pending_items"]

    resp = _split()

    assert resp.status_code == 200, resp.text
    job = _status()
    assert job["phase"] == main._AWAITING_NOTES and job["done"] is False
    # The same one item, with why nothing changed beside it.
    assert job["pending_items"] == before
    assert job["split_error"] == main._STILL_ONE_ITEM
    seen = jobstore.internal(JOB, "owner")
    assert seen["kind"] == "pipeline"
    assert "_staging_id" not in seen
    # Still one draft, still paid for: nothing was given back.
    assert refunds == [] and seen["_refunds"] == [RECEIPT]


def test_after_that_the_answer_still_drafts_the_one_item(monkeypatch):
    _sorted_by(monkeypatch, [{"name": "a polo", "indices": [0, 1, 2, 3]}])
    session = _paused_upload()
    assert _split().status_code == 200
    drafted = []
    monkeypatch.setattr(main, "_run_identify_job",
                        lambda *a, **k: drafted.append(a))

    resp = TestClient(main.app).post(f"/api/bulk/notes/{JOB}",
                                     json={"notes": {"0": "it's a polo"}})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "items": 1}
    assert drafted == [(JOB, session, "owner", RECEIPT)]
    assert storage.load_item_notes(session) == "it's a polo"


def test_a_sort_that_fails_puts_the_question_back(monkeypatch, refunds):
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")

    def broken(thumbs, notes=""):
        raise RuntimeError("model fell over")

    monkeypatch.setattr(main.claude_ai, "group_photos", broken)
    _paused_upload()
    before = _status()["pending_items"]

    assert _split().status_code == 200
    job = _status()
    assert job["phase"] == main._AWAITING_NOTES and job["done"] is False
    assert job["pending_items"] == before
    assert job["split_error"].startswith("Couldn't sort these photos just now")
    # The model's complaint is not in the sentence; a reference is.
    assert "fell over" not in job["split_error"]
    assert refunds == []


def test_not_on_a_batch_that_is_already_sorted(two_items):
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging), 4)
    jobstore.register("job-b", {"id": "job-b", "phase": "uploading",
                                "done": False, "error": None, "items": [],
                                "_staging_id": staging}, uid="owner")
    main._run_bulk_job("job-b", staging, False, "owner")
    assert jobstore.snapshot("job-b", "owner")["phase"] == main._AWAITING_NOTES

    resp = _split(job="job-b")

    assert resp.status_code == 400
    assert len(jobstore.snapshot("job-b", "owner")["pending_items"]) == 2


def test_not_once_the_upload_has_moved_on(two_items):
    _paused_upload()
    jobstore.update(JOB, phase=main._DRAFTING_PHASE)

    assert _split().status_code == 409
    assert jobstore.internal(JOB, "owner")["kind"] == "pipeline"


def test_one_photo_is_one_item(two_items):
    _paused_upload(photos=1)

    resp = _split()

    assert resp.status_code == 400
    assert _status()["phase"] == main._AWAITING_NOTES


def test_someone_elses_upload_is_a_404(two_items, monkeypatch):
    _paused_upload()
    monkeypatch.setattr(main.deps, "uid", lambda request: "stranger")

    assert _split().status_code == 404
    assert jobstore.internal(JOB, "owner")["kind"] == "pipeline"


def test_the_sorted_pile_survives_a_restart_while_waiting(two_items):
    session = _paused_upload()
    assert _split().status_code == 200
    rows = _status()["pending_items"]
    mirror = jobstore._record(jobstore._JOBS[JOB])

    jobstore.reset()
    assert main._resume_interrupted_batches([mirror]) == {JOB}

    job = _status()
    assert job["phase"] == main._AWAITING_NOTES
    assert job["pending_items"] == rows
    assert jobstore.internal(JOB, "owner")["_staging_id"] == session

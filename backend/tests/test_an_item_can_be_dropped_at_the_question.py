"""Removing a whole item from a batch at the guidance step.

The photo delete's sibling (see test_a_bad_shot_can_be_dropped_at_the_question):
the pile is sorted and nothing has been drafted, so an item the seller does not
want listed — or a group the model made out of the tablecloth — is free to
drop here, and costs a draft to drop anywhere later. It must cost nothing it
was not already costing: the batch stays paused, the other items keep exactly
their own photos, and a tap aimed at a slot another removal renumbered is
refused rather than landing on a different item.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.services import jobstore  # noqa: E402


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
                             "still deciding which items to keep")

    monkeypatch.setattr(main.claude_ai, "identify", never)


@pytest.fixture
def split_in_three(monkeypatch):
    """Photos 0-1, 2-3 and 4-5 are three items."""
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos", lambda thumbs, **kw: {
        "groups": [{"name": "coasters", "indices": [0, 1]},
                   {"name": "a blue decanter", "indices": [2, 3]},
                   {"name": "a doll head cup", "indices": [4, 5]}]})


def _paused_batch(job="job-1", photos=6):
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging), photos)
    jobstore.register(job, {"id": job, "phase": "uploading", "done": False,
                            "error": None, "items": [],
                            "_staging_id": staging}, uid="owner")
    main._run_bulk_job(job, staging, False, "owner")
    return staging, job


def _asked(job="job-1"):
    return jobstore.snapshot(job, "owner")["pending_items"]


def _remove(gi, photo, job="job-1"):
    return TestClient(main.app).post(f"/api/bulk/notes/{job}/delete-item",
                                     json={"gi": gi, "photo": photo})


def test_the_item_leaves_and_the_rest_keep_their_own_photos(split_in_three):
    staging, job = _paused_batch()
    before = _asked()

    resp = _remove(0, before[0]["photos"][0])

    assert resp.status_code == 200, resp.text
    after = _asked()
    assert [r["name"] for r in after] == ["a blue decanter", "a doll head cup"]
    assert [r["gi"] for r in after] == [0, 1]
    # The slot moved; the identity the client keys its notes by did not.
    assert [r["key"] for r in after] == [1, 2]
    # Renumbered, and still pointing at the same shots.
    assert after[0]["photos"] == before[1]["photos"]
    assert after[1]["photos"] == before[2]["photos"]
    assert resp.json()["pending_items"] == after
    seen = jobstore.internal(job, "owner")
    assert seen["phase"] == main._AWAITING_NOTES
    assert seen["total_items"] == 2 and seen["total_photos"] == 4
    # And its photos are out of the pile, not just out of the list.
    gone = [p.rsplit("/", 1)[1] for p in before[0]["photos"]]
    assert not any((storage.optimized_path(staging) / n).exists() for n in gone)


def test_a_stale_slot_is_refused_rather_than_hitting_its_neighbour(
        split_in_three):
    staging, job = _paused_batch()
    before = _asked()
    assert _remove(0, before[0]["photos"][0]).status_code == 200

    # A second tab still showing the old list taps the decanter at slot 1 —
    # which is now the doll head cup.
    resp = _remove(1, before[1]["photos"][0])

    assert resp.status_code == 409
    assert [r["name"] for r in _asked()] == ["a blue decanter",
                                             "a doll head cup"]


def test_the_last_item_stays(split_in_three):
    _paused_batch()
    assert _remove(0, _asked()[0]["photos"][0]).status_code == 200
    assert _remove(0, _asked()[0]["photos"][0]).status_code == 200

    resp = _remove(0, _asked()[0]["photos"][0])

    assert resp.status_code == 400
    assert [r["name"] for r in _asked()] == ["a doll head cup"]


def test_a_photo_another_item_shares_stays_with_it(monkeypatch):
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos", lambda thumbs, **kw: {
        "groups": [{"name": "a", "indices": [0, 1]},
                   {"name": "b", "indices": [1, 2]}]})
    _paused_batch(photos=3)
    before = _asked()

    assert _remove(0, before[0]["photos"][0]).status_code == 200

    assert _asked()[0]["photos"] == before[1]["photos"]


def test_the_answer_after_a_removal_drafts_only_what_is_left(
        split_in_three, monkeypatch):
    staging, job = _paused_batch()
    assert _remove(1, _asked()[1]["photos"][0]).status_code == 200
    plan = main._drafting_plan(jobstore.internal(job, "owner"), staging)

    assert [g["name"] for g in plan["groups"]] == ["coasters",
                                                   "a doll head cup"]
    assert plan["remaining"] == [0, 1]

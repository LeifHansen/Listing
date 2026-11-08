"""Merging items of a batch at the guidance step.

The grouping is the model's guess, and the one it keeps getting wrong is the
split: one item shot from several angles comes back as two or three. The
seller can see that at a glance on the sorted pile, before anything has been
drafted -- so folding them back together here must cost nothing: the batch
stays paused, the merged item gets every photo of the ones folded in (once
each, overview first), it keeps its place and its identity, and a tap aimed at
slots another edit has renumbered is refused rather than merging whatever slid
into them.
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
                             "still sorting out which photos are one item")

    monkeypatch.setattr(main.claude_ai, "identify", never)


def _grouped(monkeypatch, groups):
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos",
                        lambda thumbs, **kw: {"groups": groups})


@pytest.fixture
def split_in_three(monkeypatch):
    """Photos 0-1, 2-3 and 4-5 are three items."""
    _grouped(monkeypatch, [{"name": "coasters", "indices": [0, 1]},
                           {"name": "a blue decanter", "indices": [2, 3]},
                           {"name": "a doll head cup", "indices": [4, 5]}])


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


def _merge(*rows, job="job-1"):
    """Merge the given rows, each named by its slot and one of its photos —
    the way the client sends them."""
    return TestClient(main.app).post(
        f"/api/bulk/notes/{job}/merge-items",
        json={"items": [{"gi": r["gi"], "photo": r["photos"][0]} for r in rows]})


def _remove(gi, photo, job="job-1"):
    return TestClient(main.app).post(f"/api/bulk/notes/{job}/delete-item",
                                     json={"gi": gi, "photo": photo})


def test_two_items_become_one_with_both_sets_of_photos(split_in_three):
    staging, job = _paused_batch()
    before = _asked()

    resp = _merge(before[0], before[1])

    assert resp.status_code == 200, resp.text
    after = _asked()
    assert [r["name"] for r in after] == ["coasters", "a doll head cup"]
    assert [r["gi"] for r in after] == [0, 1]
    # The merged item keeps the first slot's identity, so the notes the
    # client holds against it stay with it; the one after keeps its own.
    assert [r["key"] for r in after] == [0, 2]
    assert after[0]["photos"] == before[0]["photos"] + before[1]["photos"]
    assert after[0]["photo_count"] == 4
    assert after[1]["photos"] == before[2]["photos"]
    assert resp.json()["pending_items"] == after
    seen = jobstore.internal(job, "owner")
    assert seen["phase"] == main._AWAITING_NOTES
    assert seen["total_items"] == 2
    # Nothing left the pile: a merge only changes which item a photo is in.
    assert seen["total_photos"] == 6
    assert len(seen["_names"]) == 6
    for row in before:
        for p in row["photos"]:
            assert (storage.optimized_path(staging) / p.rsplit("/", 1)[1]).exists()


def test_items_that_are_not_neighbours_merge_and_the_middle_one_stays(
        split_in_three):
    _paused_batch()
    before = _asked()

    assert _merge(before[0], before[2]).status_code == 200

    after = _asked()
    assert [r["name"] for r in after] == ["coasters", "a blue decanter"]
    assert after[0]["photos"] == before[0]["photos"] + before[2]["photos"]
    assert after[1]["photos"] == before[1]["photos"]
    assert [r["key"] for r in after] == [0, 1]


def test_one_bell_split_three_ways_is_one_bell(monkeypatch):
    _grouped(monkeypatch, [{"name": "cloisonne bell #1", "indices": [0, 1]},
                           {"name": "cloisonne bell #2", "indices": [2, 3]},
                           {"name": "cloisonne bell #3", "indices": [4, 5]}])
    _paused_batch()
    before = _asked()

    assert _merge(*before).status_code == 200

    after = _asked()
    assert len(after) == 1
    # A number in the name read as if someone had counted three bells.
    assert after[0]["name"] == "cloisonne bell"
    assert after[0]["photo_count"] == 6


def test_the_overview_leads_and_names_the_item(monkeypatch):
    """The model can list a close-up's group before the overview's. The pile
    order is the seller's shooting order, so the merged item opens on the
    first shot and is named after the group that holds it."""
    _grouped(monkeypatch, [{"name": "hallmark close-up", "indices": [2, 3]},
                           {"name": "silver teapot", "indices": [0, 1]}])
    staging, _ = _paused_batch(photos=4)
    before = _asked()

    assert _merge(before[0], before[1]).status_code == 200

    after = _asked()
    assert after[0]["name"] == "silver teapot"
    assert after[0]["photos"] == before[1]["photos"] + before[0]["photos"]


def test_a_photo_both_items_shared_is_in_the_merged_one_once(monkeypatch):
    _grouped(monkeypatch, [{"name": "a", "indices": [0, 1]},
                           {"name": "b", "indices": [1, 2]}])
    _paused_batch(photos=3)
    before = _asked()

    assert _merge(before[0], before[1]).status_code == 200

    after = _asked()
    assert after[0]["photo_count"] == 3
    assert len(set(after[0]["photos"])) == 3


def test_a_stale_slot_is_refused_rather_than_merging_its_neighbour(
        split_in_three):
    _paused_batch()
    before = _asked()
    assert _remove(0, before[0]["photos"][0]).status_code == 200

    # A second tab still showing the old list merges the decanter and the
    # cup at slots 1 and 2 -- but slot 2 is gone and slot 1 is the cup now.
    resp = _merge(before[1], before[2])

    assert resp.status_code == 409
    assert [r["name"] for r in _asked()] == ["a blue decanter",
                                             "a doll head cup"]


def test_a_photo_from_another_item_is_not_proof(split_in_three):
    _paused_batch()
    before = _asked()

    resp = TestClient(main.app).post(
        "/api/bulk/notes/job-1/merge-items",
        json={"items": [{"gi": 0, "photo": before[0]["photos"][0]},
                        {"gi": 1, "photo": before[2]["photos"][0]}]})

    assert resp.status_code == 409
    assert len(_asked()) == 3


def test_it_takes_two_distinct_items(split_in_three):
    _paused_batch()
    before = _asked()

    assert _merge(before[0]).status_code == 400
    assert _merge(before[1], before[1]).status_code == 400
    assert len(_asked()) == 3


def test_not_once_the_batch_has_moved_on(split_in_three):
    _, job = _paused_batch()
    before = _asked()
    jobstore.update(job, phase=main._DRAFTING_PHASE)

    resp = _merge(before[0], before[1])

    assert resp.status_code == 409
    assert len(jobstore.internal(job, "owner")["_groups"]) == 3


def test_not_on_a_single_upload():
    jobstore.register("job-p", {"id": "job-p", "phase": main._AWAITING_NOTES,
                                "done": False, "error": None, "items": [],
                                "kind": "pipeline"}, uid="owner")

    resp = TestClient(main.app).post(
        "/api/bulk/notes/job-p/merge-items",
        json={"items": [{"gi": 0, "photo": "/media/x/optimized/a.jpg"},
                        {"gi": 1, "photo": "/media/x/optimized/b.jpg"}]})

    assert resp.status_code == 400


def test_the_answer_after_a_merge_drafts_one_item_from_all_its_photos(
        split_in_three):
    staging, job = _paused_batch()
    before = _asked()
    assert _merge(before[1], before[2]).status_code == 200
    plan = main._drafting_plan(jobstore.internal(job, "owner"), staging)

    assert [g["name"] for g in plan["groups"]] == ["coasters",
                                                   "a blue decanter"]
    assert plan["groups"][1]["indices"] == [2, 3, 4, 5]
    assert plan["remaining"] == [0, 1]


def test_a_merge_survives_a_restart_while_waiting(split_in_three):
    staging, job = _paused_batch()
    before = _asked()
    assert _merge(before[0], before[1]).status_code == 200
    mirror = jobstore._record(jobstore._JOBS[job])

    jobstore.reset()
    assert main._resume_interrupted_batches([mirror]) == {job}

    after = _asked()
    assert [r["name"] for r in after] == ["coasters", "a doll head cup"]
    assert [r["key"] for r in after] == [0, 2]
    assert after[0]["photo_count"] == 4

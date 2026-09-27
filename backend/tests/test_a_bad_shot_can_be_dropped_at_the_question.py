"""Deleting a photo at the guidance step, before anything has been drafted.

The question the batch stops to ask is asked WITH the photos — the seller is
looking at the exact shots the AI is about to read, optimized and already
sorted into items. Which makes it the one moment a bad one is free to drop:
the blurred shot, the one of the floor, the receipt that got swept up with the
pile. Ten seconds later it has cost a draft that had to look at it and an edit
to undo, and there is no reason for the seller to pay that when the fix is a
tap on a thumbnail they are already looking at.

What that has to cost, in turn, is nothing it was not already costing:

  * the job stays PAUSED and unanswered. A delete changes what the question is
    about; it does not answer it, and it must not start the drafting run;
  * the pile and the grouping stay in step. A batch's indices point from its
    groups into its photo list, and a delete that renumbered one without the
    other would hand item 2 item 3's photos — which is worse than the bad
    shot, because the seller would not see it until the drafts landed;
  * an item keeps at least one photo. An item with none is drafted from an
    empty directory: an identify charged for looking at nothing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.services import jobstore  # noqa: E402


def _client() -> "TestClient":
    """A client that does NOT boot the app — see the sibling file for why
    (the lifespan starts a log thread that outlives this module's tests)."""
    return TestClient(main.app)


def _photos(dir_, n, prefix="src"):
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", (60, 60), (240, 240, 240)).save(
            dir_ / f"{prefix}_{i:03d}.jpg", "JPEG")


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


@pytest.fixture
def refuses_to_draft(monkeypatch):
    """The AI, wired to fail the test if it is ever reached. A delete that
    accidentally answered the question would otherwise look exactly like one
    that did not."""
    def never(*a, **k):
        raise AssertionError("the AI was asked to draft while the seller was "
                             "still deciding which photos to keep")

    monkeypatch.setattr(main.claude_ai, "identify", never)
    return never


@pytest.fixture
def split_in_two(monkeypatch):
    """Grouping, stubbed: photos 0-1 are one item, 2-3 the other."""
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos", lambda thumbs, **kw: {
        "groups": [{"name": "a blue polo", "indices": [0, 1]},
                   {"name": "a lacoste polo", "indices": [2, 3]}]})


def _paused_batch(uid="owner", job="job-1", photos=4):
    """A real batch run to the pause, with the AI's grouping stubbed."""
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging), photos)
    jobstore.register(job, {"id": job, "phase": "uploading", "done": False,
                            "error": None, "items": [],
                            "_staging_id": staging}, uid=uid)
    main._run_bulk_job(job, staging, False, uid)
    return staging, job


def _paused_upload(uid="owner", job="job-p", photos=3):
    """A single upload run to the same pause. One item, one session, and no
    grouping at all — the item IS the session's optimized directory."""
    session = storage.new_session_id()
    _photos(storage.original_dir(session), photos)
    jobstore.register(job, {"id": job, "kind": "pipeline", "phase": "optimizing",
                            "done": False, "error": None, "result": None,
                            "_session_id": session}, uid=uid)
    main._run_pipeline_job(job, session, uid, False, None, None)
    return session, job


def _asked(job="job-1"):
    return jobstore.snapshot(job, "owner")["pending_items"]


def _drop(client, photo, gi=0, job="job-1"):
    return client.post(f"/api/bulk/notes/{job}/delete-photo",
                       json={"gi": gi, "photo": photo})


# --- a bulk pile ------------------------------------------------------------

def test_the_photo_leaves_the_item_it_was_shown_under(split_in_two,
                                                      refuses_to_draft):
    staging, job = _paused_batch()
    before = _asked()
    assert len(before[0]["photos"]) == 2

    resp = _drop(_client(), before[0]["photos"][1])

    assert resp.status_code == 200, resp.text
    after = _asked()
    assert after[0]["photos"] == [before[0]["photos"][0]]
    assert after[0]["photo_count"] == 1
    # And the answer says so itself, so a client that deleted a photo does not
    # have to wait for its next poll to believe it.
    assert resp.json()["pending_items"] == after


def test_the_other_items_keep_their_own_photos(split_in_two, refuses_to_draft):
    """The one that would go wrong quietly. A batch's groups point INTO its
    photo list by index, so dropping a photo the second item's indices sit
    after is the moment those indices stop meaning what they say — and the
    seller would find out from a draft of the wrong polo."""
    staging, job = _paused_batch()
    before = _asked()
    mine = list(before[1]["photos"])

    assert _drop(_client(), before[0]["photos"][0]).status_code == 200

    after = _asked()
    assert after[1]["photos"] == mine, (
        "the second item's photos moved when the first item lost one")
    assert after[1]["name"] == "a lacoste polo"
    # Same claim, read off the grouping the drafting run actually copies from.
    job_now = jobstore.internal(job, "owner")
    names, groups = job_now["_names"], job_now["_groups"]
    assert len(names) == 3
    assert [names[i] for i in groups[1]["indices"]] == [
        url.rsplit("/", 1)[-1] for url in mine]


def test_the_batch_is_still_waiting_for_its_answer(split_in_two,
                                                   refuses_to_draft):
    """A delete says what the question is about. It does not answer it, and a
    job that started drafting here would draft from photos the seller was
    still in the middle of pruning."""
    _staging, job = _paused_batch()

    assert _drop(_client(), _asked()[0]["photos"][0]).status_code == 200

    snap = jobstore.snapshot(job, "owner")
    assert snap["phase"] == "awaiting_notes"
    assert snap["done"] is False and snap.get("error") is None
    assert not snap["items"], "nothing is drafted before the seller answers"


def test_the_pile_loses_the_file_too(split_in_two, refuses_to_draft):
    """Not tidiness: the pile is what the drafting run copies out of, and a
    file no group points at any more is a file nothing will ever read."""
    staging, _job = _paused_batch()
    gone = _asked()[0]["photos"][0].rsplit("/", 1)[-1]

    assert _drop(_client(), _asked()[0]["photos"][0]).status_code == 200

    left = storage.list_optimized(staging)
    assert gone not in left
    assert len(left) == 3, "it took more than the one photo it was given"


def test_the_last_photo_on_an_item_stays(split_in_two, refuses_to_draft):
    """An item with no photos is drafted from an empty directory — an identify
    charged for looking at nothing. The client greys the button out for the
    same reason; this is the half that cannot be bypassed."""
    staging, job = _paused_batch()
    client = _client()
    assert _drop(client, _asked()[0]["photos"][1]).status_code == 200

    resp = _drop(client, _asked()[0]["photos"][0])

    assert resp.status_code == 400, resp.text
    assert "at least one photo" in resp.json()["detail"]
    assert len(_asked()[0]["photos"]) == 1, "it deleted it anyway"
    assert len(storage.list_optimized(staging)) == 3


def test_an_item_is_never_emptied_by_a_delete_on_another_one(monkeypatch,
                                                             refuses_to_draft):
    """The grouping is the model's, and nothing stops it putting one photo
    under two items. Dropping that photo from the item with three shots would
    then empty the item it was the only shot of — silently, and the batch
    would draft it from an empty directory."""
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos", lambda thumbs, **kw: {
        "groups": [{"name": "three shots", "indices": [0, 1, 2]},
                   {"name": "the same one again", "indices": [2]}]})
    _staging, job = _paused_batch()
    shared = _asked()[1]["photos"][0]

    resp = _drop(_client(), shared, gi=0)

    assert resp.status_code == 400, resp.text
    assert "at least one photo" in resp.json()["detail"]
    assert [len(it["photos"]) for it in _asked()] == [3, 1], "it emptied one"


def test_a_photo_that_is_not_that_items_is_refused(split_in_two,
                                                   refuses_to_draft):
    """The photo is checked against the item's OWN row rather than trusted as
    a path. A stale tab naming item 2's photo under item 1 is the ordinary
    case, and the deeper point is that nothing else on the volume can be
    named here at all."""
    _staging, job = _paused_batch()
    client = _client()
    someone_elses = _asked()[1]["photos"][0]

    wrong_item = _drop(client, someone_elses, gi=0)
    made_up = _drop(client, "/media/../../etc/passwd", gi=0)
    no_such_item = _drop(client, _asked()[0]["photos"][0], gi=9)

    assert wrong_item.status_code == 409, wrong_item.text
    assert made_up.status_code == 409, made_up.text
    assert no_such_item.status_code == 409, no_such_item.text
    assert [len(it["photos"]) for it in _asked()] == [2, 2]


def test_a_delete_for_a_job_that_has_moved_on_is_refused(monkeypatch):
    """The seller answered in another tab while this one still had the
    question up. The drafting run is already reading the grouping this would
    rewrite."""
    jobstore.register("job-2", {"id": "job-2", "phase": "identifying",
                                "done": False}, uid="owner")

    resp = _drop(_client(), "/media/s/optimized/img_000.jpg", job="job-2")

    assert resp.status_code == 409, resp.text
    assert "moved on" in resp.json()["detail"]


def test_a_delete_on_someone_elses_job_is_a_404(monkeypatch):
    """Same answer as an id that never existed, so this cannot be used to ask
    whether somebody else's batch is real."""
    monkeypatch.setattr(main.deps, "uid", lambda request: "someone-else")
    jobstore.register("job-3", {"id": "job-3", "phase": "awaiting_notes",
                                "done": False, "_staging_id": "s",
                                "_names": ["img_000.jpg", "img_001.jpg"],
                                "_groups": [{"name": "a", "indices": [0, 1]}]},
                      uid="owner")
    client = _client()

    mine = _drop(client, "/media/s/optimized/img_000.jpg", job="job-3")
    never_existed = _drop(client, "/media/s/optimized/img_000.jpg",
                          job="no-such-job")

    assert mine.status_code == 404
    assert never_existed.status_code == 404
    assert mine.json() == never_existed.json()


def test_the_answer_drafts_from_what_is_left(split_in_two, monkeypatch):
    """The whole point, end to end: the photo the seller dropped is not one of
    the photos their item is drafted from."""
    staging, job = _paused_batch()
    dropped = _asked()[0]["photos"][1]
    assert _drop(_client(), dropped).status_code == 200

    seen = []
    monkeypatch.setattr(main, "_run_bulk_job",
                        lambda *a, **k: seen.append(k.get("resume_from")))
    resp = _client().post(f"/api/bulk/notes/{job}", json={"notes": {}})

    assert resp.status_code == 200, resp.text
    plan = seen[0]
    assert dropped.rsplit("/", 1)[-1] not in plan["names"]
    assert [len(g["indices"]) for g in plan["groups"]] == [1, 2]


def test_a_dropped_photo_does_not_come_back_after_a_restart(split_in_two,
                                                           refuses_to_draft):
    """The pause is open-ended, so a deploy in the middle of it is the
    ordinary case. The question is rebuilt from the grouping on the way back
    up — so the delete has to be IN the grouping, not only in the status it
    published."""
    staging, job = _paused_batch()
    gone = _asked()[0]["photos"][1]
    assert _drop(_client(), gone).status_code == 200
    mirror = jobstore._record(jobstore._JOBS[job])

    jobstore.reset()
    assert main._resume_interrupted_batches([mirror]) == {job}

    back = _asked()
    assert gone not in back[0]["photos"]
    assert [len(it["photos"]) for it in back] == [1, 2]
    assert jobstore.snapshot(job, "owner")["total_photos"] == 3


# --- one listing ------------------------------------------------------------

def test_a_single_upload_drops_the_file_itself(refuses_to_draft):
    """No grouping to edit here: the identify chain reads the session's
    optimized directory, and re-reads it months later on a "Start over". A
    photo left on the disk is a photo that comes back."""
    session, job = _paused_upload()
    before = _asked(job)[0]["photos"]
    assert len(before) == 3

    resp = _drop(_client(), before[0], job=job)

    assert resp.status_code == 200, resp.text
    left = storage.list_optimized(session)
    assert before[0].rsplit("/", 1)[-1] not in left
    assert len(left) == 2
    assert _asked(job)[0]["photos"] == before[1:]
    assert jobstore.snapshot(job, "owner")["phase"] == "awaiting_notes"


def test_a_single_upload_keeps_its_last_photo(refuses_to_draft):
    """There is no listing at all without one, and the seller is one tap from
    having uploaded nothing."""
    session, job = _paused_upload(photos=1)

    resp = _drop(_client(), _asked(job)[0]["photos"][0], job=job)

    assert resp.status_code == 400, resp.text
    assert len(storage.list_optimized(session)) == 1

"""A batch waiting on the seller has to survive a deploy.

This is the reason the guidance step does not block a worker. A person asked a
question takes as long as a person takes — lunch, a phone call, tomorrow — and
this app runs on one machine that is replaced on every deploy. A pause held in
a thread would be lost every time, and what it would take with it is a whole
pile of optimized photos and the seller's place in their own work.

So the pause is a STATE, not a wait: the worker writes the grouping down and
returns. Which makes the restart story small, and makes these the tests that
matter —

  * a boot puts the question back exactly as the seller left it, and starts no
    worker, because there is nothing to run until they answer;
  * it does not count against the batch's resume budget, because nothing
    failed — a seller who waits out three deploys still gets asked;
  * a deploy AFTER they answer keeps their answer, because the drafting run it
    picks back up is the one that needs it;
  * and a pile that went away while it waited ends honestly, saying nothing
    was drafted and nothing charged, rather than pointing at empty Drafts.
"""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.services import jobstore  # noqa: E402


def _client() -> "TestClient":
    """A client that does NOT boot the app, deliberately.

    `with TestClient(main.app)` enters the lifespan, which adopts job
    mirrors, runs the resume pass and starts the error-log writer thread —
    and that thread is never stopped, so for the rest of the pytest process
    it drains the queue other tests are about to flush() and asserts start
    failing in files that never touched this one. These tests want one route
    answered, not a boot.
    """
    return TestClient(main.app)


NAMES = ["img_000.jpg", "img_001.jpg", "img_002.jpg", "img_003.jpg"]
GROUPS = [{"name": "a blue polo", "indices": [0, 1]},
          {"name": "a lacoste polo", "indices": [2, 3]}]
BLUE = "men's L, small mark on the left cuff"


def _pile(staging):
    """The optimized pile a paused batch left on the volume."""
    d = storage.optimized_dir(staging)
    d.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        Image.new("RGB", (60, 60), (240, 240, 240)).save(d / name, "JPEG")
    return staging


@pytest.fixture(autouse=True)
def _no_job_outlives_its_test(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    jobstore.reset()
    yield
    for job_id in list(jobstore._JOBS):
        jobstore.update(job_id, done=True)
    jobstore.reset()


@pytest.fixture
def workers(monkeypatch):
    """Every worker the boot decides to start, without running one."""
    started = []
    ran = threading.Event()

    def _fake(job_id, staging_id, strip_bg, uid, resumed=False,
              resume_from=None, item_notes=None):
        started.append({"job_id": job_id, "staging_id": staging_id,
                        "resumed": resumed, "resume_from": resume_from,
                        "item_notes": item_notes})
        ran.set()

    monkeypatch.setattr(main, "_run_bulk_job", _fake)
    return started, ran


def _mirror(staging, **extra):
    """The mirror a batch paused at the guidance step leaves behind."""
    return {"id": "job-1", "phase": "awaiting_notes", "done": False,
            "error": None, "current": 0, "total_items": len(GROUPS),
            "total_photos": len(NAMES), "_uid": "owner",
            "_staging_id": staging, "_strip_bg": False, "_resumes": 0,
            "_names": NAMES, "_groups": GROUPS, "_done": [], "_inflight": [],
            **extra}


# --- the question comes back ------------------------------------------------

def test_the_boot_puts_the_question_back(workers):
    started, _ran = workers
    staging = _pile(storage.new_session_id())

    picked = main._resume_interrupted_batches([_mirror(staging)])

    assert picked == {"job-1"}, (
        "the batch was left for dead, so the seller's photos and their place "
        "in the pile went with it")
    snap = jobstore.snapshot("job-1", "owner")
    assert snap["phase"] == "awaiting_notes" and snap["done"] is False
    # The same question, rebuilt from the grouping rather than mirrored —
    # `pending_items` is derived from it, so there is one copy of the truth.
    assert [it["name"] for it in snap["pending_items"]] == [
        "a blue polo", "a lacoste polo"]
    assert [it["gi"] for it in snap["pending_items"]] == [0, 1]
    for item in snap["pending_items"]:
        assert len(item["photos"]) == 2
        assert item["photos"][0].startswith(f"/media/{staging}/optimized/")
    assert started == [], (
        "a batch waiting on a person has nothing to run — a worker started "
        "here would draft the pile the seller was still describing")


def test_waiting_does_not_count_against_the_resume_budget(workers):
    """A batch that dies mid-photo-pass twice is hitting something it will
    keep hitting, and the boot eventually leaves it alone. Waiting is not
    that: nothing failed, and a seller who takes three deploys' worth of time
    to answer must not be punished for it."""
    staging = _pile(storage.new_session_id())

    assert main._resume_interrupted_batches([_mirror(staging)]) == {"job-1"}

    assert jobstore._JOBS["job-1"]["_resumes"] == 0


def test_the_seller_can_still_answer_a_re_adopted_batch(workers, monkeypatch):
    """The point of putting the question back: the answer has to work. The
    re-registered job carries the grouping and the pile, which is everything
    the drafting run is handed."""
    started, ran = workers
    staging = _pile(storage.new_session_id())
    main._resume_interrupted_batches([_mirror(staging)])
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")

    client = _client()
    resp = client.post("/api/bulk/notes/job-1",
                       json={"notes": {"0": BLUE}})

    assert resp.status_code == 200, resp.text
    assert ran.wait(5), "it accepted the answer and never drafted"
    assert started[0]["item_notes"] == {0: BLUE}
    assert started[0]["resumed"] is True, (
        "the photo work was charged for on the run that did it; this one "
        "must not charge for it again")
    assert [g["name"] for g in started[0]["resume_from"]["groups"]] == [
        "a blue polo", "a lacoste polo"]
    assert started[0]["resume_from"]["remaining"] == [0, 1]


# --- a deploy after the answer ---------------------------------------------

def test_a_deploy_mid_drafting_keeps_what_the_seller_typed(workers):
    """The other half of the window. Once the answer is in, the batch is an
    ordinary drafting batch — and the drafting resume already existed. What it
    did not have was the seller's lines, which live on the job because the
    items they belong to have not been created yet."""
    started, ran = workers
    staging = _pile(storage.new_session_id())
    record = _mirror(staging, phase="identifying",
                     _item_notes={"0": BLUE})

    assert main._resume_interrupted_batches([record]) == {"job-1"}

    assert ran.wait(5)
    assert started[0]["item_notes"] == {0: BLUE}, (
        "the batch finished without the one thing the seller stopped it to "
        "say, and the draft it produced is the one they were preventing")
    assert jobstore._JOBS["job-1"]["_item_notes"] == {"0": BLUE}, (
        "carried forward on the job too, so a SECOND deploy keeps it")


def test_an_answer_from_before_the_step_existed_is_simply_absent(workers):
    """A mirror written by the previous deploy has no `_item_notes` at all.
    That is every box left blank, which is the prompt this app sent before the
    step existed — not a crash, and not a missing key to guess at."""
    started, ran = workers
    staging = _pile(storage.new_session_id())

    main._resume_interrupted_batches([_mirror(staging, phase="identifying")])

    assert ran.wait(5)
    assert started[0]["item_notes"] == {}


# --- when there is nothing left to come back to ----------------------------

def test_a_pause_whose_pile_went_away_is_not_picked_up(workers):
    """The pause is open-ended, so the orphan sweep can reach the pile first.
    There is nothing to draft from, so the boot leaves the honest interrupted
    message rather than asking a question it cannot act on."""
    started, _ran = workers
    gone = storage.new_session_id()  # never given any photos

    assert main._resume_interrupted_batches([_mirror(gone)]) == set()
    assert started == []
    assert not storage.session_dir(gone).exists(), (
        "asking about a swept pile put its directories back")


def test_what_the_seller_is_told_when_it_cannot_come_back():
    """Nothing was drafted and nothing was charged, so the message must not
    send them to Drafts to look for work that is not there — which is what the
    catch-all wording for an unknown phase does."""
    message = jobstore.interrupted_message(_mirror("s1"))

    assert "waiting for your notes" in message
    assert "nothing was charged" in message.lower()
    assert "Drafts" not in message


def test_a_batch_the_seller_stopped_is_not_re_asked(workers):
    """A stop is an answer too. The restart is not a second chance to put a
    question the seller has already walked away from."""
    started, _ran = workers
    staging = _pile(storage.new_session_id())

    assert main._resume_interrupted_batches(
        [_mirror(staging, _cancel=True)]) == set()
    assert started == []

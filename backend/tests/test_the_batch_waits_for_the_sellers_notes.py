"""The guidance step: the pipeline stops and asks before it drafts.

Everything a draft is about to guess at — the brand on a worn-off label, which
of two near-identical polos is the large, whether the mark on the cuff is worth
disclosing — is already known to the person holding the thing. Before this
step the app never asked: it guessed, and the seller corrected it afterwards,
field by field, across every draft in the pile.

So the batch stops once the photos are optimized and split, and asks once per
item. What that costs has to be nothing:

  * nothing is drafted and no draft is charged for while it waits, so a seller
    who changes their mind at the question owes nothing;
  * the worker RETURNS rather than blocking, because a person can take hours
    and a thread cannot — which is also what makes the pause survive a deploy
    (see test_a_paused_batch_survives_a_restart.py);
  * the pile stays on the volume, because it is what the drafting run copies
    each item's photos out of. The one path that would otherwise leak it — the
    seller stopping a batch that is paused — has to drop it itself, since
    there is no worker left to.
"""
from __future__ import annotations

import time

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


@pytest.fixture
def refuses_to_draft(monkeypatch):
    """The AI, wired to fail the test if it is ever reached.

    The whole claim of this file is that nothing is identified before the
    seller answers, and an assertion about the job's status cannot prove that
    on its own — a draft that ran and was thrown away looks the same from
    outside. This does: if the pause leaks, the test says which call made it
    through rather than which field went missing.
    """
    def never(*a, **k):
        raise AssertionError("the AI was asked to draft before the seller "
                             "had been asked anything")

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


# --- a bulk pile ------------------------------------------------------------

def test_the_batch_stops_after_the_split_and_says_what_it_is_asking_about(
        split_in_two, refuses_to_draft):
    staging, job = _paused_batch()
    snap = jobstore.snapshot(job, "owner")

    assert snap["phase"] == "awaiting_notes"
    assert snap["done"] is False, (
        "a job that is waiting on a person is not a finished job — the client "
        "has to keep watching it, and the banner has to keep saying so")
    assert snap.get("error") is None
    assert not snap["items"], "nothing is drafted before the seller answers"

    asked = snap["pending_items"]
    assert [it["gi"] for it in asked] == [0, 1]
    assert [it["name"] for it in asked] == ["a blue polo", "a lacoste polo"]
    # The photos come off the staging pile, which is exactly why it may not be
    # purged at the pause: these URLs are what the question is asked WITH.
    for item in asked:
        assert len(item["photos"]) == 2
        for url in item["photos"]:
            assert url.startswith(f"/media/{staging}/optimized/")


def test_the_pile_is_still_there_to_draft_from(split_in_two, refuses_to_draft):
    """A finished batch drops its staging pile. A paused one cannot: the
    drafting run copies each item's photos out of it, and the seller may be
    away for hours."""
    staging, _job = _paused_batch()
    assert storage.list_optimized(staging), "the pile was purged mid-question"


def test_a_paused_batch_has_charged_for_no_drafts(split_in_two,
                                                  refuses_to_draft, monkeypatch):
    spent = []
    monkeypatch.setattr(main.tokens, "enabled", lambda: True)
    monkeypatch.setattr(main.tokens, "spend", lambda uid, kind, units=1: (
        spent.append(kind) or {"ok": True, "entry_id": "e1", "user_id": uid}))

    _paused_batch()

    assert spent == [], (
        "the question is free: a seller who walks away at it owes nothing")


def test_only_the_photos_it_can_show_are_counted(monkeypatch, refuses_to_draft):
    """A twelve-shot item says twelve and sends eight. The status is polled
    every 1.5s and is already the biggest body this app serves, so the row is
    capped — but a cap that quietly showed eight of twelve would read as photos
    the batch had lost."""
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos", lambda thumbs, **kw: {
        "groups": [{"name": "one big item", "indices": list(range(12))}]})

    _paused_batch(photos=12)

    item = jobstore.snapshot("job-1", "owner")["pending_items"][0]
    assert len(item["photos"]) == main._PENDING_PHOTOS_PER_ITEM
    assert item["photo_count"] == 12


def test_stopping_a_paused_batch_takes_its_photos_with_it(
        split_in_two, refuses_to_draft, monkeypatch):
    """The one way the pause could leak a pile. Every other ending runs
    through the worker's `finally`, which drops it; a batch waiting on the
    seller has no worker at all, so the request has to."""
    staging, job = _paused_batch()
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")

    client = _client()
    resp = client.post(f"/api/bulk/cancel/{job}")

    assert resp.status_code == 200, resp.text
    assert not storage.session_dir(staging).exists(), (
        "nobody is left to drop this pile, so the stop had to")


# --- one listing ------------------------------------------------------------

def test_a_single_upload_stops_at_the_same_question(refuses_to_draft):
    """One item, so one box — asked by the same phase on the same status, so
    the client has one thing to recognise rather than two."""
    session = storage.new_session_id()
    _photos(storage.original_dir(session), 2)
    job = storage.new_session_id()
    jobstore.register(job, {"id": job, "kind": "pipeline", "phase": "optimizing",
                            "done": False, "error": None, "result": None,
                            "_session_id": session}, uid="owner")

    main._run_pipeline_job(job, session, "owner", False, None, None)

    snap = jobstore.snapshot(job, "owner")
    assert snap["phase"] == "awaiting_notes" and snap["done"] is False
    asked = snap["pending_items"]
    assert len(asked) == 1 and asked[0]["gi"] == 0
    assert len(asked[0]["photos"]) == 2
    for url in asked[0]["photos"]:
        assert url.startswith(f"/media/{session}/optimized/")
    # The photo pass's own summary is published BEFORE the pause, because the
    # toasts it drives ("we turned two photos upright") belong beside the
    # photos the seller is being asked about, not after the draft lands.
    assert len(snap["upload"]["optimized"]) == 2
    assert storage.load_listing(session) is None, "it drafted before asking"


def test_the_up_front_charge_is_held_not_refunded_at_the_question(
        refuses_to_draft, monkeypatch):
    """A single upload is charged for its draft before the photos are touched,
    so a broke caller is refused up front rather than after the photo work.
    The pause must not give that back: the draft is still coming, the moment
    the seller answers."""
    refunded = []
    monkeypatch.setattr(main.tokens, "refund",
                        lambda receipt, units=None: refunded.append(receipt))
    session = storage.new_session_id()
    _photos(storage.original_dir(session), 1)
    job = storage.new_session_id()
    jobstore.register(job, {"id": job, "kind": "pipeline", "done": False,
                            "_session_id": session}, uid="owner")

    main._run_pipeline_job(job, session, "owner", False, None,
                           {"ok": True, "entry_id": "e1", "user_id": "owner"})

    assert refunded == [], "the charge was given back while the draft it paid "\
                           "for was still waiting to be written"


# --- answering a question nobody is asking ---------------------------------

def test_notes_for_a_job_that_has_moved_on_are_refused(monkeypatch):
    """A double tap, a tab left open on a batch that finished, an answer that
    crossed with a Stop. Starting a second drafting run over the same pile
    would draft — and charge for — every item twice."""
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")
    jobstore.register("job-2", {"id": "job-2", "phase": "identifying",
                                "done": False}, uid="owner")
    started = []
    monkeypatch.setattr(main, "_run_bulk_job",
                        lambda *a, **k: started.append(a))

    client = _client()
    resp = client.post("/api/bulk/notes/job-2", json={"notes": {"0": "hi"}})

    assert resp.status_code == 409, resp.text
    assert started == [], "it started a second drafting run over one pile"


def test_notes_for_someone_elses_job_are_a_404(monkeypatch):
    """Same answer as an id that never existed, so this cannot be used to ask
    whether somebody else's batch is real."""
    monkeypatch.setattr(main.deps, "uid", lambda request: "someone-else")
    jobstore.register("job-3", {"id": "job-3", "phase": "awaiting_notes",
                                "done": False}, uid="owner")

    client = _client()
    mine = client.post("/api/bulk/notes/job-3", json={"notes": {}})
    never_existed = client.post("/api/bulk/notes/no-such-job",
                                json={"notes": {}})

    assert mine.status_code == 404
    assert never_existed.status_code == 404
    assert mine.json() == never_existed.json()


def test_a_batch_whose_photos_went_away_while_it_waited_says_so(monkeypatch):
    """The pause is open-ended, so the orphan sweep can reach the pile first.
    There is nothing to draft from and nothing was ever charged, so the batch
    ends with a reason instead of a drafting run over an empty directory."""
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")
    staging = storage.new_session_id()  # never given any photos
    jobstore.register("job-4", {
        "id": "job-4", "phase": "awaiting_notes", "done": False,
        "_staging_id": staging, "_names": ["img_000.jpg"],
        "_groups": [{"name": "a", "indices": [0]}], "_done": [], "_inflight": [],
    }, uid="owner")

    client = _client()
    resp = client.post("/api/bulk/notes/job-4", json={"notes": {}})

    assert resp.status_code == 409, resp.text
    snap = jobstore.snapshot("job-4", "owner")
    assert snap["done"] is True
    assert "no longer on the server" in snap["error"], snap["error"]


def test_the_question_is_asked_once_per_batch(split_in_two, monkeypatch):
    """Answering it hands the batch to a run that drafts. That run must not
    stop and ask again — an empty answer is still an answer, and a batch that
    re-asked after every Skip could never finish."""
    drafted = []
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")
    for name in ("_resolve_category", "_assign_store_category",
                 "_enrich_listing", "_drop_answered_missing_info",
                 "_lookup_artwork", "_research_draft", "_price_against_comps",
                 "_price_against_retail"):
        monkeypatch.setattr(main, name, lambda *a, **k: None)
    monkeypatch.setattr(main, "_apply_listing_defaults",
                        lambda listing, uid, prefs=None: listing)
    monkeypatch.setattr(main, "_load_prefs", lambda uid: {})
    monkeypatch.setattr(main, "_auto_promote_enabled", lambda uid: False)
    monkeypatch.setattr(main.db, "upsert_listing", lambda *a, **k: True)
    monkeypatch.setattr(main.claude_ai, "warm_identify_cache", lambda: True)

    from backend.models import IdentifyResult, Listing

    def identify(paths, names, strategy="", notes="", item_notes=""):
        drafted.append(item_notes)
        return IdentifyResult(listing=Listing(title="drafted", price=10,
                                              images=list(names)))

    monkeypatch.setattr(main.claude_ai, "identify", identify)
    _staging, job = _paused_batch()

    client = _client()
    resp = client.post(f"/api/bulk/notes/{job}", json={"notes": {}})
    assert resp.status_code == 200, resp.text

    deadline = time.time() + 15
    while time.time() < deadline and not jobstore.snapshot(job, "owner")["done"]:
        time.sleep(0.05)
    snap = jobstore.snapshot(job, "owner")
    assert snap["done"] is True and snap.get("error") is None
    assert snap["phase"] == "done", snap["phase"]
    assert len(drafted) == 2 and drafted == ["", ""], (
        "every box left blank is the same prompt a run without the step "
        "would have sent")


def test_answering_twice_drafts_once(split_in_two, refuses_to_draft, monkeypatch):
    """The expensive race, in its everyday shape: a double tap, or a retry
    landing beside the original. Two drafting runs over one pile would draft
    and CHARGE for every item twice, and nothing afterwards would look wrong
    enough to notice — just two of everything in Drafts."""
    started = []
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")
    _staging, job = _paused_batch()
    # Stubbed only AFTER the pile is paused — _paused_batch runs the real
    # worker to get there, which is the half of this that has to be real.
    monkeypatch.setattr(main, "_run_bulk_job",
                        lambda *a, **k: started.append(k.get("item_notes")))
    client = _client()

    first = client.post(f"/api/bulk/notes/{job}", json={"notes": {"0": "a"}})
    second = client.post(f"/api/bulk/notes/{job}", json={"notes": {"0": "a"}})

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert started == [{0: "a"}], "the pile was drafted twice"


def test_the_move_out_of_the_pause_is_one_locked_step(split_in_two,
                                                      refuses_to_draft):
    """What makes the above safe under real concurrency rather than only in
    sequence: the read and the write are one step, so of two answers arriving
    at the same instant exactly one is handed the job. Tested on the primitive
    because a thread race is not something a test can reliably lose."""
    _staging, job = _paused_batch()

    first = jobstore.claim(job, "awaiting_notes", "owner", phase="identifying")
    second = jobstore.claim(job, "awaiting_notes", "owner", phase="identifying")

    assert first is not None and first["phase"] == "awaiting_notes", (
        "the claim has to hand back the job as it WAS — the grouping and the "
        "pile it names are what the drafting run is built from")
    assert second is None
    assert jobstore.snapshot(job, "owner")["phase"] == "identifying"


def test_a_claim_on_someone_elses_job_is_no_claim_at_all(split_in_two,
                                                         refuses_to_draft):
    _staging, job = _paused_batch()

    assert jobstore.claim(job, "awaiting_notes", "someone-else",
                          phase="identifying") is None
    assert jobstore.snapshot(job, "owner")["phase"] == "awaiting_notes"

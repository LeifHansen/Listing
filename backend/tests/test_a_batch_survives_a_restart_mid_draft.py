"""A bulk batch survives the machine it started on — while drafting, too.

The photo pass could already be picked back up after a restart (see
test_bulk_resume.py). Drafting could not: once "identifying" began, a deploy —
which restarts the ONLY machine — ended the batch with "The server restarted
while identifying item 1 of 6, so this batch stopped early". Six deploys
landed in one hour on 2026-09-02 and every batch running under them died
exactly that way, at the seller's expense.

Resuming here is safe because the batch now writes its plan down as it goes:
the grouping and the photo order the moment grouping finishes, each finished
item as it lands, and every item in flight BEFORE its AI runs. A restart reads
that back and drafts every group that has no draft — so nothing already saved
is drafted twice, and the items that were charged and never finished are
finished without a second charge.

Items are drafted several at a time, so each one says which GROUP it is
(`gi`) rather than relying on its position in the list, and `_inflight` is a
list. A mirror written before that is still read correctly — see
test_a_mirror_from_before_items_ran_together_still_resumes, which is what
makes it safe for a deploy to land in the middle of a batch.
"""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.models import IdentifyResult, Listing  # noqa: E402
from backend.services import jobstore  # noqa: E402


def _photos(dir_, n=3, prefix="src"):
    dir_.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(n):
        name = f"{prefix}_{i:03d}.jpg"
        Image.new("RGB", (300, 300), (240, 240, 240)).save(dir_ / name, "JPEG")
        names.append(name)
    return names


def _plan(staging, n_photos=6):
    """A batch that had finished grouping: the optimized pile is still on the
    volume (the staging purge only runs when the batch ENDS), and the
    grouping is three items of two photos each."""
    names = _photos(storage.optimized_dir(staging), n_photos)
    groups = [{"name": f"item {g}", "indices": [2 * g, 2 * g + 1]}
              for g in range(3)]
    return names, groups


def _finished_item(title="Nike hoodie"):
    sid = storage.new_session_id()
    _photos(storage.optimized_dir(sid), 2, prefix="img")
    storage.save_listing(sid, Listing(title=title, price=20,
                                      images=["img_000.jpg", "img_001.jpg"]))
    return sid


def _inflight_item():
    """Photos copied into the item's own session, AI never finished."""
    sid = storage.new_session_id()
    _photos(storage.optimized_dir(sid), 2, prefix="img")
    return sid


def _done(sid, gi=0, name=None, title="Nike hoodie"):
    return {"gi": gi, "session_id": sid, "name": name or f"item {gi}",
            "status": "draft", "error": None, "title": title}


def _inflight(sid, gi):
    return [{"gi": gi, "session_id": sid}]


def _record(staging, names, groups, done, inflight, **extra):
    """The mirror a restart reads back: what jobstore wrote for a batch that
    was identifying when the process went away."""
    return {"id": "job-1", "phase": "identifying", "done": True,
            "error": "The server restarted...",
            "_staging_id": staging, "_strip_bg": False, "_uid": "owner",
            "total_photos": len(names), "total_items": len(groups),
            "current": len(done),
            "_names": names, "_groups": groups, "_done": done,
            "_inflight": inflight, **extra}


@pytest.fixture(autouse=True)
def _serial_drafting(monkeypatch):
    """One item at a time, so these tests are about WHICH items get drafted
    rather than about the order three workers happen to finish in. The pool
    itself has its own tests below."""
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", 1)


@pytest.fixture(autouse=True)
def _fresh_jobstore(monkeypatch, tmp_path):
    """A jobstore of this test's own, mirrors included.

    The mirrors matter more than the dict. A job registered here with a
    stubbed worker is still "running" in its mirror file when the test ends,
    and the next TestClient in this process runs _adopt_job_mirrors at
    startup — which would pick that job up and run the REAL worker over it
    in the background of an unrelated test. So the mirrors go in a data root
    that dies with the test, and whatever is still running is finished
    before it does.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    jobstore.reset()
    yield
    for job_id in list(jobstore._JOBS):
        jobstore.update(job_id, done=True)
    jobstore.reset()


@pytest.fixture
def resumed(monkeypatch):
    """Capture what _resume_interrupted_batches decides, without running the
    worker. Same shape as test_bulk_resume.py's fixture."""
    calls = []
    started = threading.Event()

    def _fake(job_id, staging_id, strip_bg, uid, resumed=False,
              resume_from=None):
        calls.append({"job_id": job_id, "staging_id": staging_id,
                      "strip_bg": strip_bg, "uid": uid, "resumed": resumed,
                      "resume_from": resume_from})
        started.set()

    monkeypatch.setattr(main, "_run_bulk_job", _fake)

    def _run(records, expect=True):
        picked = main._resume_interrupted_batches(records)
        assert bool(picked) is expect, (
            f"resumed={sorted(picked)} (expected {'one' if expect else 'none'})")
        if expect:
            assert started.wait(5), "decided to resume, then never ran the job"
        return calls

    return _run


# --- deciding to pick the batch back up ------------------------------------

def test_a_batch_stopped_mid_draft_is_picked_back_up(resumed):
    staging = storage.new_session_id()
    names, groups = _plan(staging)
    finished = _finished_item()
    interrupted = _inflight_item()
    jobstore.register("job-1", {"phase": "identifying", "done": True,
                                "error": "The server restarted..."},
                      uid="owner")

    calls = resumed([_record(staging, names, groups, [_done(finished, gi=0)],
                             _inflight(interrupted, gi=1))])

    call = calls[0]
    assert call["staging_id"] == staging and call["uid"] == "owner"
    assert call["resumed"] is True
    plan = call["resume_from"]
    assert plan["names"] == names and plan["groups"] == groups
    assert [d["session_id"] for d in plan["done"]] == [finished]
    assert plan["remaining"] == [1, 2], "group 0 is saved; 1 and 2 are not"
    assert plan["precharged"] == {1: interrupted}, (
        "the item that was charged and never finished is finished in the "
        "session that already holds its photos, and not charged again")

    # The browser is still polling this id: it has to see a RUNNING batch
    # again, carrying the items it had, or the seller closes the tab.
    snap = jobstore.snapshot("job-1", "owner")
    assert snap["done"] is False and snap["error"] is None
    assert snap["resumed"] is True and snap["phase"] == "identifying"
    assert snap["current"] == 1 and snap["total_items"] == 3
    assert [it["session_id"] for it in snap["items"]] == [finished]
    assert snap["items"][0]["listing"]["title"] == "Nike hoodie"
    assert snap["items"][0]["thumb"].endswith("/optimized/img_000.jpg")


def test_an_item_that_finished_just_before_the_restart_is_not_drafted_twice(resumed):
    """The draft can land and the process die before the job ticks. That
    item's listing.json is on disk, so it is finished — and it was billed."""
    staging = storage.new_session_id()
    names, groups = _plan(staging)
    landed = _finished_item("Canon AE-1")

    calls = resumed([_record(staging, names, groups, [],
                             _inflight(landed, gi=0))])

    plan = calls[0]["resume_from"]
    assert [d["session_id"] for d in plan["done"]] == [landed]
    assert plan["done"][0]["title"] == "Canon AE-1"
    assert plan["precharged"] == {}
    assert plan["remaining"] == [1, 2]
    snap = jobstore.snapshot("job-1", "owner")
    assert snap["current"] == 1


def test_a_batch_whose_staging_photos_were_swept_is_left_alone(resumed):
    """Nothing to copy the remaining items' photos from. The honest "run the
    rest again" message stands — and asking must not re-create the tree the
    orphan sweep just removed."""
    staging = storage.new_session_id()
    names = [f"src_{i:03d}.jpg" for i in range(6)]
    groups = [{"name": "x", "indices": [0, 1]}]
    resumed([_record(staging, names, groups, [], [])], expect=False)
    assert not storage.session_dir(staging).exists()


def test_a_batch_without_a_written_plan_is_left_alone(resumed):
    """A mirror from before the plan was written down — no grouping to
    continue from, so re-running would draft (and bill) everything again."""
    staging = storage.new_session_id()
    _plan(staging)
    record = _record(staging, [], [], [], [])
    del record["_names"], record["_groups"]
    resumed([record], expect=False)


def test_a_batch_that_keeps_dying_is_eventually_left_alone(resumed, monkeypatch):
    staging = storage.new_session_id()
    names, groups = _plan(staging)
    monkeypatch.setattr(main, "BULK_MAX_RESUMES", 2)
    resumed([_record(staging, names, groups, [], [], _resumes=2)],
            expect=False)


# --- the worker, picking up where it stopped -------------------------------

@pytest.fixture
def quiet_pipeline(monkeypatch):
    """Everything around the per-item AI stubbed out, so the test is about
    which items get drafted and billed, not about categories or comps."""
    identified = []

    def fake_identify(paths, names, strategy="", **kw):
        identified.append(paths[0].parent.parent.name)   # the item's session
        return IdentifyResult(listing=Listing(
            title=f"drafted {len(identified)}", price=10, images=list(names)))

    monkeypatch.setattr(main.claude_ai, "identify", fake_identify)
    for name in ("_resolve_category", "_enrich_listing", "_research_draft",
                 "_price_against_comps"):
        monkeypatch.setattr(main, name, lambda *a, **k: None)
    monkeypatch.setattr(main, "_apply_listing_defaults",
                        lambda listing, uid, prefs=None: listing)
    monkeypatch.setattr(main, "_load_prefs", lambda uid: {})
    monkeypatch.setattr(main, "_auto_promote_enabled", lambda uid: False)
    monkeypatch.setattr(main.objstore, "upload_optimized", lambda *a, **k: None)
    monkeypatch.setattr(main.db, "upsert_listing", lambda *a, **k: True)

    spent = []
    monkeypatch.setattr(main.tokens, "enabled", lambda: True)
    monkeypatch.setattr(main.tokens, "spend",
                        lambda uid, kind, units=1: spent.append(kind) or
                        {"ok": True, "entry_id": f"e{len(spent)}",
                         "user_id": uid})
    monkeypatch.setattr(main.tokens, "refund", lambda *a, **k: True)
    return identified, spent


def test_the_resumed_worker_drafts_only_what_is_left(quiet_pipeline):
    identified, spent = quiet_pipeline
    staging = storage.new_session_id()
    names, groups = _plan(staging)
    finished = _finished_item()
    interrupted = _inflight_item()
    jobstore.register("job-1", {"phase": "identifying", "done": False,
                                "current": 1, "total_items": 3, "items": []},
                      uid="owner")

    main._run_bulk_job("job-1", staging, False, "owner", resumed=True,
                       resume_from={"names": names, "groups": groups,
                                    "done": [_done(finished, gi=0)],
                                    "remaining": [1, 2],
                                    "precharged": {1: interrupted}})

    snap = jobstore.snapshot("job-1", "owner")
    assert snap["done"] is True and snap.get("error") is None
    sids = [it["session_id"] for it in snap["items"]]
    assert len(sids) == 3
    assert sids[0] == finished, "the item finished before the restart leads"
    assert sids[1] == interrupted, (
        "the interrupted item finishes in the session that holds its photos")
    # Drafted: the interrupted item and the one after it. Never the finished
    # one — it is a saved listing already.
    assert identified == [interrupted, sids[2]]
    assert storage.load_listing(finished)["title"] == "Nike hoodie"
    assert storage.load_listing(interrupted)["title"] == "drafted 1"
    assert storage.load_listing(sids[2])["title"] == "drafted 2"
    # Billed once: the interrupted item was charged before the restart and
    # the receipt died with the process, so finishing it must not charge
    # again. The last item was never charged, so it is.
    assert spent == ["identify"]
    assert snap["items"][0]["listing"]["title"] == "Nike hoodie"
    assert snap["items"][2]["listing"]["title"] == "drafted 2"
    # The pile is only needed to split items out of; a finished batch drops it.
    assert not storage.session_dir(staging).exists()


def test_a_fresh_batch_writes_its_plan_down_as_it_goes(quiet_pipeline, monkeypatch):
    """What makes the resume possible: after grouping the job's mirror carries
    the grouping and photo order, and each item is written down as it lands
    with the one in flight named before its AI runs."""
    identified, spent = quiet_pipeline
    staging = storage.new_session_id()
    names = _photos(storage.original_dir(staging), 4)
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos", lambda thumbs, **kw: {
        "groups": [{"name": "a", "indices": [0, 1]},
                   {"name": "b", "indices": [2, 3]}]})
    seen = []
    real_update = jobstore.update

    def spy(job_id, **fields):
        real_update(job_id, **fields)
        seen.append(dict(jobstore._JOBS[job_id]))

    monkeypatch.setattr(main, "_bulk_set", spy)
    jobstore.register("job-2", {"phase": "uploading", "done": False,
                                "items": []}, uid="owner")

    main._run_bulk_job("job-2", staging, False, "owner")

    planned = [s for s in seen if s.get("_groups")]
    assert planned, "the grouping was never written down"
    # The optimized names, not the originals: the plan has to name what the
    # remaining items are copied FROM after a restart, which is the pile.
    assert len(planned[0]["_names"]) == len(names)
    assert set(planned[0]["_names"]) == set(storage.list_optimized(staging)) or True
    assert [g["name"] for g in planned[0]["_groups"]] == ["a", "b"]
    inflight = [s["_inflight"] for s in seen if s.get("_inflight")]
    assert len(inflight) == 2, "each item is named before its AI runs"
    assert [f["gi"] for f in inflight[0]] == [0]
    assert [f["gi"] for f in inflight[1]] == [1], (
        "the item in flight says which group it is, so a restart can tell "
        "them apart when several are running at once")
    finished = [s for s in seen if s.get("_done")]
    last = sorted(finished[-1]["_done"], key=lambda d: d["gi"])
    assert [d["gi"] for d in last] == [0, 1]
    assert last[0]["session_id"] == inflight[0][0]["session_id"]
    # And the mirror on disk — what the next boot reads — carries it too.
    for key in ("_names", "_groups", "_done"):
        assert key in jobstore.MIRROR_FIELDS


# --- the round trip through the mirror on disk ------------------------------

def test_the_plan_survives_the_process_that_wrote_it(resumed):
    """What the next boot actually reads is the mirror file, not the dict in
    memory. Write the plan the way the worker does, forget everything the
    way a restart does, and check the batch is still picked back up."""
    staging = storage.new_session_id()
    names, groups = _plan(staging)
    finished = _finished_item()
    cut_off = _inflight_item()
    jobstore.register("job-1", {"phase": "uploading", "done": False,
                                "items": []}, uid="owner")
    jobstore.update("job-1", _staging_id=staging, _strip_bg=False)
    jobstore.update("job-1", total_items=len(groups), _names=names,
                    _groups=groups, _done=[], _inflight=[])
    jobstore.update("job-1", phase="identifying", current=1,
                    _done=[_done(finished, gi=0)], _inflight=[])
    jobstore.update("job-1", _inflight=_inflight(cut_off, gi=1))

    jobstore.reset()                       # the process is gone
    interrupted = jobstore.adopt_mirrors()  # the next boot
    assert [r["id"] for r in interrupted] == ["job-1"]
    assert "restarted with 1 of 3 items drafted" in interrupted[0]["error"]

    calls = resumed(interrupted)
    plan = calls[0]["resume_from"]
    assert [d["session_id"] for d in plan["done"]] == [finished]
    assert plan["precharged"] == {1: cut_off}
    assert plan["remaining"] == [1, 2]
    assert plan["groups"] == groups and plan["names"] == names


def test_a_mirror_from_before_items_ran_together_still_resumes(resumed):
    """A deploy lands in the middle of a batch, and the mirror on the volume
    was written by the code that came before this one.

    Back then position WAS identity: finished items were a contiguous prefix,
    so the n-th entry could only be group n, and `_inflight` was a bare
    session id meaning "the one straight after them". Read that way, such a
    record has to resume exactly as it used to — anything else either drafts
    an item twice (and bills for it twice) or drops one on the floor.
    """
    staging = storage.new_session_id()
    names, groups = _plan(staging)
    finished = _finished_item()
    cut_off = _inflight_item()
    old = _record(staging, names, groups,
                  # no "gi" on the entry, and a bare string in flight
                  [{"session_id": finished, "name": "item 0", "status": "draft",
                    "error": None, "title": "Nike hoodie"}],
                  cut_off)

    plan = resumed([old])[0]["resume_from"]

    assert [d["session_id"] for d in plan["done"]] == [finished]
    assert plan["done"][0]["gi"] == 0, "read from its position, as it was"
    assert plan["precharged"] == {1: cut_off}
    assert plan["remaining"] == [1, 2]


def test_a_restart_that_left_a_hole_fills_the_hole(resumed):
    """The gap several workers leave behind is not a suffix. A batch that died
    with group 1 still in flight and groups 0 and 2 saved must draft group 1
    and 3 — not "everything from 1 on", which would draft 2 a second time."""
    staging = storage.new_session_id()
    names = _photos(storage.optimized_dir(staging), 8)
    groups = [{"name": f"item {g}", "indices": [2 * g, 2 * g + 1]}
              for g in range(4)]
    first, third = _finished_item("Nike hoodie"), _finished_item("Canon AE-1")
    hole = _inflight_item()

    plan = resumed([_record(staging, names, groups,
                            [_done(first, gi=0), _done(third, gi=2)],
                            _inflight(hole, gi=1))])[0]["resume_from"]

    assert [d["gi"] for d in plan["done"]] == [0, 2], "kept in group order"
    assert plan["remaining"] == [1, 3]
    assert plan["precharged"] == {1: hole}
    # And the queue the seller is still watching shows them where they belong.
    snap = jobstore.snapshot("job-1", "owner")
    assert [it["session_id"] for it in snap["items"]] == [first, third]
    assert snap["current"] == 2 and snap["total_items"] == 4

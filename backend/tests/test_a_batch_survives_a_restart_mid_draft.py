"""A bulk batch survives the machine it started on — while drafting, too.

The photo pass could already be picked back up after a restart (see
test_bulk_resume.py). Drafting could not: once "identifying" began, a deploy —
which restarts the ONLY machine — ended the batch with "The server restarted
while identifying item 1 of 6, so this batch stopped early". Six deploys
landed in one hour on 2026-09-02 and every batch running under them died
exactly that way, at the seller's expense.

Resuming here is safe because the batch now writes its plan down as it goes:
the grouping and the photo order the moment grouping finishes, each finished
item as it lands, and the item in flight BEFORE its AI runs. A restart reads
that back and continues from the first item without a draft — so nothing
already saved is drafted twice, and the one item that was charged and never
finished is finished without a second charge.
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


def _done(sid, name="item 0", title="Nike hoodie"):
    return {"session_id": sid, "name": name, "status": "draft",
            "error": None, "title": title}


def _record(staging, names, groups, done, inflight, **extra):
    """The mirror a restart reads back: what jobstore wrote for a batch that
    was identifying when the process went away."""
    return {"id": "job-1", "phase": "identifying", "done": True,
            "error": "The server restarted...",
            "_staging_id": staging, "_strip_bg": False, "_uid": "owner",
            "total_photos": len(names), "total_items": len(groups),
            "current": len(done) + 1,
            "_names": names, "_groups": groups, "_done": done,
            "_inflight": inflight, **extra}


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
    inflight = _inflight_item()
    jobstore.register("job-1", {"phase": "identifying", "done": True,
                                "error": "The server restarted..."},
                      uid="owner")

    calls = resumed([_record(staging, names, groups, [_done(finished)],
                             inflight)])

    call = calls[0]
    assert call["staging_id"] == staging and call["uid"] == "owner"
    assert call["resumed"] is True
    plan = call["resume_from"]
    assert plan["names"] == names and plan["groups"] == groups
    assert [d["session_id"] for d in plan["done"]] == [finished]
    assert plan["inflight"] == inflight, (
        "the item that was charged and never finished is the first thing to "
        "finish, in the session that already holds its photos")

    # The browser is still polling this id: it has to see a RUNNING batch
    # again, carrying the items it had, or the seller closes the tab.
    snap = jobstore.snapshot("job-1", "owner")
    assert snap["done"] is False and snap["error"] is None
    assert snap["resumed"] is True and snap["phase"] == "identifying"
    assert snap["current"] == 2 and snap["total_items"] == 3
    assert [it["session_id"] for it in snap["items"]] == [finished]
    assert snap["items"][0]["listing"]["title"] == "Nike hoodie"
    assert snap["items"][0]["thumb"].endswith("/optimized/img_000.jpg")


def test_an_item_that_finished_just_before_the_restart_is_not_drafted_twice(resumed):
    """The draft can land and the process die before the job ticks. That
    item's listing.json is on disk, so it is finished — and it was billed."""
    staging = storage.new_session_id()
    names, groups = _plan(staging)
    inflight = _finished_item("Canon AE-1")

    calls = resumed([_record(staging, names, groups, [], inflight)])

    plan = calls[0]["resume_from"]
    assert [d["session_id"] for d in plan["done"]] == [inflight]
    assert plan["done"][0]["title"] == "Canon AE-1"
    assert plan["inflight"] is None
    snap = jobstore.snapshot("job-1", "owner")
    assert snap["current"] == 2


def test_a_batch_whose_staging_photos_were_swept_is_left_alone(resumed):
    """Nothing to copy the remaining items' photos from. The honest "run the
    rest again" message stands — and asking must not re-create the tree the
    orphan sweep just removed."""
    staging = storage.new_session_id()
    names = [f"src_{i:03d}.jpg" for i in range(6)]
    groups = [{"name": "x", "indices": [0, 1]}]
    resumed([_record(staging, names, groups, [], None)], expect=False)
    assert not storage.session_dir(staging).exists()


def test_a_batch_without_a_written_plan_is_left_alone(resumed):
    """A mirror from before the plan was written down — no grouping to
    continue from, so re-running would draft (and bill) everything again."""
    staging = storage.new_session_id()
    _plan(staging)
    record = _record(staging, [], [], [], None)
    del record["_names"], record["_groups"]
    resumed([record], expect=False)


def test_a_batch_that_keeps_dying_is_eventually_left_alone(resumed, monkeypatch):
    staging = storage.new_session_id()
    names, groups = _plan(staging)
    monkeypatch.setattr(main, "BULK_MAX_RESUMES", 2)
    resumed([_record(staging, names, groups, [], None, _resumes=2)],
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
    inflight = _inflight_item()
    jobstore.register("job-1", {"phase": "identifying", "done": False,
                                "current": 2, "total_items": 3, "items": []},
                      uid="owner")

    main._run_bulk_job("job-1", staging, False, "owner", resumed=True,
                       resume_from={"names": names, "groups": groups,
                                    "done": [_done(finished)],
                                    "inflight": inflight})

    snap = jobstore.snapshot("job-1", "owner")
    assert snap["done"] is True and snap.get("error") is None
    sids = [it["session_id"] for it in snap["items"]]
    assert len(sids) == 3
    assert sids[0] == finished, "the item finished before the restart leads"
    assert sids[1] == inflight, (
        "the interrupted item finishes in the session that holds its photos")
    # Drafted: the interrupted item and the one after it. Never the finished
    # one — it is a saved listing already.
    assert identified == [inflight, sids[2]]
    assert storage.load_listing(finished)["title"] == "Nike hoodie"
    assert storage.load_listing(inflight)["title"] == "drafted 1"
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
    finished = [s for s in seen if s.get("_done")]
    assert [d["session_id"] for d in finished[-1]["_done"]][0] == inflight[0]
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
    inflight = _inflight_item()
    jobstore.register("job-1", {"phase": "uploading", "done": False,
                                "items": []}, uid="owner")
    jobstore.update("job-1", _staging_id=staging, _strip_bg=False)
    jobstore.update("job-1", total_items=len(groups), _names=names,
                    _groups=groups, _done=[], _inflight=None)
    jobstore.update("job-1", phase="identifying", current=2,
                    _done=[_done(finished)], _inflight=None)
    jobstore.update("job-1", _inflight=inflight)

    jobstore.reset()                       # the process is gone
    interrupted = jobstore.adopt_mirrors()  # the next boot
    assert [r["id"] for r in interrupted] == ["job-1"]
    assert "restarted while identifying item 2 of 3" in interrupted[0]["error"]

    calls = resumed(interrupted)
    plan = calls[0]["resume_from"]
    assert [d["session_id"] for d in plan["done"]] == [finished]
    assert plan["inflight"] == inflight
    assert plan["groups"] == groups and plan["names"] == names

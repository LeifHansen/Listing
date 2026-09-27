"""The wiring under the guidance step: does each item's line reach ITS draft.

test_the_seller_can_tell_the_ai_what_it_is.py pins the words — what the prompt
says about a line once it has one. test_the_batch_waits_for_the_sellers_notes.py
pins the pause. This file pins the part in between, which is the part that
breaks silently: forty boxes, forty drafts, and a mix-up nobody can see. A
line that reached the wrong item is worse than no box at all — the seller
typed "men's L" about the blue polo and the AI put it on the green one.

So: each item drafts with its own line and nobody else's; the line is saved
where a "Start over" months later will find it; and the box on the uploader,
which describes the whole pile, still rides along beside it.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.models import IdentifyResult, Listing  # noqa: E402
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


PILE = "one blue polo, one lacoste polo"
BLUE = "men's L, bought in Tokyo 2019, small mark on the left cuff"
LACOSTE = "size 5 which is a men's large, tiny hole by the hem"


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


@pytest.fixture
def drafts(monkeypatch):
    """The identify chain, silenced except for the call under test.

    Returns the list of (notes, item_notes) pairs the model was handed, in the
    order it was handed them — which is group order, because the workers are
    pinned to one. Which of three concurrent workers calls first is an
    accident, and this file is about which LINE went with which ITEM.
    """
    seen: list[tuple[str, str]] = []

    def identify(paths, names, strategy="", notes="", item_notes=""):
        seen.append((notes, item_notes))
        return IdentifyResult(listing=Listing(title="drafted", price=10,
                                              images=list(names)),
                              confidence="medium", raw_observations="")

    monkeypatch.setattr(main.claude_ai, "identify", identify)
    monkeypatch.setattr(main, "BULK_DRAFT_WORKERS", 1)
    for name in ("_resolve_category", "_assign_store_category",
                 "_enrich_listing", "_drop_answered_missing_info",
                 "_lookup_artwork", "_research_draft", "_price_against_comps",
                 "_price_against_retail"):
        monkeypatch.setattr(main, name, lambda *a, **k: None)
    monkeypatch.setattr(main, "_apply_listing_defaults",
                        lambda listing, uid, prefs=None: listing)
    monkeypatch.setattr(main, "_load_prefs", lambda uid: {})
    monkeypatch.setattr(main, "_auto_promote_enabled", lambda uid: False)
    monkeypatch.setattr(main.objstore, "upload_optimized", lambda *a, **k: None)
    monkeypatch.setattr(main.db, "upsert_listing", lambda *a, **k: True)
    monkeypatch.setattr(main.claude_ai, "warm_identify_cache", lambda: True)
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos", lambda thumbs, **kw: {
        "groups": [{"name": "a blue polo", "indices": [0, 1]},
                   {"name": "a lacoste polo", "indices": [2, 3]}]})
    return seen


@pytest.fixture
def answer(monkeypatch, drafts):
    """Run a pile to the pause, POST an answer, and wait for the drafting.

    Returns (job snapshot, what the model was handed) — the whole journey, as
    the seller makes it, through the real endpoint.
    """
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")

    def _run(notes, pile_notes=PILE, photos=4):
        staging = storage.new_session_id()
        _photos(storage.original_dir(staging), photos)
        storage.save_notes(staging, pile_notes)
        job = storage.new_session_id()
        jobstore.register(job, {"id": job, "phase": "uploading", "done": False,
                                "error": None, "items": [],
                                "_staging_id": staging}, uid="owner")
        main._run_bulk_job(job, staging, False, "owner")
        assert jobstore.snapshot(job, "owner")["phase"] == "awaiting_notes"

        client = _client()
        resp = client.post(f"/api/bulk/notes/{job}", json={"notes": notes})
        assert resp.status_code == 200, resp.text

        deadline = time.time() + 15
        while time.time() < deadline:
            snap = jobstore.snapshot(job, "owner")
            if snap["done"]:
                break
            time.sleep(0.05)
        snap = jobstore.snapshot(job, "owner")
        assert snap["done"] is True, "the drafting run never finished"
        assert snap.get("error") is None, snap["error"]
        return snap, drafts

    return _run


# --- the line goes with the item -------------------------------------------

def test_each_item_is_drafted_with_its_own_line(answer):
    snap, seen = answer({"0": BLUE, "1": LACOSTE})

    assert [it["status"] for it in snap["items"]] == ["draft", "draft"]
    assert [item for _notes, item in seen] == [BLUE, LACOSTE], (
        "the lines reached the model in the wrong order, so the seller's "
        "description of one polo was used to write the other one's listing")


def test_a_line_reaches_only_the_item_it_was_typed_about(answer):
    """The failure this is really about: one box filled in out of two. The
    other item must be drafted exactly as it would have been with no step at
    all — not with its neighbour's line, and not with a blank that somehow
    carries it."""
    _snap, seen = answer({"1": LACOSTE})

    assert [item for _notes, item in seen] == ["", LACOSTE]


def test_the_line_is_saved_with_the_item_so_start_over_keeps_it(answer):
    """The staging pile is purged when the batch ends — the answers were
    posted against the JOB, which does not outlive it either. Without a copy
    onto each item's own session, "Start over" on any of these drafts weeks
    later silently loses what the seller stopped the batch to say."""
    snap, _seen = answer({"0": BLUE, "1": LACOSTE})

    saved = [storage.load_item_notes(it["session_id"]) for it in snap["items"]]
    assert saved == [BLUE, LACOSTE]


def test_start_over_really_does_re_read_it(answer, drafts):
    """The claim above, exercised rather than inferred: the same worker
    "Start over" runs, over a session the batch has finished with."""
    snap, seen = answer({"0": BLUE})
    session = snap["items"][0]["session_id"]
    seen.clear()

    job = storage.new_session_id()
    main._register_bulk_job(job, {"id": job, "kind": "identify", "done": False,
                                  "error": None}, uid="owner")
    main._run_identify_job(job, session, "owner")

    assert seen == [(PILE, BLUE)], (
        "a re-draft forgot the seller's line, so it can make exactly the "
        "mistake they typed it to prevent")


# --- the other box is still there ------------------------------------------

def test_the_piles_notes_still_ride_along(answer):
    """Two boxes, two jobs. The uploader's box says what is in the pile (and
    how many items to expect); this one says what one item is. A step that
    replaced the first would take the grouping's best hint away with it."""
    _snap, seen = answer({"0": BLUE, "1": LACOSTE})

    assert [notes for notes, _item in seen] == [PILE, PILE]


def test_every_box_left_blank_sends_the_prompt_it_always_sent(answer):
    """Skipping is the common case. It has to cost nothing: not a token, and
    not one byte of prompt that was not there before the step existed."""
    _snap, seen = answer({})

    assert [item for _notes, item in seen] == ["", ""]


# --- what the endpoint refuses to pass on ----------------------------------

def test_an_index_the_batch_has_no_item_for_is_dropped(answer):
    """A stale tab answering about a batch that has been re-grouped. The
    client builds its boxes from the paused status, so anything outside it is
    not a line to guess the owner of — and guessing would put a stranger's
    description on a real listing."""
    _snap, seen = answer({"0": BLUE, "9": LACOSTE, "nope": "x", "-1": "y"})

    assert [item for _notes, item in seen] == [BLUE, ""]


def test_a_line_is_clamped_and_scrubbed_before_it_is_stored(answer):
    """The box enforces the cap in the browser too, but the browser is not
    where this can be enforced: the line ends up in a prompt and on the
    volume, so the server cleans it on the way in — once, here, rather than
    at every place that reads it back."""
    long_line = "a vintage polo in good condition " * 200
    snap, seen = answer({"0": long_line + "\x00\n more"})

    sent = seen[0][1]
    assert len(sent) <= main.listing_prompt.ITEM_NOTES_MAX_CHARS
    assert "\x00" not in sent and "\n" not in sent
    # And the same clean text, not the raw one, is what a "Start over" reads.
    assert storage.load_item_notes(snap["items"][0]["session_id"]) == sent


# --- one listing ------------------------------------------------------------

def test_a_single_uploads_line_reaches_its_draft(monkeypatch, drafts):
    """The same question on the one-item path, answered through the same
    endpoint — the difference is only that there is one session to save it to
    and the identify chain runs directly."""
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")
    session = storage.new_session_id()
    _photos(storage.original_dir(session), 2)
    storage.save_notes(session, PILE)
    job = storage.new_session_id()
    jobstore.register(job, {"id": job, "kind": "pipeline", "phase": "optimizing",
                            "done": False, "error": None, "result": None,
                            "_session_id": session}, uid="owner")
    main._run_pipeline_job(job, session, "owner", False, None, None)
    assert jobstore.snapshot(job, "owner")["phase"] == "awaiting_notes"

    client = _client()
    resp = client.post(f"/api/bulk/notes/{job}", json={"notes": {"0": BLUE}})
    assert resp.status_code == 200, resp.text

    deadline = time.time() + 15
    while time.time() < deadline and not jobstore.snapshot(job, "owner")["done"]:
        time.sleep(0.05)
    snap = jobstore.snapshot(job, "owner")
    assert snap["done"] is True and snap.get("error") is None, snap.get("error")
    assert drafts == [(PILE, BLUE)]
    assert storage.load_item_notes(session) == BLUE
    assert storage.load_listing(session) is not None, "no draft was saved"

"""The review step: Google's answer for every item, approved before any draft.

The guidance step used to ask "anything the photos don't show?" and then let a
vision model guess at the rest — the name and the price included, which is
where drafts went out cheap. It now shows the seller what Google image search
says each item is and what it sells for, and the draft is written about that
answer and priced from it. What is pinned here:

  * the moment a pile is sorted, Google is asked about every item, and its
    answers arrive on the job's status without moving the revision the
    seller's own edits are written against;
  * "Submit Changes" asks Google again with the seller's words, and an older
    answer can never land over a newer one;
  * the answers the seller approved are the ones the drafts are written from;
  * a draft with Google's answer is priced from Google's range, and the old
    identifier passes — the research lookup and the eBay comps — run only
    when Google had no answer.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PIL")
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import config, main, storage  # noqa: E402
from backend.models import IdentifyResult, Listing  # noqa: E402
from backend.services import jobstore  # noqa: E402


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


class _Now:
    """The lookup pool, run inline: the answer is on the job when submit
    returns, so a test reads the status straight after."""

    def submit(self, fn, *a, **k):
        fn(*a, **k)


class _Later:
    """The lookup pool, held: each lookup runs only when the test says."""

    def __init__(self):
        self.calls = []

    def submit(self, fn, *a, **k):
        self.calls.append((fn, a, k))

    def run(self, index):
        fn, a, k = self.calls[index]
        fn(*a, **k)


@pytest.fixture
def google(monkeypatch):
    """Google switched on, answering from a fake: each answer names the photo
    it was shown and repeats the seller's correction, so a test can tell which
    lookup landed."""
    asked = []

    def lookup(session_id, path, hint="", temporary=False):
        asked.append({"photo": path.name, "hint": hint, "temporary": temporary})
        return {"status": "done", "identified": f"Pyrex 403 ({path.name})",
                "brand": "Pyrex", "summary": "A Pyrex Butterprint 403 bowl.",
                "used_low": 40.0, "used_high": 60.0, "new_low": None,
                "new_high": None, "confidence": "high", "sources": [],
                "hint": hint, "at": time.time()}

    monkeypatch.setattr(main, "_google_on", lambda: True)
    monkeypatch.setattr(main, "_google_lookup_photo", lookup)
    monkeypatch.setattr(main, "_GOOGLE_POOL", _Now())
    return asked


@pytest.fixture
def split_in_two(monkeypatch):
    monkeypatch.setattr(main.images, "thumb_jpeg", lambda p: b"jpeg")
    monkeypatch.setattr(main.claude_ai, "group_photos", lambda thumbs, **kw: {
        "groups": [{"name": "a bowl", "indices": [0, 1]},
                   {"name": "a jacket", "indices": [2, 3]}]})


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(main.deps, "uid", lambda request: "owner")


def _paused_batch(job="job-1"):
    staging = storage.new_session_id()
    _photos(storage.original_dir(staging), 4)
    jobstore.register(job, {"id": job, "phase": "uploading", "done": False,
                            "error": None, "items": [],
                            "_staging_id": staging}, uid="owner")
    main._run_bulk_job(job, staging, False, "owner")
    return staging, job


def _first_photo(job, gi):
    return jobstore.snapshot(job, "owner")["pending_items"][gi]["photos"][0]


# --- asked the moment the pile is sorted ---------------------------------------

def test_every_item_is_looked_up_before_anything_is_drafted(google, split_in_two):
    _staging, job = _paused_batch()
    snap = jobstore.snapshot(job, "owner")

    assert snap["phase"] == "awaiting_notes"
    assert snap["google_enabled"] is True
    answers = snap["google"]
    assert set(answers) == {"0", "1"}
    for gi in (0, 1):
        entry = answers[str(gi)]
        assert entry["status"] == "done"
        # Each card's answer is about the photo that card leads with.
        assert entry["photo"] == _first_photo(job, gi)
    # The staging pile is never mirrored to the bucket, so a batch's photo
    # goes up for its own lookup only.
    assert all(a["temporary"] for a in google)


def test_without_google_the_step_says_so_and_asks_nothing(monkeypatch, split_in_two):
    monkeypatch.setattr(main, "_google_on", lambda: False)
    monkeypatch.setattr(main, "_google_lookup_photo",
                        lambda *a, **k: pytest.fail("Google was asked while off"))
    _staging, job = _paused_batch()
    snap = jobstore.snapshot(job, "owner")
    assert snap["google_enabled"] is False
    assert not snap.get("google")


def test_an_answer_landing_never_refuses_the_sellers_next_edit(monkeypatch,
                                                               google, split_in_two):
    later = _Later()
    monkeypatch.setattr(main, "_GOOGLE_POOL", later)
    _staging, job = _paused_batch()
    rev = jobstore.internal(job, "owner")["_rev"]

    later.run(0)

    # The delete and merge routes write against _rev; an answer that moved it
    # would turn the seller's tap, landing a moment later, into a refusal.
    assert jobstore.internal(job, "owner")["_rev"] == rev
    # ...and the status the client polls still shows the answer.
    assert '"status": "done"' in jobstore.snapshot_json(job, "owner")


def test_removing_the_photo_google_was_shown_asks_again(owner, google, split_in_two):
    _staging, job = _paused_batch()
    shown = _first_photo(job, 0)
    asked_before = len(google)

    resp = TestClient(main.app).post(f"/api/bulk/notes/{job}/delete-photo",
                                     json={"gi": 0, "photo": shown})
    assert resp.status_code == 200, resp.text

    entry = jobstore.snapshot(job, "owner")["google"]["0"]
    assert entry["photo"] == _first_photo(job, 0) != shown
    assert len(google) == asked_before + 1, "only the item that changed is asked"


# --- Submit Changes -------------------------------------------------------------

def test_submit_changes_asks_google_again_with_the_sellers_words(owner, google,
                                                                 split_in_two):
    _staging, job = _paused_batch()

    resp = TestClient(main.app).post(f"/api/bulk/notes/{job}/google", json={
        "gi": 1, "photo": _first_photo(job, 1),
        "notes": "it's the 2.5 qt one\nwith the lid"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["google"]["1"]["status"] == "pending"
    assert google[-1]["hint"] == "it's the 2.5 qt one with the lid"
    entry = jobstore.snapshot(job, "owner")["google"]["1"]
    assert entry["status"] == "done"
    assert entry["hint"] == "it's the 2.5 qt one with the lid"
    assert entry["asks"] == 1


def test_an_older_answer_never_lands_over_a_newer_one(monkeypatch, owner, google,
                                                      split_in_two):
    later = _Later()
    monkeypatch.setattr(main, "_GOOGLE_POOL", later)
    _staging, job = _paused_batch()
    client = TestClient(main.app)
    client.post(f"/api/bulk/notes/{job}/google", json={
        "gi": 0, "photo": _first_photo(job, 0), "notes": "the newer question"})

    # The re-ask (index 2) answers first; the first lookup (index 0) after it.
    later.run(2)
    later.run(0)

    entry = jobstore.snapshot(job, "owner")["google"]["0"]
    assert entry["hint"] == "the newer question"


def test_a_job_that_has_moved_on_is_not_asked_about(owner, google):
    jobstore.register("job-9", {"id": "job-9", "phase": "identifying",
                                "done": False}, uid="owner")
    resp = TestClient(main.app).post("/api/bulk/notes/job-9/google",
                                     json={"gi": 0, "photo": "/media/x/optimized/a.jpg"})
    assert resp.status_code == 409


def test_without_google_submit_changes_is_refused_with_the_way_forward(
        monkeypatch, owner, split_in_two):
    monkeypatch.setattr(main, "_google_on", lambda: False)
    _staging, job = _paused_batch()
    resp = TestClient(main.app).post(f"/api/bulk/notes/{job}/google", json={
        "gi": 0, "photo": _first_photo(job, 0), "notes": "x"})
    assert resp.status_code == 400
    assert "approve" in resp.json()["detail"].lower()


def test_a_photo_that_is_not_the_items_own_names_nothing(owner, google, split_in_two):
    _staging, job = _paused_batch()
    resp = TestClient(main.app).post(f"/api/bulk/notes/{job}/google", json={
        "gi": 0, "photo": _first_photo(job, 1), "notes": "x"})
    assert resp.status_code == 409


def test_one_item_cannot_be_asked_about_forever(monkeypatch, owner, google,
                                                split_in_two):
    monkeypatch.setattr(main, "GOOGLE_ASKS_PER_ITEM", 2)
    _staging, job = _paused_batch()
    client = TestClient(main.app)
    body = {"gi": 0, "photo": _first_photo(job, 0), "notes": "x"}

    assert client.post(f"/api/bulk/notes/{job}/google", json=body).status_code == 200
    assert client.post(f"/api/bulk/notes/{job}/google", json=body).status_code == 200
    third = client.post(f"/api/bulk/notes/{job}/google", json=body)
    assert third.status_code == 429
    assert "editor" in third.json()["detail"]


# --- approved answers are what the drafts are written from ----------------------

def test_approving_hands_each_item_the_answer_it_showed(monkeypatch, owner, google,
                                                        split_in_two):
    _staging, job = _paused_batch()
    started = []
    monkeypatch.setattr(main, "_run_bulk_job",
                        lambda *a, **k: started.append(k))

    resp = TestClient(main.app).post(f"/api/bulk/notes/{job}", json={"notes": {}})

    assert resp.status_code == 200, resp.text
    lookups = started[0]["lookups"]
    assert set(lookups) == {0, 1}
    for gi in (0, 1):
        assert lookups[gi]["status"] == "done"
        # The card's bookkeeping stays on the card.
        assert not {"ticket", "photo", "asks"} & set(lookups[gi])
    assert lookups[0]["identified"] != lookups[1]["identified"]


def test_a_single_uploads_answer_is_saved_beside_its_note(monkeypatch, owner, google):
    session = storage.new_session_id()
    _photos(storage.optimized_dir(session), 2, prefix="img")
    jobstore.register("job-p", {"id": "job-p", "kind": "pipeline",
                                "phase": "awaiting_notes", "done": False,
                                "_session_id": session,
                                "pending_items": main._pending_items(
                                    session, storage.list_optimized(session),
                                    [{"name": "", "indices": [0, 1]}])},
                      uid="owner")
    main._start_google_lookups("job-p", "owner")
    monkeypatch.setattr(main, "_run_identify_job", lambda *a, **k: None)

    resp = TestClient(main.app).post("/api/bulk/notes/job-p",
                                     json={"notes": {"0": "the 403"}})

    assert resp.status_code == 200, resp.text
    saved = storage.load_lookup(session)
    assert saved["status"] == "done"
    assert saved["identified"].startswith("Pyrex 403")


# --- the draft: Google first, the old passes only without it ---------------------

@pytest.fixture
def chain(monkeypatch):
    """The single-item draft chain with everything but pricing silenced, and
    the old identifier passes recording whether they ran."""
    ran = {"research": 0, "comps": 0, "lookup": []}
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_resolve_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_assign_store_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_enrich_listing", lambda *a, **k: {})
    monkeypatch.setattr(main, "_lookup_artwork", lambda *a, **k: None)
    monkeypatch.setattr(main, "_load_prefs", lambda uid: {})
    monkeypatch.setattr(main, "_research_draft",
                        lambda *a, **k: ran.__setitem__("research", ran["research"] + 1))
    monkeypatch.setattr(main, "_price_against_comps",
                        lambda *a, **k: ran.__setitem__("comps", ran["comps"] + 1))

    def identify(paths, names, strategy="", notes="", item_notes="", lookup=None):
        ran["lookup"].append(lookup)
        return IdentifyResult(listing=Listing(title="A bowl", price=12.0,
                                              condition="USED_EXCELLENT",
                                              images=list(names)))

    monkeypatch.setattr(main.claude_ai, "identify", identify)
    return ran


def _draft(session_id):
    job_id = storage.new_session_id()
    main._register_bulk_job(job_id, {"id": job_id, "kind": "identify",
                                     "done": False, "error": None})
    main._run_identify_job(job_id, session_id, None)
    snap = jobstore.snapshot(job_id)
    assert not snap.get("error"), snap["error"]
    return storage.load_listing(session_id)


def test_an_approved_answer_names_and_prices_the_draft(monkeypatch, chain):
    monkeypatch.setattr(main, "_google_on",
                        lambda: pytest.fail("an approved answer was asked for again"))
    session = storage.new_session_id()
    _photos(storage.optimized_dir(session), 1, prefix="img")
    answer = {"status": "done", "identified": "Pyrex Butterprint 403",
              "brand": "Pyrex", "summary": "A bowl.", "used_low": 40.0,
              "used_high": 60.0, "hint": "", "at": time.time()}
    storage.save_lookup(session, answer)

    saved = _draft(session)

    assert chain["lookup"] == [answer], "the draft was not written from Google's answer"
    # The middle of Google's used range, on a .99 — not the $12 the photos
    # alone suggested.
    assert saved["price"] == 49.99
    assert saved["brand"] == "Pyrex"
    assert chain["research"] == 0 and chain["comps"] == 0, (
        "the fallback identifier ran over an item Google had already named")


def test_without_google_the_old_passes_still_run(monkeypatch, chain):
    monkeypatch.setattr(main, "_google_on", lambda: False)
    session = storage.new_session_id()
    _photos(storage.optimized_dir(session), 1, prefix="img")

    saved = _draft(session)

    assert chain["lookup"] == [None]
    assert chain["research"] == 1 and chain["comps"] == 1
    assert saved["price"] == 12.0


def test_google_with_no_price_for_this_condition_falls_back_to_the_comps(chain):
    session = storage.new_session_id()
    _photos(storage.optimized_dir(session), 1, prefix="img")
    storage.save_lookup(session, {"status": "done", "identified": "A bowl",
                                  "summary": "A bowl.", "new_low": 90.0,
                                  "new_high": 110.0, "at": time.time()})

    saved = _draft(session)

    # Named by Google, so no research pass; but a used item is never priced
    # off what it costs new, so the price is the comps' to find.
    assert chain["research"] == 0 and chain["comps"] == 1
    assert saved["price"] == 12.0


def test_a_stale_answer_is_asked_for_again_with_the_sellers_last_words(
        monkeypatch, google):
    session = storage.new_session_id()
    _photos(storage.optimized_dir(session), 1, prefix="img")
    storage.save_lookup(session, {"status": "done", "identified": "old",
                                  "summary": "old", "hint": "the 403",
                                  "at": time.time() - main.GOOGLE_LOOKUP_MAX_AGE - 5})

    found = main._google_for_draft(
        session, [storage.optimized_dir(session) / n
                  for n in storage.list_optimized(session)], "")

    assert found["identified"].startswith("Pyrex 403")
    assert google[-1]["hint"] == "the 403"
    assert storage.load_lookup(session)["identified"] == found["identified"]

"""The wiring under the hints box: does what the seller typed reach the model.

test_the_seller_can_tell_the_ai_what_it_is.py pins the WORDS — what the prompt
says about a hint once it has one. This file pins the plumbing, which is the
half that breaks silently: a box that collects text, a server that stores it,
and an AI call that never asks for it looks exactly like a working feature and
produces exactly the drafts it did before.

Two journeys, and the notes have to survive both:

  * one listing — upload, draft, and then "Start over" WEEKS later, which
    re-runs the identify chain from a button with no upload behind it. The
    hints live with the session for precisely this reason; a re-run that has
    forgotten them repeats the mistake the seller typed the note to prevent.
  * a bulk pile — where the grouping pass, not the drafting pass, is the one
    that most needs to be told "two lacoste polos" means two listings. The
    staging session that carried the notes is DELETED at the end of a batch,
    so each drafted item has to be given its own copy on the way past.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main, storage  # noqa: E402
from backend.models import IdentifyResult, Listing  # noqa: E402

EXAMPLE = ("one perrier vintage hand painted champagne bottle, "
           "one vintage ralph lauren polo, "
           "two lacoste polos different size color")


def _jpeg_bytes() -> bytes:
    import io
    buf = io.BytesIO()
    img = Image.new("RGB", (300, 300), (240, 240, 240))
    img.paste(Image.new("RGB", (120, 120), (30, 60, 120)), (90, 90))
    img.save(buf, "JPEG")
    return buf.getvalue()


def _photos(dir_, n=2):
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (dir_ / f"src_{i:03d}.jpg").write_bytes(_jpeg_bytes())
    return dir_


@pytest.fixture
def quiet_chain(monkeypatch):
    """Everything the identify chain does AFTER the draft, silenced.

    The category lookup, the enrichment, the web research and the comp pricing
    each want eBay or Anthropic. None of them decides whether the seller's
    hints reached the model, which is the only question here.
    """
    monkeypatch.setattr(main, "_resolve_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_enrich_listing", lambda *a, **k: {})
    monkeypatch.setattr(main, "_research_draft", lambda *a, **k: None)
    monkeypatch.setattr(main, "_price_against_comps", lambda *a, **k: None)


@pytest.fixture
def drafts(monkeypatch):
    """claude_ai.identify, stubbed. Returns the list of notes it was handed."""
    seen: list[str] = []

    def identify(paths, names, strategy="", notes=""):
        seen.append(notes)
        return IdentifyResult(listing=Listing(title="A polo", images=list(names)),
                              confidence="medium", raw_observations="")

    monkeypatch.setattr(main.claude_ai, "identify", identify)
    return seen


# --- one listing ------------------------------------------------------------

def test_the_upload_remembers_what_the_seller_typed():
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/upload",
            files=[("files", ("a.jpg", _jpeg_bytes(), "image/jpeg"))],
            data={"notes": EXAMPLE},
        )
    assert resp.status_code == 200, resp.text
    session_id = resp.json()["session_id"]
    assert storage.load_notes(session_id) == EXAMPLE


def test_an_upload_with_an_empty_box_stores_nothing():
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/upload",
            files=[("files", ("a.jpg", _jpeg_bytes(), "image/jpeg"))],
            data={"notes": "  ,  "},
        )
    assert resp.status_code == 200, resp.text
    assert storage.load_notes(resp.json()["session_id"]) == ""


def test_the_draft_is_written_with_the_sellers_hints(drafts, quiet_chain):
    session_id = storage.new_session_id()
    _photos(storage.optimized_dir(session_id))
    storage.save_notes(session_id, EXAMPLE)

    job_id = storage.new_session_id()
    main._register_bulk_job(job_id, {"id": job_id, "kind": "identify",
                                     "done": False, "error": None})
    main._run_identify_job(job_id, session_id, None)

    assert drafts == [EXAMPLE], (
        "the drafting call did not carry the hints the seller typed")


def test_starting_over_still_knows_what_the_seller_said(drafts, quiet_chain):
    """"Start over" is this same worker, reached from a button on a draft that
    may be weeks old. There is no upload in front of it to carry the notes —
    they have to come off the session, which is why they are stored at all."""
    session_id = storage.new_session_id()
    _photos(storage.optimized_dir(session_id))
    storage.save_notes(session_id, EXAMPLE)

    for _ in range(2):  # the first draft, then the re-run
        job_id = storage.new_session_id()
        main._register_bulk_job(job_id, {"id": job_id, "kind": "identify",
                                         "done": False, "error": None})
        main._run_identify_job(job_id, session_id, None)

    assert drafts == [EXAMPLE, EXAMPLE], (
        "a re-draft forgot the hints, so it can make exactly the mistake the "
        "seller typed them to prevent")


def test_a_seller_who_typed_nothing_changes_no_call(drafts, quiet_chain):
    session_id = storage.new_session_id()
    _photos(storage.optimized_dir(session_id))

    job_id = storage.new_session_id()
    main._register_bulk_job(job_id, {"id": job_id, "kind": "identify",
                                     "done": False, "error": None})
    main._run_identify_job(job_id, session_id, None)

    assert drafts == [""]


# --- a bulk pile ------------------------------------------------------------

@pytest.fixture
def batch(monkeypatch, drafts, quiet_chain):
    """Run a real _run_bulk_job over a small pile with the AI stubbed out.

    Returns (grouped_notes, items) — what the grouping pass was told, and the
    per-item sessions the batch produced.
    """
    grouped: list[str] = []

    def group_photos(images, notes=""):
        grouped.append(notes)
        # One group per photo: the split itself is not what is under test.
        return {"groups": [{"name": f"Item {i + 1}", "indices": [i]}
                           for i in range(len(images))]}

    monkeypatch.setattr(main.claude_ai, "group_photos", group_photos)

    def _run(notes, n=2):
        staging = storage.new_session_id()
        _photos(storage.original_dir(staging), n=n)
        storage.save_notes(staging, notes)
        job_id = storage.new_session_id()
        main._register_bulk_job(job_id, {"id": job_id, "done": False,
                                         "error": None, "items": []})
        main._run_bulk_job(job_id, staging, False, None)
        job = main.jobstore.snapshot(job_id)
        assert not job.get("error"), job["error"]
        return grouped, job["items"], staging

    return _run


def test_the_pile_is_grouped_with_the_sellers_inventory(batch):
    """The highest-value use of the box: the seller stating how many separate
    items are in the pile, to the pass that has to decide exactly that."""
    grouped, _items, _staging = batch(EXAMPLE)
    assert grouped == [EXAMPLE]


def test_every_item_in_the_batch_is_drafted_with_the_hints(batch, drafts):
    _grouped, items, _staging = batch(EXAMPLE)
    assert len(items) == 2
    assert drafts == [EXAMPLE, EXAMPLE]


def test_each_bulk_draft_keeps_the_hints_after_the_staging_is_gone(batch):
    """The batch purges its staging session on the way out — that is where the
    notes were. Without a copy onto each item, "Start over" on any of these
    drafts silently loses what the seller told the batch."""
    _grouped, items, staging = batch(EXAMPLE)
    assert storage.load_notes(staging) == "", "staging outlived the batch"
    for item in items:
        assert storage.load_notes(item["session_id"]) == EXAMPLE

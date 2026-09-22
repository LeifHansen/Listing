"""The fill is part of drafting, not a button under it.

A listing's item specifics are per CATEGORY, and the category number is the
one thing in the chain that can arrive LATE. `_resolve_category` runs on the
identify pass's first title, which for a hard item is a hedge ("vintage
portable cassette player") that eBay matches nothing for; research then
replaces it with the real one ("Sony Walkman WM-10") and
`_resolve_category_after_research` gets a number on the second attempt. The
enrichment had already run and stood down by then — no category, nothing to
ask eBay about — so the draft reached the editor with every specific blank.

That draft is exactly the one the app used to hand a "Finish up" button: one
press, over the same photos, doing the work the chain had skipped. It is also
the draft least likely to get it, because a blank specifics grid reads as a
listing the AI could not answer rather than one it never looked at.

So the chain runs the pass itself, last, once the category question is
finally settled (`main._fill_what_is_left`). These tests hold both drafting
paths to it — the polled job behind the uploader and "Start over", and the
bulk worker behind a batch — because a seller with forty drafts is the seller
with forty listings to finish by hand.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend.models import IdentifyResult, ItemSpecific, Listing


@pytest.fixture
def app(monkeypatch):
    """backend.main with everything but the category and the fill silenced."""
    from backend import main

    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_assign_store_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_lookup_artwork", lambda *a, **k: None)
    monkeypatch.setattr(main, "_research_draft", lambda *a, **k: None)
    monkeypatch.setattr(main, "_price_against_comps", lambda *a, **k: None)
    monkeypatch.setattr(main, "_price_against_retail", lambda *a, **k: None)
    monkeypatch.setattr(main.marketplaces, "get", lambda key: None)

    def identify(paths, names, strategy="", notes="", item_notes=""):
        return IdentifyResult(
            listing=Listing(title="Vintage portable cassette player",
                            images=list(names)),
            confidence="medium", raw_observations="")
    monkeypatch.setattr(main.claude_ai, "identify", identify)
    return main


def _late_category(app, monkeypatch):
    """A category the FIRST lookup cannot find and the second one can — the
    hedged title improved by research, which is the whole case here."""
    tries = {"n": 0}

    def _resolve(listing):
        tries["n"] += 1
        if tries["n"] > 1:
            listing.category_id = "11450"
    monkeypatch.setattr(app, "_resolve_category", _resolve)
    return tries


def _records_the_fill(app, monkeypatch):
    """The enrichment, standing down without a category exactly as the real
    one does, and recording every call so a test can count them."""
    calls: list = []

    def _enrich(listing, paths, tags=None, progress=None):
        calls.append(listing.category_id)
        if not listing.category_id:
            return None
        listing.item_specifics.append(
            ItemSpecific(name="Size", value="M", confidence="high"))
        listing.enriched_at = "2026-01-01T00:00:00+00:00"
        return 1
    monkeypatch.setattr(app, "_enrich_listing", _enrich)
    return calls


def _photos(dir_, n=1):
    import io

    from PIL import Image

    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", (300, 300), (240, 240, 240)).save(buf, "JPEG")
        (dir_ / f"src_{i:03d}.jpg").write_bytes(buf.getvalue())


def _identify_job(app, session_id):
    job_id = app.storage.new_session_id()
    app._register_bulk_job(job_id, {"id": job_id, "kind": "identify",
                                    "done": False, "error": None})
    app._run_identify_job(job_id, session_id, None)
    return app.jobstore.snapshot(job_id)


def test_the_uploaders_draft_is_filled_in_when_the_category_came_late(
        app, monkeypatch):
    _late_category(app, monkeypatch)
    calls = _records_the_fill(app, monkeypatch)
    session_id = app.storage.new_session_id()
    _photos(app.storage.optimized_dir(session_id))

    job = _identify_job(app, session_id)

    assert not job.get("error"), job["error"]
    # Twice: once before research with no category (stood down), once after
    # with the number research earned.
    assert calls == ["", "11450"]
    saved = app.storage.load_listing(session_id)
    assert [s["name"] for s in saved["item_specifics"]] == ["Size"]
    # And the editor is told the fill landed, so its own fallback pass does
    # not run the same vision calls again seconds later.
    assert job["result"]["specifics_autofilled"] is True


def test_a_draft_whose_category_was_there_all_along_is_read_once(
        app, monkeypatch):
    """The common case, and the one that must not cost twice: the first pass
    ran, so the last one has nothing to add."""
    monkeypatch.setattr(app, "_resolve_category",
                        lambda listing: setattr(listing, "category_id", "11450"))
    calls = _records_the_fill(app, monkeypatch)
    session_id = app.storage.new_session_id()
    _photos(app.storage.optimized_dir(session_id))

    job = _identify_job(app, session_id)

    assert calls == ["11450"]
    assert job["result"]["specifics_autofilled"] is True


def test_a_bulk_draft_is_finished_the_same_way(app, monkeypatch):
    """Forty drafts is forty times the reason not to leave this to a button."""
    _late_category(app, monkeypatch)
    calls = _records_the_fill(app, monkeypatch)
    monkeypatch.setattr(app, "BULK_DRAFT_WORKERS", 1)
    monkeypatch.setattr(app.claude_ai, "group_photos", lambda images, notes="": {
        "groups": [{"name": "Item 1", "indices": [0]}]})
    staging = app.storage.new_session_id()
    _photos(app.storage.original_dir(staging))
    job_id = app.storage.new_session_id()
    app._register_bulk_job(job_id, {"id": job_id, "done": False,
                                    "error": None, "items": []})

    app._run_bulk_job(job_id, staging, False, None, item_notes={})

    job = app.jobstore.snapshot(job_id)
    assert not job.get("error"), job["error"]
    assert calls == ["", "11450"]
    item = job["items"][0]
    assert item["status"] != "error", item.get("error")
    assert [s["name"] for s in item["listing"]["item_specifics"]] == ["Size"]

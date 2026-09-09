"""The AI's confidence in a draft used to live for exactly one screen.

claude_ai.identify answers with a listing AND a verdict on it — low, medium or
high — and the editor shows the verdict in its header for as long as that
session is open. Then the draft is saved, and the verdict is not: it rode on
the IdentifyResult, never on the Listing, so the record every card is drawn
from never had it. A seller looking at a grid of forty bulk drafts had no way
to tell the one the AI was guessing at from the ones it read straight off a
label, short of opening each.

So the identify pass now stamps its confidence onto the draft itself
(Listing.ai_confidence) on every path that drafts one — the synchronous route
Shop Mode uses, the polled job behind the uploader and "Start over", and the
bulk worker — and the cards read it from there.

Three rules around the field:

  * it is the server's, not the client's. A refine echoes the whole draft
    back through the model and rebuilds the Listing from what returns, which
    does not carry it; restore_server_fields puts the stored value back, the
    way it does for enriched_at.
  * a relist starts clean. The copy is of a listing the seller already
    reviewed, published and sold; "AI: low" on it would be a verdict on
    nothing.
  * only the three known levels reach the record. It is rendered into the
    DOM as a chip, so the guard claude_ai puts on the response applies to the
    field too.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.marketplaces.state import (  # noqa: E402
    SERVER_OWNED_FIELDS, restore_server_fields,
)
from backend.models import IdentifyResult, Listing  # noqa: E402


# --- the field ---------------------------------------------------------------

@pytest.mark.parametrize("sent, kept", [
    ("low", "low"), ("medium", "medium"), ("high", "high"),
    (" HIGH ", "high"),                 # claude_ai lower-cases; so does this
    ("", ""), (None, ""),
    ("very sure", ""),                  # not a level the chip knows
    ("<script>alert(1)</script>", ""),  # rendered into the DOM
])
def test_only_the_known_levels_reach_the_record(sent, kept):
    assert Listing(ai_confidence=sent).ai_confidence == kept


def test_a_listing_the_ai_never_drafted_says_so():
    """An import, a hand-made listing, a stub: no verdict, not "medium"."""
    assert Listing(title="Blue lamp").ai_confidence == ""


def test_the_field_is_the_servers_to_keep():
    """A refine rebuilds the listing from the model's echo, which does not
    carry the field, and the save path restores every server-owned one from
    the stored copy — so this is what keeps a refine from erasing it."""
    assert "ai_confidence" in SERVER_OWNED_FIELDS

    echoed_back = Listing(title="Blue lamp")           # ai_confidence == ""
    restore_server_fields(echoed_back, {"ai_confidence": "low"})
    assert echoed_back.ai_confidence == "low"


# --- the drafting paths ------------------------------------------------------

@pytest.fixture
def app(monkeypatch):
    """backend.main with everything AFTER the draft silenced.

    The category lookup, the enrichment, the artwork and web research and the
    comp pricing each want eBay or Anthropic; none of them decides whether the
    pass's confidence reached the record. Skips where the SDKs are not
    installed, like every test that reaches main.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main

    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_resolve_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_assign_store_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_enrich_listing", lambda *a, **k: {})
    monkeypatch.setattr(main, "_lookup_artwork", lambda *a, **k: None)
    monkeypatch.setattr(main, "_research_draft", lambda *a, **k: None)
    monkeypatch.setattr(main, "_price_against_comps", lambda *a, **k: None)
    return main


@pytest.fixture
def ai(app, monkeypatch):
    """claude_ai.identify, stubbed to answer with the confidences it is
    handed, one per call, in order."""
    def _install(*levels):
        queue = list(levels)

        def identify(paths, names, strategy="", notes=""):
            return IdentifyResult(
                listing=Listing(title="A polo", images=list(names)),
                confidence=queue.pop(0), raw_observations="")

        monkeypatch.setattr(app.claude_ai, "identify", identify)

    return _install


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


def test_the_uploaders_draft_carries_the_verdict(app, ai):
    """The polled job: what the uploader waits on, and what the editor reads.
    Both the job's answer (the editor's header) and the saved record (the
    card) have to say the same thing."""
    ai("low")
    session_id = app.storage.new_session_id()
    _photos(app.storage.optimized_dir(session_id))

    job = _identify_job(app, session_id)

    assert not job.get("error"), job["error"]
    assert job["result"]["confidence"] == "low"
    assert job["result"]["listing"]["ai_confidence"] == "low"
    assert app.storage.load_listing(session_id)["ai_confidence"] == "low"


def test_starting_over_replaces_the_verdict_with_the_new_one(app, ai):
    """"Start over" is the same worker on the same photos. A re-draft that
    read the label this time must not keep wearing the first pass's "low"."""
    ai("low", "high")
    session_id = app.storage.new_session_id()
    _photos(app.storage.optimized_dir(session_id))

    _identify_job(app, session_id)
    assert app.storage.load_listing(session_id)["ai_confidence"] == "low"
    _identify_job(app, session_id)
    assert app.storage.load_listing(session_id)["ai_confidence"] == "high"


def test_every_bulk_draft_carries_its_own(app, ai, monkeypatch):
    """The queue is the screen this exists for: forty cards, and the seller
    deciding which to open first. Each item's card must show ITS verdict,
    not the batch's first or last."""
    ai("high", "low")
    monkeypatch.setattr(app.claude_ai, "group_photos", lambda images, notes="": {
        "groups": [{"name": f"Item {i + 1}", "indices": [i]}
                   for i in range(len(images))]})
    staging = app.storage.new_session_id()
    _photos(app.storage.original_dir(staging), n=2)
    job_id = app.storage.new_session_id()
    app._register_bulk_job(job_id, {"id": job_id, "done": False,
                                    "error": None, "items": []})

    app._run_bulk_job(job_id, staging, False, None)

    job = app.jobstore.snapshot(job_id)
    assert not job.get("error"), job["error"]
    items = job["items"]
    assert [it["listing"]["ai_confidence"] for it in items] == ["high", "low"]
    for it in items:
        saved = app.storage.load_listing(it["session_id"])
        assert saved["ai_confidence"] == it["listing"]["ai_confidence"]


def test_shop_modes_synchronous_draft_carries_it_too(app, ai):
    """The one path that answers in the request itself. Shop Mode saves the
    draft it shows, so the record behind that card needs the verdict as
    much as the answer does."""
    from fastapi.testclient import TestClient

    ai("medium")
    session_id = app.storage.new_session_id()
    _photos(app.storage.optimized_dir(session_id))

    with TestClient(app.app) as client:
        resp = client.post(f"/api/identify/{session_id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["listing"]["ai_confidence"] == "medium"
    assert app.storage.load_listing(session_id)["ai_confidence"] == "medium"


# --- what must NOT carry it --------------------------------------------------

def test_a_relist_starts_without_a_verdict(app, monkeypatch):
    """The copy is of a listing the seller reviewed, published and sold.
    The AI's doubts about the first draft are not a fact about this one."""
    from fastapi.testclient import TestClient

    sold = {"id": "sold-1", "status": "sold", "user_id": None,
            "listing": Listing(title="Blue lamp", price=25.0, sold_price=20.0,
                               ai_confidence="low").model_dump()}
    written: dict[str, dict] = {}
    monkeypatch.setattr(app.db, "get_listing", lambda lid: sold if lid == "sold-1" else None)
    monkeypatch.setattr(app.db, "upsert_listing",
                        lambda lid, listing, status="draft", user_id=None, when=None:
                        written.__setitem__(lid, listing) or True)

    with TestClient(app.app) as client:
        resp = client.post("/api/listings/sold-1/relist")

    assert resp.status_code == 200, resp.text
    assert resp.json()["listing"]["ai_confidence"] == ""
    assert written[resp.json()["id"]]["ai_confidence"] == ""
    assert sold["listing"]["ai_confidence"] == "low", "the archive was touched"

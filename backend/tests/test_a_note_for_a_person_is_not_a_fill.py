"""A "Fill in details" suggestion is only made where the fill can do something.

The dashboard read any missing_info note as "some fields buyers filter by are
still blank" and offered "Enrich all" for it. But the app appends its own
notes to that list beside the AI's -- a price it raised, a title it suggests,
what to check before listing, where it looked, a category it could not match
-- and none of those is a blank an item specific answers. A seller pressed the
button on such a group on 2026-09-02, paid for a pass that came back "nothing
the photos could answer", and could not tell whether anything had worked,
because the suggestion it was meant to retire never moved.

Two halves. The recommender offers the fill only for notes a specific could
answer, and offers a LOOK ("Check details", which opens the listing and has
no bulk verb) for the rest. And a fresh draft stops carrying notes that its
own draft-time fill already answered, so the suggestion is not made for a
listing whose Size is filled beside a note saying "size".
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from backend import main, storage  # noqa: E402
from backend.models import IdentifyResult, ItemSpecific, Listing  # noqa: E402
from backend.services import recommender  # noqa: E402

ADVICE = [
    "Verify: the edition number in the lower margin",
    "Looked up from: https://example.org/catalogue",
    "The lookup suggests this title: “Hokusai Great Wave Woodblock Print”.",
    "Confirm the price — comparable eBay listings ask $40-$90.",
    "Price raised to the bottom of what this looks worth ($40-$90, sold listings).",
    "CHECK BEFORE LISTING — a first edition of this is worth 100x",
    "For reference, the lookup puts this at $40-$90 (sold listings).",
    "eBay category — we couldn't match one; pick it from the suggestions",
    "item condition — eBay doesn't offer that condition in this category; pick one",
]


def _recs(listing: dict, status: str = "published") -> dict:
    """type -> rec, for one published listing with no eBay metrics."""
    out = recommender.recommend_for(
        {"id": "x", "status": status, "listing": listing, "created_at": None},
        promotion_known=False)
    return {r["type"]: r for r in out}


# ------------------------------------------------------- the recommender

def test_a_note_a_specific_could_answer_earns_the_fill():
    recs = _recs({"title": "Camera", "missing_info": ["exact model number"]})
    assert "specifics" in recs
    assert recs["specifics"]["label"] == "Fill in details"
    assert "verify" not in recs


@pytest.mark.parametrize("note", ADVICE)
def test_advice_to_a_person_earns_a_look_not_a_button(note):
    recs = _recs({"title": "Print", "missing_info": [note]})
    assert "specifics" not in recs, note
    assert recs["verify"]["label"] == "Check details"
    assert recs["verify"]["action"] == "open"
    assert "1 thing the AI left" in recs["verify"]["reason"]


def test_a_mix_still_offers_the_fill_and_counts_the_rest_as_answered_by_it():
    recs = _recs({"title": "Print", "missing_info": ["size", *ADVICE[:2]]})
    assert "specifics" in recs
    assert "verify" not in recs   # one nudge per cause, never both


def test_no_notes_is_no_nudge():
    recs = _recs({"title": "Print", "missing_info": []})
    assert "specifics" not in recs and "verify" not in recs


def test_the_wording_counts():
    recs = _recs({"title": "Print", "missing_info": ADVICE[:3]})
    assert "3 things the AI left" in recs["verify"]["reason"]


# ------------------------------------------------- the notes the fill drops

def test_the_category_note_is_answered_by_the_category():
    """_needs_a_category writes this note; the fill resolves a category
    before it runs, so a draft that now has one must stop saying it has
    none -- the note used to outlive the fix and keep the suggestion up."""
    listing = Listing(title="Print", category_id="550",
                      missing_info=[ADVICE[7], "size"])
    assert main._drop_answered_missing_info(listing) == 1
    assert listing.missing_info == ["size"]


def test_the_category_note_stays_while_there_is_no_category():
    listing = Listing(title="Print", category_id="", missing_info=[ADVICE[7]])
    assert main._drop_answered_missing_info(listing) == 0


# ---------------------------------------------- a fresh draft, at draft time

def _photo(dir_):
    dir_.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 300), (240, 240, 240)).save(dir_ / "img_000.jpg", "JPEG")


def test_a_fresh_draft_does_not_ask_for_what_its_own_fill_answered(monkeypatch):
    """The identify job runs the same fill a "Fill in details" press runs,
    but never dropped the notes it answered -- so every AI draft with a
    filled Size beside a note saying "size" was suggested for a fill that
    then had nothing to do."""
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_resolve_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_research_draft", lambda *a, **k: None)
    monkeypatch.setattr(main, "_price_against_comps", lambda *a, **k: None)
    monkeypatch.setattr(main, "_lookup_artwork", lambda *a, **k: None)

    def identify(paths, names, strategy="", notes=""):
        return IdentifyResult(
            listing=Listing(title="A polo", images=list(names),
                            missing_info=["size", "confirm the signature"]),
            confidence="medium", raw_observations="")

    def enrich(listing, paths, tags=None, progress=None):
        listing.item_specifics.append(ItemSpecific(name="Size", value="M",
                                                   confidence="high"))
        return 1

    monkeypatch.setattr(main.claude_ai, "identify", identify)
    monkeypatch.setattr(main, "_enrich_listing", enrich)

    session_id = storage.new_session_id()
    _photo(storage.optimized_dir(session_id))
    job_id = storage.new_session_id()
    main._register_bulk_job(job_id, {"id": job_id, "kind": "identify",
                                     "done": False, "error": None})
    main._run_identify_job(job_id, session_id, None)

    saved = storage.load_listing(session_id)
    assert saved["missing_info"] == ["confirm the signature"]

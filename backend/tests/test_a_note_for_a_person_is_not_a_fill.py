"""A "Fill in details" suggestion is only made where the fill can do something.

"Fill in details" fills ONE thing: eBay's item specifics for the listing's
category, read off its own photos. The dashboard decided whether to offer it
by looking at missing_info instead -- any note the AI or the app had left --
and that could not work, because a note is evidence of the OPPOSITE. Every
draft runs the same fill at draft time and then drops the notes it answered,
so a note still sitting on a listing is one the fill has already failed to
answer once.

The loop that made: the listing is in the group because of the note, the
seller presses "Enrich all", the pass re-runs and adds nothing, the note is
kept (rightly -- a blank the AI cannot settle is real), so the listing is
still in the group. The count never moved, the seller was charged per
listing, and on 2026-09-03 the report was that the feature "doesn't work at
all" with 35 listings stuck in it. Narrowing WHICH notes counted (2026-09-02)
made the group smaller without breaking the loop.

Membership is now the count of filled item specifics -- what the button
actually fills. A listing whose specifics are blank earns the fill; one whose
specifics are filled earns a nudge to LOOK ("Check details"), whatever notes
it still carries. The fill still drops the notes it answers, so a draft never
asks for what its own draft-time fill settled.
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

# What the AI leaves on a listing it HAS filled in: the things only the person
# holding the item can answer. These are the notes that kept 35 listings in
# the group forever.
FOR_A_PERSON = [
    "Measurements — I can't measure from photos",
    "Authentication for this designer piece",
    "Any chips or cracks not visible in the photos",
]


def _filled(n: int = 4) -> list[dict]:
    """A listing's worth of answered item specifics."""
    return [{"name": f"Aspect {i}", "value": f"value {i}"} for i in range(n)]


def _recs(listing: dict, status: str = "published") -> dict:
    """type -> rec, for one published listing with no eBay metrics."""
    out = recommender.recommend_for(
        {"id": "x", "status": status, "listing": listing, "created_at": None},
        promotion_known=False)
    return {r["type"]: r for r in out}


# ------------------------------------------------------- the recommender

def test_a_listing_with_blank_specifics_earns_the_fill():
    recs = _recs({"title": "Camera", "item_specifics": []})
    assert recs["specifics"]["label"] == "Fill in details"
    assert "None of eBay's item specifics" in recs["specifics"]["reason"]
    assert "verify" not in recs


def test_a_thin_set_of_specifics_still_earns_the_fill():
    recs = _recs({"title": "Camera", "item_specifics": _filled(1)})
    assert "specifics" in recs
    assert "Only 1 of eBay's item specifics is filled" in recs["specifics"]["reason"]


@pytest.mark.parametrize("note", ADVICE + FOR_A_PERSON)
def test_a_filled_listing_earns_a_look_not_a_button(note):
    """The loop, closed. Whatever the note says, a listing whose specifics
    are filled has nothing for the fill to add -- so it is never offered a
    button that would charge for an empty pass and leave the note in place."""
    recs = _recs({"title": "Print", "item_specifics": _filled(),
                  "missing_info": [note]})
    assert "specifics" not in recs, note
    assert recs["verify"]["label"] == "Check details"
    assert recs["verify"]["action"] == "open"
    assert "1 thing the AI left" in recs["verify"]["reason"]


def test_a_filled_listing_with_no_notes_is_no_nudge():
    recs = _recs({"title": "Print", "item_specifics": _filled(),
                  "missing_info": []})
    assert "specifics" not in recs and "verify" not in recs


def test_a_blank_listing_earns_the_fill_even_with_nothing_to_check():
    """The fill is offered for the blank, not for the note -- so a listing
    nobody left a note on still gets it. This is the population the button
    was always meant for: listings synced from eBay that never went through
    an AI draft."""
    recs = _recs({"title": "Print", "item_specifics": [], "missing_info": []})
    assert "specifics" in recs


def test_a_specific_with_no_value_does_not_count_as_filled():
    recs = _recs({"title": "Print",
                  "item_specifics": [{"name": "Brand", "value": "  "},
                                     {"name": "Type", "value": ""}]})
    assert "specifics" in recs


def test_the_wording_counts():
    recs = _recs({"title": "Print", "item_specifics": _filled(),
                  "missing_info": ADVICE[:3]})
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

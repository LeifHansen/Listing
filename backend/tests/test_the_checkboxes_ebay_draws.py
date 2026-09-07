"""eBay's tick-box item specifics, and the values eBay suggests for them.

Two of eBay's aspect fields vary independently and the app ran them together.
CARDINALITY says how many answers fit: MULTI is what eBay draws as CHECKBOXES.
MODE says what the value list MEANS: SELECTION_ONLY makes it law, FREE_TEXT
makes it eBay's own SUGGESTIONS — the values its listing form offers under the
box.

Every checkbox path tested "SELECTION_ONLY and MULTI", so the tick-box aspects
eBay reports as FREE_TEXT + MULTI (Features, Occasion, Style and Material, in a
lot of categories) fell through every one of them: described to the model as a
plain free-text box, drawn in the editor as a single input, and eBay's
suggested values — fetched on every aspect lookup — shown to nobody at all.
"""
from __future__ import annotations

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("fastapi")

from backend.models import ItemSpecific, Listing  # noqa: E402
from backend.services import claude_ai, taxonomy  # noqa: E402


def _aspect(name, *, values=None, multi=False, mode="FREE_TEXT",
            required=False, data_type="STRING"):
    return {"name": name, "required": required, "mode": mode,
            "values": list(values or []),
            "cardinality": "MULTI" if multi else "SINGLE",
            "data_type": data_type, "format": "", "max_length": 0}


# The shape at the heart of this: eBay draws boxes AND allows a value of the
# seller's own.
OPEN_FEATURES = _aspect("Features", multi=True,
                        values=["Breathable", "Pockets", "Lined"])
CLOSED_STYLE = _aspect("Style", multi=True, mode="SELECTION_ONLY",
                       values=["Bomber", "Parka"])
OPEN_MATERIAL = _aspect("Material", values=["Cotton", "Wool", "Denim"])


# ---------------------------------------------- what the model is asked for

def test_a_free_text_multi_aspect_is_still_checkboxes():
    """The defect. eBay reports plenty of its tick boxes as free text, and the
    only thing that decides the SHAPE of the answer is the cardinality."""
    line = claude_ai._aspect_lines([OPEN_FEATURES])
    assert "CHECKBOXES" in line
    assert "repeating this aspect once per value" in line


def test_ebays_suggestions_reach_the_model_on_a_free_text_aspect():
    """They arrive on every aspect lookup and were read by nothing."""
    line = claude_ai._aspect_lines([OPEN_FEATURES])
    assert "eBay suggests: Breathable, Pockets, Lined" in line


def test_a_suggestion_list_is_offered_as_open_not_as_law():
    """The whole difference from SELECTION_ONLY. Told these were the allowed
    values, a model leaves the aspect blank when the real answer isn't among
    them — and here any value is legal."""
    line = claude_ai._aspect_lines([OPEN_FEATURES])
    assert "give your own value when none does" in line
    assert "allowed values" not in line


def test_a_closed_list_still_says_allowed():
    line = claude_ai._aspect_lines([CLOSED_STYLE])
    assert "allowed values: Bomber, Parka" in line
    assert "eBay suggests" not in line


def test_a_single_free_text_aspect_gets_the_suggestions_too():
    line = claude_ai._aspect_lines([OPEN_MATERIAL])
    assert "free text" in line and "eBay suggests: Cotton, Wool, Denim" in line
    assert "CHECKBOXES" not in line, "one answer, so no tick boxes"


def test_a_plain_free_text_aspect_is_unchanged():
    line = claude_ai._aspect_lines([_aspect("Care Instructions")])
    assert line == '- "Care Instructions" (free text)'


def test_a_multi_aspect_with_no_values_still_says_several_answers():
    """Nothing to tick from, but still not a one-answer box."""
    line = claude_ai._aspect_lines([_aspect("Features", multi=True)])
    assert "CHECKBOXES" in line and "one entry per value" in line


# ------------------------------------------- an off-list tick is still legal

def test_a_value_off_an_open_list_survives():
    """A closed list refuses anything not on it; an open one must not, or the
    seller's own answer is silently dropped on the way to eBay."""
    assert taxonomy.coerce_aspect_value("Reflective", OPEN_FEATURES) == "Reflective"
    assert taxonomy.coerce_aspect_value("Reflective", CLOSED_STYLE) is None


def test_an_open_lists_own_wording_still_wins_where_it_fits():
    """Matching to eBay's spelling is worth doing even when off-list is legal."""
    assert taxonomy.coerce_aspect_value("breathable", OPEN_FEATURES) == "Breathable"


def test_several_ticks_come_back_from_one_free_text_multi_aspect():
    """End to end through the validator: the model ticks three boxes on an
    aspect eBay called free text, and all three become values — including the
    one eBay never suggested."""
    out = claude_ai._validate_specifics({"specifics": [
        {"name": "Features", "value": "Breathable", "confidence": "high"},
        {"name": "Features", "value": "pockets", "confidence": "medium"},
        {"name": "Features", "value": "Reflective", "confidence": "medium"},
    ]}, [OPEN_FEATURES])
    assert [s.value for s in out] == ["Breathable", "Pockets", "Reflective"]


def test_a_comma_joined_answer_still_splits_into_ticks():
    out = claude_ai._validate_specifics({"specifics": [
        {"name": "Features", "value": "Breathable, Reflective"},
    ]}, [OPEN_FEATURES])
    assert [s.value for s in out] == ["Breathable", "Reflective"]


# ----------------------------------- one ticked box is not an answered aspect

def _listing(**kw):
    return Listing(title="Jacket", category_id="57988", **kw)


def test_a_half_ticked_checkbox_aspect_is_offered_again():
    """The reason these reached eBay with one box ticked. Holding ANY value
    read as answered, so the coverage pass was never shown the aspect and the
    remaining boxes were never asked about."""
    listing = _listing(item_specifics=[
        ItemSpecific(name="Features", value="Pockets", confidence="medium")])
    aspects = [OPEN_FEATURES, OPEN_MATERIAL]

    assert [a["name"] for a in taxonomy.fillable_blanks(listing, aspects)] \
        == ["Material"], "the plain count answers 'which fields are empty'"
    assert [a["name"] for a in taxonomy.fillable_blanks(
        listing, aspects, top_up_multi=True)] == ["Features", "Material"]


def test_a_single_value_aspect_is_never_topped_up():
    """One answer is the whole answer there — asking again would only invite
    the model to overwrite it."""
    listing = _listing(item_specifics=[
        ItemSpecific(name="Material", value="Cotton", confidence="medium")])
    assert taxonomy.fillable_blanks(
        listing, [OPEN_MATERIAL], top_up_multi=True) == []


def test_the_sellers_own_ticks_are_the_last_word():
    """A row with no confidence flag is one the seller typed or confirmed.
    Topping that up would let a guess join an answer they had already given."""
    listing = _listing(item_specifics=[
        ItemSpecific(name="Features", value="Pockets", confidence="")])
    assert taxonomy.fillable_blanks(
        listing, [OPEN_FEATURES], top_up_multi=True) == []


def test_the_prompt_says_which_boxes_are_already_ticked():
    """Asked about a half-ticked aspect with no word about the ticks, the pass
    re-offers the value already there and adds nothing."""
    line = claude_ai._aspect_lines([OPEN_FEATURES], {"features": ["Pockets"]})
    assert "already ticked: Pockets" in line
    assert "add any OTHERS that apply" in line


def test_a_blank_aspect_is_not_told_about_ticks_it_does_not_have():
    line = claude_ai._aspect_lines([OPEN_FEATURES], {"material": ["Cotton"]})
    assert "already ticked" not in line


# ------------------------------------- the pass the top-up actually reaches

def test_the_coverage_pass_is_handed_the_half_ticked_aspect_and_its_ticks(
        monkeypatch, tmp_path):
    """End to end. The pass is asked about Features (one box ticked), told
    which box that is, and its extra ticks land beside the first."""
    from backend import main

    photo = tmp_path / "1.jpg"
    photo.write_bytes(b"x")
    listing = _listing(item_specifics=[
        ItemSpecific(name="Features", value="Pockets", confidence="medium")])
    seen = {}

    def fake(image_paths, listing_, blanks, held=None):
        seen["blanks"] = [a["name"] for a in blanks]
        seen["held"] = dict(held or {})
        return claude_ai._validate_specifics({"specifics": [
            {"name": "Features", "value": "Breathable", "confidence": "medium"},
            {"name": "Features", "value": "Lined", "confidence": "medium"},
        ]}, blanks)

    monkeypatch.setattr(main.claude_ai, "fill_missing_aspects", fake)
    monkeypatch.setattr(main, "_coverage_on", lambda: True)

    added = main._cover_remaining_specifics(
        listing, [photo], [OPEN_FEATURES, OPEN_MATERIAL])

    assert "Features" in seen["blanks"]
    assert seen["held"] == {"features": ["Pockets"]}
    assert added == 2
    assert sorted(s.value for s in listing.item_specifics
                  if s.name == "Features") == ["Breathable", "Lined", "Pockets"]

"""When eBay refuses over an item specific, say WHICH — or say eBay's words.

The parser only read the aspect name out of the error's `parameters`. eBay
does not always send those — a REVISE of a live listing often carries the
name in the sentence alone — and the fallback was boilerplate: "Add the
required item specifics (e.g. Brand, Type, Size) under Item specifics."

On 2026-09-03 a seller read exactly that, on a listing whose Brand, Type and
Size were all filled in, beside a card reading "Required to publish 5/5 —
all set, nothing here is blocking". Their words: "I CAN'T FUCKING GET IT TO
FUCKING PUBLISH." The app was holding eBay's own sentence the whole time and
rendering a guess instead.

Two changes. The name is now read out of the sentence when no parameter
carries it, so the editor can ring the field. And when nothing names it,
eBay's own words go in the title and the fix — the same rule the error-240
branch already followed, for the same reason: the seller's next move depends
on what eBay said, and a reason we do not render is a reason they never see.
"""
from __future__ import annotations

import pytest

from backend.ebay_errors import explain  # noqa: E402


def _refusal(long_message, message="Missing required item specific.", params=None):
    return explain({"errorId": "21919303", "message": message,
                    "longMessage": long_message, "parameters": params or []})


# ----------------------------------------------- the name, from wherever

def test_a_parameter_still_wins():
    out = _refusal("The item specific Item Height is missing.",
                   params=[{"value": "Item Height"}])
    assert out["fields"] == ["Item Height"]
    assert out["title"] == "Missing required item specific: Item Height"


@pytest.mark.parametrize("sentence, name", [
    ("The item specific Unit Quantity is missing.", "Unit Quantity"),
    ("The item specific California Prop 65 Warning is required.",
     "California Prop 65 Warning"),
    ("The listing is missing the required item specific 'Type'.", "Type"),
    ("Missing required item specific: Country/Region of Manufacture",
     "Country/Region of Manufacture"),
    ("The aspect Unit Type is required for this category.", "Unit Type"),
])
def test_the_name_is_read_out_of_the_sentence_when_no_parameter_carries_it(
        sentence, name):
    out = _refusal(sentence)
    assert out["fields"] == [name], sentence
    assert out["title"].endswith(name)
    # The editor rings the field it names, so the target has to be the card
    # holding it (see SpecificsCard / fixTargetFor).
    assert out["target"] == "specifics"


def test_the_trailing_sentence_is_not_part_of_the_name():
    """"Unit Quantity is missing" is not an aspect, and asking a seller to
    fill in a field by that name is the same dead end as the boilerplate."""
    assert _refusal("The item specific Unit Quantity is missing.")["fields"] \
        == ["Unit Quantity"]


def test_a_name_that_legitimately_contains_a_small_word_keeps_it():
    assert _refusal("Missing required item specific: Country/Region of Manufacture"
                    )["fields"] == ["Country/Region of Manufacture"]


# ------------------------------------- and when eBay names nothing at all

def test_ebays_own_words_are_shown_rather_than_three_guessed_field_names():
    said = "This listing is missing one or more required item specifics."
    out = _refusal(said)
    assert out["fields"] == []
    assert said in out["title"]
    assert said in out["fix"]
    # The old boilerplate named three fields that were probably already
    # filled; that is what sent the seller round the loop.
    assert "e.g. Brand, Type, Size" not in out["fix"]


def test_a_sentence_with_no_aspect_in_it_yields_no_field():
    """Better nothing than "Is": a wrong name rings the wrong box."""
    assert _refusal("The item specific is missing.")["fields"] == []


def test_the_fix_says_ebay_can_add_new_required_specifics():
    """The case that produced the report: a listing that published fine,
    refused later over an aspect the category did not require then."""
    out = _refusal("This listing is missing one or more required item specifics.")
    assert "published before" in out["fix"] or "from time to time" in out["fix"]


# ---------------------------------------------------- nothing else moved

def test_a_dimension_aspect_still_gets_its_units_advice():
    out = _refusal("The item specific Item Height is missing.",
                   params=[{"value": "Item Height"}])
    assert "number and unit" in out["fix"]
    assert "Package size" in out["fix"]

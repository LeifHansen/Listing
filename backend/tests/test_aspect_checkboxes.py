"""Filling eBay's multi-select item specifics — the "checkbox" aspects.

A SELECTION_ONLY aspect with MULTI cardinality is what eBay renders as tick
boxes (Features, Style, Season...). They were reaching listings empty, or with
one box ticked at most: the prompt asked for one value per aspect name, only
the first 40 allowed values were ever shown to the model, a comma-joined answer
matched nothing and was dropped whole, and the merge skipped any aspect that
already held a value. These are the rules that keep every applicable box
ticked.
"""
from __future__ import annotations

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from backend import main  # noqa: E402
from backend.models import ItemSpecific, Listing  # noqa: E402
from backend.services import claude_ai  # noqa: E402


def _aspect(name, *, values=None, multi=False, mode="SELECTION_ONLY",
            required=False, data_type="STRING"):
    return {"name": name, "required": required, "mode": mode,
            "values": list(values or []),
            "cardinality": "MULTI" if multi else "SINGLE",
            "data_type": data_type, "format": "", "max_length": 0}


FEATURES = _aspect("Features", multi=True,
                   values=["Breathable", "Pockets", "Water Resistant", "Lined"])
FIT = _aspect("Fit", values=["Regular", "Slim", "Relaxed"])


# --- what the model is told ---------------------------------------------------

def test_multi_choice_aspect_is_presented_as_checkboxes():
    line = claude_ai._aspect_lines([FEATURES])
    assert "CHECKBOXES" in line
    assert "repeating this aspect once per value" in line
    assert "Water Resistant" in line


def test_single_choice_aspect_asks_for_exactly_one():
    line = claude_ai._aspect_lines([FIT])
    assert "choose exactly one of: Regular, Slim, Relaxed" in line
    assert "CHECKBOXES" not in line


def test_long_value_list_says_it_was_truncated():
    """The old 40-value cap hid the rest silently, so nothing past it could be
    picked. Values beyond the cap are still allowed — the model is told to give
    them verbatim, and coerce_aspect_value matches against the full list."""
    many = [f"Value {i}" for i in range(claude_ai._MAX_SHOWN_VALUES + 25)]
    line = claude_ai._aspect_lines([_aspect("Pattern", values=many, multi=True)])
    assert f"Value {claude_ai._MAX_SHOWN_VALUES - 1}" in line
    assert "+25 more allowed values not shown" in line


# --- what comes back ----------------------------------------------------------

def _filled(specifics, aspects):
    return claude_ai._validate_specifics({"specifics": specifics}, aspects)


def test_every_tick_survives_validation():
    out = _filled([
        {"name": "Features", "value": "Pockets", "confidence": "high"},
        {"name": "Features", "value": "Lined", "confidence": "medium"},
        {"name": "Features", "value": "Breathable", "confidence": "medium"},
    ], [FEATURES])
    assert [s.value for s in out] == ["Pockets", "Lined", "Breathable"]
    assert [s.confidence for s in out] == ["high", "medium", "medium"]


def test_comma_joined_ticks_are_split_back_apart():
    """The prompt forbids joining, but when the model does it anyway the joined
    string matches no allowed value — the whole aspect used to be dropped."""
    out = _filled([{"name": "Features", "value": "Pockets, Lined"}], [FEATURES])
    assert [s.value for s in out] == ["Pockets", "Lined"]


def test_allowed_value_containing_a_comma_is_not_split():
    aspect = _aspect("Type", multi=True, values=["Shirt, Blouse", "Shirt"])
    out = _filled([{"name": "Type", "value": "Shirt, Blouse"}], [aspect])
    assert [s.value for s in out] == ["Shirt, Blouse"]


def test_off_list_and_duplicate_ticks_are_dropped():
    out = _filled([
        {"name": "Features", "value": "Pockets"},
        {"name": "Features", "value": "pockets"},   # same tick, different case
        {"name": "Features", "value": "Bluetooth"},  # not an allowed value
    ], [FEATURES])
    assert [s.value for s in out] == ["Pockets"]


def test_single_value_aspect_still_takes_only_its_first_value():
    out = _filled([
        {"name": "Fit", "value": "Slim"},
        {"name": "Fit", "value": "Relaxed"},
    ], [FIT])
    assert [s.value for s in out] == ["Slim"]


def test_ticks_are_capped_at_ebays_thirty():
    aspect = _aspect("Features", multi=True,
                     values=[f"F{i}" for i in range(40)])
    out = _filled([{"name": "Features", "value": f"F{i}"} for i in range(40)],
                  [aspect])
    assert len(out) == 30


# --- merging into the listing -------------------------------------------------

def _listing(specifics):
    return Listing(title="Wool Overcoat", item_specifics=specifics)


def test_multi_aspect_is_topped_up_not_skipped():
    """One AI value left over from the first vision pass used to block every
    further tick — which is why the checkbox aspects shipped with one box."""
    listing = _listing([ItemSpecific(name="Features", value="Pockets",
                                     confidence="medium")])
    added = main._merge_filled_specifics(listing, [
        ItemSpecific(name="Features", value="Pockets", confidence="medium"),
        ItemSpecific(name="Features", value="Lined", confidence="medium"),
    ], [FEATURES])
    assert added == 1
    assert [s.value for s in listing.item_specifics] == ["Pockets", "Lined"]


def test_a_value_the_seller_owns_is_never_topped_up():
    """confidence "" means the seller typed or confirmed it (models.ItemSpecific);
    their answer for the aspect stands as given."""
    listing = _listing([ItemSpecific(name="Features", value="Pockets")])
    added = main._merge_filled_specifics(
        listing, [ItemSpecific(name="Features", value="Lined",
                               confidence="medium")], [FEATURES])
    assert added == 0
    assert [s.value for s in listing.item_specifics] == ["Pockets"]


def test_single_aspect_keeps_the_value_it_has():
    listing = _listing([ItemSpecific(name="Fit", value="Slim",
                                     confidence="medium")])
    added = main._merge_filled_specifics(
        listing, [ItemSpecific(name="Fit", value="Relaxed",
                               confidence="medium")], [FIT])
    assert added == 0
    assert [s.value for s in listing.item_specifics] == ["Slim"]


def test_empty_aspect_takes_every_tick():
    added = main._merge_filled_specifics(listing := _listing([]), [
        ItemSpecific(name="Features", value="Lined", confidence="medium"),
        ItemSpecific(name="Features", value="Breathable", confidence="high"),
        ItemSpecific(name="Fit", value="Slim", confidence="medium"),
        ItemSpecific(name="Fit", value="Relaxed", confidence="medium"),
    ], [FEATURES, FIT])
    assert added == 3
    assert [s.value for s in listing.item_specifics] == [
        "Lined", "Breathable", "Slim"]

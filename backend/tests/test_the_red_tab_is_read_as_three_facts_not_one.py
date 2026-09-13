"""The red tab dates a pair three separate ways, and one of them was backwards.

The denim rule read the tab as one fact: the lettering, capital E or not. A
red tab collectors' guide reads it as three, and each dates a pair on its own
-- which face(s) carry the lettering, whether an ® sits beside the name, and
the shape of the E. The rule had the first of those INVERTED. It said
lettering on BOTH faces was the older tab, "roughly before the mid-1950s".
It is the other way round: from 1936 until the early 1950s "LEVI'S" was
stitched on ONE face and the reverse was blank, and the double-sided tab that
nearly every pair since carries arrived around 1951-1954. So the rule dated
the oldest and most valuable tab there is as the newer one, and every ordinary
double-sided tab as pre-1955.

Three more facts ride in with the correction, each of them a price claim the
rule could not previously make or could previously make wrongly:

  * a capital E is no longer proof of a vintage pair. LVC has used Big E
    tabs for years and the Levi's Premium line has used one since 2018, so
    what settles it is the INSIDE of the garment -- a modern care tag, an
    MMYY date code, a Made in Japan or post-2003 label. "Big E" written
    about a 2019 pair is the same false claim as "selvedge" on a hem that
    was never turned up.
  * single-sided came BACK in the mid-1980s, so single-sided is only the
    early tab when the rest of the pair agrees. Beside a lowercase e, or a
    care tag, it is a 1980s pair.
  * the ® arrived with the double-sided change, so "LEVI'S" with no ® is
    earlier than "LEVI'S®" -- and a tab carrying the ® and NO NAME is a
    modern trademark-only tab, which is also the one tab LVC never uses.

And the tab's COLOUR names a LINE, not an era. The rule called a white tab
"a later line"; a white tab is corduroy and "Levi's for Gals", the first
women's line, in the 1960s and 1970s.
"""
from __future__ import annotations

import pytest

from backend.services.listing_prompt import (
    DENIM_TRANSCRIBE_LINES,
    VINTAGE_DENIM_RULE,
)


def _flat(text: str) -> str:
    """The rule with its line wrapping removed, so a test pins the WORDS a
    pass reads and not the column the prompt happens to wrap at."""
    return " ".join(text.split())


# --- the fact that was backwards -------------------------------------------

def test_the_oldest_tab_is_lettered_on_one_face_not_on_both():
    """The inversion, pinned in both directions so it cannot come back."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "The oldest tabs are lettered on ONE SIDE ONLY" in rule
    assert "from 1936 until the early 1950s" in rule
    assert "the reverse is blank" in rule
    # And the newer one, with the years the guides agree on.
    assert "Around 1951-1954 Levi's went to a DOUBLE-SIDED tab" in rule
    # The old, wrong claim is gone.
    assert "Lettering on BOTH faces of the tab is older" not in rule


def test_a_single_sided_tab_is_checked_against_the_rest_of_the_pair():
    """Single-sided came back in the mid-1980s, so on its own it dates
    nothing -- the opposite error to the one above, and just as expensive."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "single-sided tabs came BACK in the mid-1980s" in rule
    assert "a single-sided tab beside a lowercase e, or a care tag, is a " \
           "1980s pair or later" in rule
    # A face you cannot see is not a face you can call blank.
    assert "when only one face is in frame say exactly that" in rule


# --- a capital E is not proof of a vintage pair ----------------------------

def test_big_e_is_not_claimed_from_the_letter_alone():
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "a capital E is NOT proof of a vintage pair on its own" in rule
    assert "from 2018 so does the Levi's Premium line" in rule
    # What settles it, and what to write when it says modern.
    assert "a modern care tag, a four-digit MMYY date code, a \"Made in " \
           "Japan\" or post-2003 label" in rule
    assert "Big E tab (Levi's Premium/LVC reproduction)" in rule


def test_the_lettering_still_dates_the_pair_when_nothing_contradicts_it():
    """The correction must not cost the rule the claim that pays: 1971 is
    still the changeover, and Big E is still what buyers search."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "was used until 1971" in rule
    assert "lowercase e is 1971 onward" in rule
    assert "most searched vintage Levi's term" in rule
    # And the half that keeps it honest: no letter read, no claim written.
    assert "Never write it when you cannot read the letter." in rule


# --- the registered mark, and the tab with no name on it -------------------

def test_the_registered_mark_is_its_own_dating_fact():
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "arrived on the tab with the double-sided change in the early " \
           "1950s" in rule
    assert "\"LEVI'S\" with NO ® beside it is earlier than \"LEVI'S®\"" in rule


def test_a_blank_tab_dates_nothing_and_rules_a_reproduction_out():
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "A tab with the ® AND NO NAME on it is a modern trademark-only " \
           "tab" in rule
    assert "it dates nothing and is never \"Big E\"" in rule
    # The useful half: LVC must carry the name, so a blank tab is not one.
    assert "the one tab LVC does not use" in rule


# --- the colour is a line, not an era --------------------------------------

def test_the_tab_colour_names_the_line_and_the_years_each_ran():
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "THE TAB'S COLOUR names the LINE, and the line is not the era" in rule
    # Orange: the fashion line, now with the year it ended.
    assert "fashion line from the 1960s until 1999" in rule
    # Silver: named, because buyers search the cut by name.
    assert "SilverTab" in rule
    assert "late 1980s through the 1990s" in rule


def test_a_white_tab_is_not_described_as_a_late_pair():
    """The rule used to call it "a later line". It is corduroy and the first
    women's line, in the 1960s and 1970s."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "Levi's for Gals" in rule
    assert "1960s and 1970s" in rule
    assert "NOT a sign of a late pair" in rule
    assert "A white, silver or black tab is a later line" not in rule


# --- the tab's own birthday ------------------------------------------------

def test_no_tab_at_all_is_two_possibilities_not_one():
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "introduced in 1936" in rule
    assert "either pre-1936 or a removed tab" in rule


# --- and what the transcribe pass now writes down --------------------------

def test_the_transcribe_pass_records_all_three_tab_facts():
    """The specifics fill quotes these the way it quotes a barcode, so a
    fact the transcribe pass does not write down cannot reach a listing."""
    lines = _flat(DENIM_TRANSCRIBE_LINES)
    assert "whether the E is a capital (Big E) or lowercase" in lines
    assert "whether an ®, ™ or © is beside it" in lines
    assert "which faces are lettered" in lines
    assert "one side only, both sides, or only one side visible" in lines


def test_every_vision_pass_still_reads_the_same_denim_text():
    """Skipped where the SDK is not installed (CI's light job)."""
    pytest.importorskip("anthropic")
    pytest.importorskip("fastapi")
    from backend.services import claude_ai

    assert VINTAGE_DENIM_RULE in claude_ai._TAG_TRANSCRIBE_ASK
    assert VINTAGE_DENIM_RULE in claude_ai._ASPECTS_SYSTEM
    assert DENIM_TRANSCRIBE_LINES in claude_ai._TAG_TRANSCRIBE_ASK
    # The aspects fill dates the era off the tab, and carries the same
    # caveat rather than a flat "Big E = before 1971".
    fill = _flat(claude_ai._ASPECTS_FILL_SCHEMA)
    assert "Big E = before 1971, unless a modern care tag inside says the " \
           "pair is an LVC or Levi's Premium reproduction" in fill

"""What order a listing title puts its words in.

eBay weights the front of a title most heavily, so the order is the SEO: the
words that identify this one item go first, and the words thousands of other
listings also use go last. That rule lives in a prompt, which is the kind of
thing that gets reworded by someone fixing an unrelated line, so these tests
pin the sequence rather than the prose.

They import services.listing_prompt, never services.claude_ai — the Anthropic
SDK is not installed in CI, and a test that importorskips it is a test that
never runs where it matters.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.services import listing_prompt

# What each numbered slot must still be about, in the order they must appear.
# The fragment is matched case-insensitively against that slot's text.
EXPECTED_SLOTS = [
    "brand",                      # 1. the maker/artist/pattern name
    "model",                      # 2. the exact model, pattern or number
    "what the thing is",          # 3. the noun a buyer searches
    "specifics a buyer filters",  # 4. size, colour, material, quantity, year
    "condition wording",          # 5. NWT, sealed, excellent condition
    "general descriptive words",  # 6. vintage, antique, rare — last
]

# Named as "general" in slot 6, and so never allowed to open a title.
GENERAL_WORDS = ("vintage", "antique", "retro", "rare")


def _title_rule() -> str:
    """The title rule alone, up to the next top-level rule in the schema."""
    text = listing_prompt.LISTING_SCHEMA
    start = text.index("- Title must be")
    return text[start:text.index("\n- Description:", start)]


def _slots() -> list[tuple[str, str]]:
    """The numbered slots as (number, text), one entry per slot."""
    body = re.sub(r"\n {5,}", " ", _title_rule())  # unwrap continuation lines
    return re.findall(r"^  (\d+)\. (.+)$", body, re.M)


def test_slots_are_numbered_without_a_gap():
    numbers = [n for n, _ in _slots()]
    assert numbers == [str(i + 1) for i in range(len(EXPECTED_SLOTS))]


def test_each_slot_still_asks_for_the_same_thing():
    slots = _slots()
    assert len(slots) == len(EXPECTED_SLOTS)
    for (number, text), expected in zip(slots, EXPECTED_SLOTS):
        assert expected in text.lower(), f"slot {number} no longer covers {expected!r}"


def test_order_holds_in_the_prose_too():
    """A renumbered list is not enough — the model reads top to bottom."""
    rule = _title_rule().lower()
    positions = [rule.index(fragment) for fragment in EXPECTED_SLOTS]
    assert positions == sorted(positions), "the slots are out of order in the text"


def test_condition_sits_between_the_sizing_and_the_general_words():
    rule = _title_rule().lower()
    assert (rule.index("specifics a buyer filters")
            < rule.index("condition wording")
            < rule.index("general descriptive words"))


def test_general_words_are_named_and_barred_from_the_front():
    rule = _title_rule()
    assert "Never START a title with a general word" in rule
    lowered = rule.lower()
    for word in GENERAL_WORDS:
        assert word in lowered, f"{word!r} is no longer named as a general word"


def test_the_80_char_budget_is_trimmed_from_the_back():
    """Adding words to the front of a capped title is only safe if the model
    knows which end to cut; otherwise it drops the brand to fit."""
    rule = _title_rule()
    assert "cut from" in rule and "BACK" in rule


def test_a_refine_cannot_undo_the_order():
    """"Make it shorter" is exactly the instruction that would otherwise trade
    the identifying words for the generic ones."""
    rule = listing_prompt.REFINE_ORDER_RULE.lower()
    assert "lead" in rule
    assert (rule.index("brand") < rule.index("model")
            < rule.index("size") < rule.index("condition"))
    assert rule.index("condition") < rule.index("vintage")
    assert "end" in rule


def test_identify_still_feeds_the_model_this_schema():
    """The prompt moved out of claude_ai.py; a constant nothing sends is a
    rule that silently stops applying. Read as text — the SDK that module
    imports is not installed here. Matched loosely on purpose: this should
    fail when the schema is unhooked from the prompt, not when someone
    reflows the import."""
    source = (Path(__file__).resolve().parents[1]
              / "services" / "claude_ai.py").read_text()
    assert re.search(r"from \.listing_prompt import\b", source)
    # The _IDENTIFY_SYSTEM assignment, up to the next line starting in
    # column 0 — however its continuation lines happen to be wrapped.
    block = re.split(r"\n(?=\S)", source[source.index("_IDENTIFY_SYSTEM"):])[0]
    assert "LISTING_SCHEMA" in block, "identify's prompt no longer carries the schema"
    assert "REFINE_ORDER_RULE" in source

"""How much a listing description says, and what it spends its words on.

The description is the field with no marketplace ceiling worth respecting
(eBay allows 500,000 characters), it is indexed by eBay's own search as well
as by Google, and it is the last thing a buyer reads before committing. The
prompt used to ask for "2-4 short paragraphs", which is a blurb; it now asks
for a full sectioned body several hundred words long.

That is a rule living in a prompt — the kind of thing a later edit reflows
away without noticing — so these tests pin the parts that carry the weight:
that a length is asked for at all, that the sections stay named and ordered,
that the SEO instructions still forbid the two ways length goes wrong
(inventing facts, stuffing keywords), and that a refine cannot quietly hand
back the blurb.

Like test_title_prompt_order, this imports services.listing_prompt and reads
services/claude_ai.py as text: the Anthropic SDK is not installed in CI, and
a test that importorskips it is a test that never runs where it matters.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.services import listing_prompt

CLAUDE_AI = (Path(__file__).resolve().parents[1] / "services" / "claude_ai.py")

# The sections the body must be built from, in the order the prompt lists
# them. Overview and the closing carry no heading; the four in the middle are
# matched as the literal heading text the model is told to write, because
# mapping_etsy keys off one of them ("Condition:") and a buyer scans them.
SECTION_HEADINGS = [
    "Key Details:",
    "Condition:",
    "Measurements:",
    "Why You'll Love It:",
]


def _description_rule() -> str:
    """The description rule alone, up to the next top-level rule."""
    text = listing_prompt.LISTING_SCHEMA
    start = text.index("\n- Description:")
    return text[start:text.index("\n- ALWAYS estimate", start)]


def _flat() -> str:
    """The rule as one line: it is a wrapped prompt, so a phrase the model
    reads as a sentence is split across lines in the source."""
    return re.sub(r"\s+", " ", _description_rule())


def _lengths() -> list[int]:
    """Every character count named in the description rule."""
    return [int(n.replace(",", "").replace("_", ""))
            for n in re.findall(r"([\d][\d,_]*)\s*(?=characters)", _flat())]


def test_the_rule_asks_for_a_long_description_in_characters():
    """A model told only "be thorough" writes four sentences. The instruction
    that changes the output is a number."""
    rule = _flat()
    assert "NO character limit" in rule
    lengths = _lengths()
    assert lengths, "the description rule no longer names a length"
    # Whatever the range becomes, the floor of it has to be past a blurb.
    assert max(lengths) >= 1_500, "the description ceiling is back to a blurb"


def test_the_length_is_never_a_licence_to_invent():
    """The failure mode of asking for 500 words about a thrifted mug is a
    model that makes some up, so the ban has to sit with the number."""
    rule = _flat().lower()
    assert "never pad with invented facts" in rule
    assert "missing_info" in rule


def test_the_sections_are_named_and_in_order():
    rule = _flat()
    positions = []
    for heading in SECTION_HEADINGS:
        assert f'"{heading}"' in rule, f"the {heading!r} section is gone"
        positions.append(rule.index(f'"{heading}"'))
    assert positions == sorted(positions), "the sections are out of order"


def test_the_opening_is_still_the_item_and_not_an_adjective():
    """Unchanged from the short version, and the reason the rule exists: the
    first words are the search snippet."""
    rule = _flat()
    assert "FIRST WORDS must be item-specific" in rule
    lowered = rule.lower()
    for word in ("vintage", "antique", "retro", "rare"):
        assert word in lowered, f"{word!r} is no longer named as an opener to avoid"


def test_measurements_are_read_and_never_estimated():
    """The one section that invites invention: a model that estimates a chest
    measurement from a photo produces a return, not a sale."""
    rule = _flat()
    assert "never estimate a measurement here" in rule
    assert "exact measurements" in rule


def test_the_keyword_half_is_actually_asked_for():
    rule = _flat()
    assert "KEYWORDS" in rule
    lowered = rule.lower()
    for asked in ("abbreviations", "singular and plural", "long-tail"):
        assert asked in lowered, f"the keyword rule no longer covers {asked!r}"


def test_keyword_stuffing_is_named_and_forbidden():
    """eBay demotes or removes for exactly this, so the SEO instruction that
    does not forbid it is a liability rather than an improvement."""
    rule = _flat()
    assert "NEVER keyword-stuff" in rule
    lowered = rule.lower()
    assert "brands the item is not" in lowered
    assert "similar to" in lowered


def test_no_shipping_or_returns_promises_in_the_body():
    """Account-level settings own these; a sentence here can contradict them,
    and the same rule already governs missing_info."""
    rule = _flat().lower()
    assert "never state shipping speed" in rule
    for word in ("handling time", "returns", "payment"):
        assert word in rule


def test_a_refine_cannot_quietly_shorten_it():
    """"Change the price" must not come back with a two-line description."""
    rule = listing_prompt.REFINE_ORDER_RULE.lower()
    assert "long and keyword-rich" in rule
    assert "unless the seller asks for it shorter" in rule


def _max_tokens(func: str) -> int:
    """The max_tokens the named function asks the model for."""
    source = CLAUDE_AI.read_text()
    block = re.split(r"\n(?=\S)", source[source.index(f"def {func}("):])[0]
    return int(re.search(r"max_tokens=(\d+)", block).group(1))


def test_the_reply_has_room_for_the_description_it_asks_for():
    """A description that overruns the cap is not a short description — it is
    a truncated JSON object, which reaches the seller as a failed draft."""
    for func in ("identify", "refine"):
        assert _max_tokens(func) >= 8192, f"{func}() cannot fit the body it asks for"

"""What a listing title FILLS, and what may never appear in one.

test_title_prompt_order pins the ORDER the words go in. This pins the other
half of the same field, from eBay's own SEO guidance: the 80 characters are
the most heavily weighted thing eBay indexes, so a title that uses sixteen of
them has thrown away the rest — "Levi's 501 jeans" is not findable by size,
fit, colour or era, and the same pair listed with all four is. The prompt
already knew how to CUT a long title and said nothing about a short one.

The bans are the same guidance's other half: Cassini ignores caps, emoji and
punctuation runs, buyers read them as spam, and a SKU in a public title is
characters spent on a string nobody types.

Three passes can write the title a buyer ends up seeing — the first draft, the
research lookup, and the art lookup, the last two by REPLACING a hedged one
outright — so the rules have to reach all three. TITLE_BUDGET_AND_BANS is the
one copy the second passes carry.

Imports services.listing_prompt and reads services/claude_ai.py as text: the
Anthropic SDK is not installed in CI, and a test that importorskips it is a
test that never runs where it matters.
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.services import listing_prompt

CLAUDE_AI = (Path(__file__).resolve().parents[1] / "services" / "claude_ai.py")

# Never in a public eBay title. Each is named in the prompt as a ban, and each
# is a different way the title stops being searchable: shouting, spam
# punctuation, hype, the seller's own policies, and an internal code.
BANNED_IN_A_TITLE = ("ALL-CAPS", "emoji", "asterisk", "L@@K", "MUST SEE",
                     "SHIPPING", "SKU")


def _flat(text: str) -> str:
    """One long line. Every phrase below is prose the prompt wraps, and a
    rewrap is not a rule change."""
    return re.sub(r"\s+", " ", text)


def _title_rule() -> str:
    """The title rule alone, up to the next top-level rule in the schema."""
    text = listing_prompt.LISTING_SCHEMA
    start = text.index("- Title must be")
    return text[start:text.index("\n- Description:", start)]


def test_the_draft_is_told_to_spend_the_whole_budget():
    """The gap this closes: the rule knew how to shorten a long title and had
    nothing to say about one that stopped at sixteen characters."""
    rule = _title_rule()
    assert "SPEND THE WHOLE BUDGET" in rule
    assert "70-80" in rule


def test_a_full_title_is_still_a_true_one():
    """The other direction. A budget with no truth rule is an instruction to
    pad, and eBay's keyword-spam policy demotes exactly that."""
    rule = _flat(_title_rule()).lower()
    assert "true of this item" in rule
    assert "never repeat a word" in rule
    assert "never name a brand this item is not" in rule


def test_a_long_title_is_still_cut_from_the_back():
    """Unchanged by the budget: the front of the title is what identifies the
    item, so trimming never starts there."""
    rule = _flat(_title_rule())
    assert "cut from the BACK" in rule
    assert "never the brand, model or size at the front" in rule


def test_every_ban_is_named_in_the_draft_rule():
    rule = _flat(_title_rule()).lower()
    for banned in BANNED_IN_A_TITLE:
        assert banned.lower() in rule, (
            f"the title rule no longer bans {banned!r}")


def test_a_number_printed_on_the_item_is_not_an_internal_code():
    """The ban is on the seller's own bookkeeping, not on catalogue numbers —
    an MPN or a card number is one of the strongest keywords a title has."""
    rule = _flat(_title_rule())
    assert "PRINTED ON THE ITEM" in rule
    lowered = rule.lower()
    for wanted in ("model", "mpn", "pattern number", "card number"):
        assert wanted in lowered


def test_the_second_passes_carry_the_same_rules():
    """Research and the art lookup return a title that overwrites a hedged one
    outright. Reading the web is not reading the listing rules, so the pass
    that correctly replaces "style of" must not hand back a caption."""
    shared = _flat(listing_prompt.TITLE_BUDGET_AND_BANS)
    assert "70-80" in shared
    for banned in BANNED_IN_A_TITLE:
        assert banned.lower() in shared.lower(), (
            f"the shared rule no longer bans {banned!r}")
    assert "never hand back a title shorter than the draft's" in shared

    source = CLAUDE_AI.read_text()
    assert source.count("TITLE_BUDGET_AND_BANS") == 3, (
        "the research schema, the art schema, or the import stopped carrying "
        "the shared title rule")


def test_a_refine_cannot_hand_back_a_shorter_or_shoutier_title():
    """The editor's rewrite reaches the same buyers as the first draft, and
    "make it punchier" is exactly the instruction that would otherwise trade
    eighty earned characters for a slogan."""
    rule = _flat(listing_prompt.REFINE_ORDER_RULE)
    assert "SPEND the 80 characters" in rule
    assert "70-80" in rule
    for banned in ("ALL-CAPS", "emoji", "asterisks", "MUST SEE", "SKU"):
        assert banned in rule, f"a refine may now write {banned!r} into a title"

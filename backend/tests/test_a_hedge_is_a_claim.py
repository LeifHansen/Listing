"""What the identify prompt is not allowed to let the AI say.

A hand-signed Fanch Ledan lithograph was drafted as a "Fanch Ledan style
lithograph" at $85. Both halves of that are the same mistake: the model was
unsure, and hedging felt like the careful thing to do. It is not. "Style",
"after", "attributed to" and "manner of" tell an eBay buyer the item is NOT
the real thing — the hedge is itself a claim about this item, one the photos
did not support, and it costs the seller most of what the piece is worth.
A low price does the same damage faster, because an underpriced listing sells
within the hour and cannot be taken back.

So the prompt now says three things it did not: describe a mark you cannot
read instead of downgrading the item; know the difference between an original,
a hand-signed edition and a poster; and return no price at all rather than a
guessed-low one when the value turns on an attribution the photos can't settle.

These tests import services.listing_prompt and never services.claude_ai — the
Anthropic SDK is not installed in the job that runs them, and a test that
importorskips it is a test that never runs where it matters.
"""
from __future__ import annotations

from backend.services import listing_prompt

SCHEMA = listing_prompt.LISTING_SCHEMA.lower()

# The words that read as "this is not the real thing".
HEDGES = ("in the style of", "after", "attributed to", "manner of",
          "reproduction")


def _rule(starts_with: str) -> str:
    """One top-level rule of the schema, unwrapped onto a single line so an
    assertion is about the words and not about where they were wrapped."""
    text = listing_prompt.LISTING_SCHEMA
    start = text.index(starts_with)
    end = text.find("\n- ", start + len(starts_with))
    body = text[start:end if end != -1 else len(text)]
    return " ".join(body.split()).lower()


def test_the_prompt_names_the_hedges_it_forbids():
    rule = _rule("- A HEDGE IS A CLAIM")
    for hedge in HEDGES:
        assert f'"{hedge}"' in rule, f"the prompt no longer names {hedge!r}"


def test_the_ban_reaches_the_title_and_the_brand():
    """The description can carry "resembles" honestly; the title and brand
    cannot — they are what the buyer and eBay's search read as the item's
    identity."""
    rule = _rule("- A HEDGE IS A CLAIM")
    assert "never hedge in the title" in rule
    assert "brand" in rule


def test_an_unreadable_mark_is_described_not_downgraded():
    """The alternative to hedging has to be spelled out, or "be accurate" just
    reads as "be vague"."""
    rule = _rule("- A HEDGE IS A CLAIM")
    assert "not fully legible" in rule          # what to write instead
    assert "missing_info" in rule               # where the question goes
    assert "never downgrade" in rule


def test_the_prompt_separates_an_original_from_a_print_from_a_poster():
    rule = _rule("- SIGNED, NUMBERED AND ORIGINAL WORKS")
    for kind in ("original", "hand-signed limited edition", "open-edition",
                 "poster"):
        assert kind in rule, f"the prompt no longer distinguishes {kind!r}"
    # ...and says what in the photos tells them apart.
    for evidence in ("pencil", "edition fraction", "chop mark", "blind stamp",
                     "certificate of authenticity", "plate mark"):
        assert evidence in rule, f"the prompt no longer looks for {evidence!r}"


def test_a_signed_piece_leads_with_the_artist():
    rule = _rule("- SIGNED, NUMBERED AND ORIGINAL WORKS")
    assert "artist's name first" in rule
    assert "hand signed" in rule


def test_a_low_guess_is_named_as_the_expensive_answer():
    """The prompt has to say WHY, or "don't guess low" reads as a style note
    next to "don't guess high"."""
    rule = _rule("- PRICE — never guess LOW to be safe")
    assert "not the cautious answer" in rule
    assert "cannot get it back" in rule


def test_an_unconfirmable_attribution_returns_no_price_at_all():
    rule = _rule("- PRICE — never guess LOW to be safe")
    assert '"price": null' in rule
    assert '"confidence": "low"' in rule
    for turns_on in ("signature", "artist", "autograph", "maker's mark",
                     "edition"):
        assert turns_on in rule, f"the price rule no longer covers {turns_on!r}"


def test_null_is_explained_as_a_question_not_a_failure():
    """Left unexplained, "return null" is the instruction a model quietly
    ignores in favour of being helpful."""
    rule = _rule("- PRICE — never guess LOW to be safe")
    assert "not a failure" in rule
    assert "comparable listings" in rule        # what actually fills it in


def test_a_refine_cannot_reintroduce_the_hedge():
    """"Make the title shorter" is exactly the instruction that trades a
    signed artist's name for a safe-sounding one."""
    rule = listing_prompt.REFINE_ORDER_RULE.lower()
    assert "never introduce a hedge" in rule
    assert "never lower a price to be safe" in rule

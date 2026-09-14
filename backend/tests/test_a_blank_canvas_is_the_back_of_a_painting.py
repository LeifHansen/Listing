"""A blank canvas is never the item; it is a painting photographed from behind.

A seller uploaded a stretched canvas seen from the back -- pale fabric,
wooden stretcher bars around it, the fabric folded and stapled over the
edges, "Gronda" stamped along the bottom bar -- and the app drafted "Gronda
Blank Stretched Artist Canvas on Wood Frame Fabric Wrapped Edges" at $17.99,
with the canvas mill as the brand. Every word was read off the photo
correctly and the listing was still worthless: the photo was the back of a
painting and the picture was on the other side.

There is no honest version of that listing. Nobody sells one used blank
canvas -- they are a few dollars new, they come in shrink-wrapped multipacks,
and a second-hand one ships for more than it fetches -- while a painting
photographed from behind is an everyday thing, because the back is where the
artist wrote the title and the gallery stapled its label. The two look
identical, because the back of a painting IS unpainted fabric on bars, and
the mill's stamp is on the bar whether or not anyone ever painted the front.

So the rule does not ask whether the canvas is blank, which from behind it
always is. It asks WHICH SIDE the photo shows, and answers it from the
stretcher bars standing proud of a recessed field, the staples and folded
corners, the cross-brace, the hanging wire. Then it does what the art rule
already does for a margin hidden under a mat: read what the back does say,
claim nothing about a picture nobody has photographed, and ask for the front
by name. The mill -- Gronda, Fredrix, Winsor & Newton -- made the support,
not the work, and never becomes the brand.
"""
from __future__ import annotations

import pytest

from backend.services.listing_prompt import (
    ART_RULE,
    ART_TAG_SCAN_RULE,
    ART_TRANSCRIBE_LINES,
    BLANK_CANVAS_RULE,
    LISTING_SCHEMA,
)


def _flat(text: str) -> str:
    """The rule with its line wrapping removed, so a test pins the WORDS a
    pass reads and not the column the prompt happens to wrap at."""
    return " ".join(text.split())


# --- the claim itself -------------------------------------------------------

def test_a_blank_canvas_is_named_as_the_back_of_a_painting():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "A BLANK CANVAS IS THE BACK OF A PAINTING" in rule
    assert "YOU ARE LOOKING AT THE BACK OF A WORK OF ART" in rule
    # And the conclusion the draft must not reach.
    assert "is NEVER the item" in rule
    assert '"blank canvas", "unused canvas", "artist canvas"' in rule


def test_the_rule_says_why_a_blank_canvas_listing_is_never_right():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "nobody photographs one at a time to sell" in rule
    # The back is a normal thing to photograph, which is why this keeps
    # happening.
    assert "the back is where the artist wrote the title" in rule


def test_thinking_it_is_a_blank_canvas_is_itself_the_error_signal():
    """The lesson the seller asked for: the thought is the tell."""
    rule = _flat(BLANK_CANVAS_RULE)
    assert "If you find yourself about to draft a blank canvas" in rule
    assert "you have the piece BACK TO FRONT" in rule
    assert "treat it as art" in rule


# --- how the back is recognised ---------------------------------------------

def test_the_rule_lists_what_makes_a_photo_the_back():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "YOU ARE LOOKING AT THE BACK when you can see any of these" in rule
    for tell in ("STRETCHER BARS", "RECESSED", "STAPLED", "CROSS-BRACE",
                 "HANGING HARDWARE", "WRAPPED OVER"):
        assert tell in rule, tell
    # One is enough -- the photo rarely shows all of them.
    assert "and one is enough" in rule
    # And the contrast that settles it.
    assert "from the front a canvas is flush and carries a picture" in rule


# --- the mill is not the brand ----------------------------------------------

def test_the_canvas_mill_never_becomes_the_brand_or_the_artist():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "THE MILL IS NOT THE BRAND AND NOT THE ARTIST" in rule
    # The stamp in the photo that started this, and its peers.
    assert "Gronda" in rule
    assert "Fredrix" in rule
    assert "Winsor &" in rule
    assert "EVERY stretched canvas they sell, painted or not" in rule
    assert "Never lead a title with it, never put it in brand" in rule
    # It is still worth recording -- as the support, not the maker.
    assert "Record it as the support" in rule


# --- what the back is actually worth reading --------------------------------

def test_the_back_is_read_rather_than_dismissed():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "READ THE BACK, IT IS THE MOST INFORMATIVE SIDE" in rule
    for mark in ("inscription", "exhibition", "framer", "inventory or lot"):
        assert mark in rule, mark
    # Why it matters: the verso is where an unsigned front gets its name.
    assert "when the front is unsigned" in rule


# --- nothing is claimed about a picture nobody photographed -----------------

def test_nothing_about_the_picture_is_drafted_from_the_back():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "THE FRONT IS THE ITEM AND YOU HAVE NOT SEEN IT" in rule
    assert "Draft nothing about the picture" in rule
    # Not in either direction -- the art rule's own symmetry.
    assert 'not "original" and not "print"' in rule
    assert "keep confidence low" in rule


def test_the_front_is_asked_for_by_name():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "ask for it in missing_info by name" in rule
    assert "photograph the FRONT of the painting" in rule
    assert "photograph any writing or labels on the back" in rule
    # And each photo is labelled with the side it shows.
    assert "which side each photo shows" in rule


# --- the same error wearing a frame -----------------------------------------

def test_the_back_of_a_framed_piece_is_not_an_empty_frame():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "THE SAME ERROR WEARS A FRAME" in rule
    assert "NOT an empty picture frame" in rule
    assert "dust cover" in rule
    # An empty frame is a real thing -- photographed from the front.
    assert "photographed from the FRONT with nothing in it" in rule


# --- the one case where a blank canvas is the item --------------------------

def test_retail_stock_is_the_one_exception_and_it_announces_itself():
    rule = _flat(BLANK_CANVAS_RULE)
    assert "THE ONE REAL EXCEPTION" in rule
    assert "RETAIL STOCK" in rule
    for tell in ("shrink-wrap", "barcode or price sticker", "multipack"):
        assert tell in rule, tell
    # Even then it is a supply, not a painting's price.
    assert "low-value art supply" in rule
    assert "never at the price a painting would fetch" in rule


# --- the rule reaches every pass that could make this mistake ---------------

def test_the_rule_rides_with_the_identify_pass():
    assert BLANK_CANVAS_RULE in LISTING_SCHEMA


def test_the_tag_locator_boxes_the_marks_on_the_back():
    rule = _flat(ART_TAG_SCAN_RULE)
    assert "When a photo shows the BACK of a canvas or a frame" in rule
    assert "mill's stamp on a stretcher bar" in rule
    assert "an artist's inscription, a title, a date" in rule


def test_the_zoom_pass_writes_a_support_line_and_a_verso_line():
    lines = _flat(ART_TRANSCRIBE_LINES)
    assert "'SUPPORT:" in lines
    assert "it names who made the canvas, never who painted it" in lines
    assert "'VERSO:" in lines
    assert "which side of the piece this crop shows" in lines


def test_the_rule_reaches_the_zoom_specifics_and_lookup_passes():
    """The same text, so the pass that names the piece and the pass that
    fills its specifics cannot disagree.

    This used to grep claude_ai.py for the literal "+ ART_RULE +
    BLANK_CANVAS_RULE" expressions, because that was how a rule reached a
    pass and the source was the only thing readable without the SDK. The
    rules now reach a pass through experts.registry, which is ALSO readable
    without the SDK -- deliberately so -- and asking it what an item is read
    under asserts the thing the expression was only evidence of. A paraphrase
    still fails: the assertion is identity against the constant.
    """
    from backend.services.experts import registry
    from backend.services.experts.base import Stage

    # The two rules travel together, at every stage where either appears.
    for stage in (Stage.IDENTIFY, Stage.TRANSCRIBE_RULES, Stage.ASPECTS):
        text = registry.rules_for(stage)
        assert ART_RULE in text, f"the art rule stopped reaching {stage.value}"
        assert BLANK_CANVAS_RULE in text, \
            f"the blank-canvas rule stopped reaching {stage.value}"

    # ...and the art lookup, which appends them to its own schema.
    from backend.services.experts import art
    assert BLANK_CANVAS_RULE in art.rules(Stage.IDENTIFY)


def test_a_canvas_draft_reaches_the_art_lookup():
    """The gate that decides a draft is art had no word for this one: the bad
    title names no medium and no artist, so nothing matched and the lookup --
    the pass that would have caught it -- never ran."""
    # Inline and guarded: importing the app pulls in the vision client, and
    # the lint+unit job deliberately doesn't install it. The smoke job runs
    # the whole suite with the real requirements, and this runs there.
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.main import _ART_WORDS

    bad_title = ("Gronda Blank Stretched Artist Canvas on Wood Frame "
                 "Fabric Wrapped Edges").lower()
    assert any(w in bad_title for w in _ART_WORDS)
    for word in ("stretched canvas", "stretcher bar", "blank canvas",
                 "empty frame", "verso"):
        assert word in _ART_WORDS, word

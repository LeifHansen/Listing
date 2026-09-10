"""A print is drafted by its signature and its edition number, and never past them.

A seller who lists art watched the app write "Vintage Art Print" about a
hand-signed, numbered lithograph -- no artist, no "signed", no edition --
because the pencil signature in the bottom margin is thirty pixels tall in a
whole-frame photo, and nothing told any pass that the small grey scrawl under
the picture is the most valuable thing in it. The identify prompt mentioned
signatures in one sentence inside a rule about autographs and trading cards;
the tag locator had no box for one; the zoom pass had no line for one; the
specifics fill and the art lookup read a print under no art rule at all.

So art now has the rule denim got: where each mark is (the signature lower
right, the edition fraction lower left, the chop embossed in a corner, the
plate mark around an intaglio image, the labels on the back), what each
looks like, what each establishes, and how it is written into the title and
the specifics -- and it rides with the identify pass, the tag locator, the
zoom-and-transcribe pass, the specifics fill and the art lookup, as one
text. The lookup returns how the piece is signed and numbered, and the
server writes Signed, Signed By, Edition Type and Edition Size and puts
"Hand Signed" and "Numbered 84/250" on the title.

The other half of the rule is what it forbids. Every mark is a price claim a
buyer checks on arrival, so a mark that is not in the photos is never claimed
-- and never denied: "unsigned" about a margin under the mat is the same
false claim in the cheaper direction. And a signature is read letter by
letter, never completed into a name the letters do not spell.
"""
from __future__ import annotations

import pytest

from backend.services.listing_prompt import (
    ART_RULE,
    ART_TAG_SCAN_RULE,
    ART_TRANSCRIBE_LINES,
    LISTING_SCHEMA,
)


def _flat(text: str) -> str:
    """The rule with its line wrapping removed, so a test pins the WORDS a
    pass reads and not the column the prompt happens to wrap at."""
    return " ".join(text.split())


# --- the marks, where they are and what they establish ----------------------

def test_the_rule_says_where_the_signature_is_and_how_a_hand_one_looks():
    rule = _flat(ART_RULE)
    assert "THE SIGNATURE" in rule
    assert "LOWER RIGHT first" in rule
    # A hand signature sits on the paper; one in the plate is printed.
    assert "HAND signature sits ON the paper" in rule
    assert "graphite sheen" in rule
    assert "IN THE PLATE" in rule
    assert 'it is not "hand signed"' in rule


def test_a_signature_is_read_letter_by_letter_and_never_completed():
    rule = _flat(ART_RULE)
    assert "LETTER BY LETTER" in rule
    assert "Never write a name the letters do not support" in rule
    # ...and an unclear one is described, not dropped.
    assert "never resolve an unclear signature by dropping it" in rule
    assert "signature partly legible" in rule


def test_the_rule_says_where_the_edition_number_is_and_what_the_proofs_mean():
    rule = _flat(ART_RULE)
    assert "THE EDITION NUMBER" in rule
    assert "LOWER LEFT" in rule
    assert "84/250" in rule
    assert "Edition Size" in rule
    for proof in ("A/P", "E/A", "H/C", "P/P", "T/P", "B.A.T."):
        assert proof in rule, f"the rule no longer names {proof!r}"
    # A missing number is a missing photo, not an open edition.
    assert "not thereby an open edition" in rule


@pytest.mark.parametrize("mark, tell", [
    # The chop: embossed and colourless, in a corner of the margin.
    ("BLIND STAMP", "EMBOSSED"),
    # The plate mark: the dent an intaglio plate leaves in the paper.
    ("THE PLATE MARK", "INDENTATION"),
    # The printed credit line: a publisher and a year, an open edition.
    ("THE PRINTED LINES ALONG THE EDGE", "PUBLISHER"),
    # The back: labels, a certificate, a stamp.
    ("THE BACK AND THE FRAME", "certificate of authenticity"),
])
def test_each_mark_is_named_with_what_it_looks_like(mark, tell):
    rule = _flat(ART_RULE)
    assert mark in rule
    assert tell in rule


def test_the_surface_tells_an_original_from_a_print_from_a_reproduction():
    rule = _flat(ART_RULE)
    assert "THE SURFACE" in rule
    assert "brushstrokes with relief" in rule
    assert "ORIGINAL PAINTING" in rule
    assert "halftone DOTS" in rule
    assert "OFFSET reproduction" in rule
    assert "GICLEE" in rule
    assert "SERIGRAPH" in rule
    assert "CANVAS PRINT, not a painting" in rule
    # ...and a surface it cannot see is not "print" by default.
    assert 'never "print" by default' in rule


def test_well_known_workshop_chops_are_named():
    rule = _flat(ART_RULE)
    for chop in ("Tamarind", "Gemini G.E.L.", "Mourlot", "ULAE"):
        assert chop in rule


# --- what the rule forbids ----------------------------------------------------

def test_a_mark_that_is_not_in_the_photos_is_never_claimed_and_never_denied():
    rule = _flat(ART_RULE)
    assert ('Never write "hand signed", "numbered", "artist proof", "original" '
            "or a chop's name about a mark that is not in the photos") in rule
    assert ('Never write "unsigned", "open edition", "poster" or "reproduction" '
            "about a piece whose margin, back or surface you cannot see") in rule
    assert "the same false claim in the cheaper direction" in rule


def test_a_mark_out_of_frame_is_a_photo_to_ask_for():
    rule = _flat(ART_RULE)
    assert "photograph the lower margin close up" in rule
    assert "photograph the back of the frame" in rule
    assert "raking light" in rule


def test_the_publisher_is_never_the_brand():
    rule = _flat(ART_RULE)
    assert "The publisher is never the brand: the artist is" in rule


# --- how it is written into the listing --------------------------------------

def test_the_title_leads_with_the_artist_then_the_work_then_the_words_that_price_it():
    rule = _flat(ART_RULE)
    assert "lead with the ARTIST'S NAME" in rule
    assert '"Hand Signed", "Signed & Numbered 84/250", "Artist Proof", "Original"' in rule
    assert ("Salvador Dali Lincoln in Dalivision Lithograph Hand Signed Numbered "
            "84/250 Framed") in rule


def test_the_art_specifics_are_named_and_signed_means_a_hand_signature():
    rule = _flat(ART_RULE)
    for aspect in ("Signed By", "Edition Type", "Edition Size", "Print Type",
                   "Original/Licensed Reprint", "Year Produced"):
        assert aspect in rule, f"the rule no longer fills {aspect!r}"
    assert "Signed (Yes only for a hand signature you can see, never for one in the plate)" in rule
    assert "never Reproduction to be safe" in rule


def test_the_observations_carry_one_line_per_mark_including_the_unseen():
    rule = _flat(ART_RULE)
    assert "SIGNATURE, EDITION, TITLE ON SHEET, STAMP, PLATE MARK, CAPTION, LABEL, SURFACE" in rule
    assert "INCLUDING the ones you could not see" in rule


# --- every pass reads the same text ------------------------------------------

def test_the_identify_pass_reads_under_the_art_rule():
    assert ART_RULE in LISTING_SCHEMA
    # ...and the signed/numbered rule sends the reader to it.
    assert "the ART rule below says where each of these" in _flat(LISTING_SCHEMA)


def test_the_identify_pass_boxes_the_signature_the_edition_the_chop_and_the_labels():
    schema = _flat(LISTING_SCHEMA)
    assert "signature|edition|stamp|caption|label" in schema
    assert 'pencil signature in the bottom margin ("signature")' in schema
    assert 'fraction or A/P annotation ("edition")' in schema
    assert "box the whole bottom margin" in schema


def test_the_tag_locator_is_told_the_facts_are_in_pencil_and_on_the_back():
    rule = _flat(ART_TAG_SCAN_RULE)
    assert "MARGINS and on the BACK" in rule
    for kind in ('"signature"', '"edition"', '"stamp"', '"caption"', '"label"'):
        assert kind in rule
    assert "faint grey scrawl" in rule
    assert "box it anyway" in rule


def test_the_transcribe_pass_writes_one_line_per_mark():
    lines = _flat(ART_TRANSCRIBE_LINES)
    for line in ("'SIGNATURE:", "'EDITION:", "'TITLE ON SHEET:", "'STAMP:",
                 "'CAPTION:", "'LABEL:", "'PLATE MARK:", "'SURFACE:"):
        assert line in lines, f"the transcribe pass no longer writes {line}"
    assert "in the plate" in lines
    assert "could not see is a line too" in lines
    assert "never complete it into a name the letters do not spell" in lines


def test_every_vision_pass_reads_the_same_art_text():
    """The locator, the transcribe pass, the specifics fill and the art
    lookup read the constants, not a paraphrase. Pinned on the source so it
    holds without the SDK installed."""
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "services" / "claude_ai.py"
    text = source.read_text(encoding="utf-8")
    assert "DENIM_TAG_SCAN_RULE + ART_TAG_SCAN_RULE" in text
    assert "DENIM_TRANSCRIBE_LINES + ART_TRANSCRIBE_LINES" in text
    assert "STICKER_AND_BARCODE_RULE + VINTAGE_DENIM_RULE + ART_RULE" in text
    assert "_ASPECTS_FILL_SCHEMA + VINTAGE_DENIM_RULE + ART_RULE" in text
    # The art lookup asks how the piece is signed and numbered, and reads
    # under the same rule.
    assert '"signature":' in text and '"edition":' in text
    assert '""" + ART_RULE' in text
    assert "signature|edition|stamp|caption|label" in text

"""A pair of vintage Levi's is drafted by its markers, and never past them.

A seller who moves a lot of vintage Levi's watched the app write "Vintage
Levi's 501 jeans" about pairs that collectors would have paid several times
the price for -- because the details that set the price were in the photos
and the draft never mentioned them. The lettering on the red tab (a capital
E is pre-1971), the fabric edge along the outseam (redline selvedge is
before the mid-1980s), the wording on the patch (an XX is 1968 or earlier),
the row of numbers on the care tag (a lot-and-finish code, a production
date, a factory number), the stamp on the back of the top button, the
rivets inside the back pockets: every one of them is in frame in an ordinary
set of photos, every one is searched for by name, and the identify prompt
said nothing about any of them.

The sticker rule already said "read every tag". What it could not say is
what to do with a fabric edge, which is not a tag and is not text: a
selvedge outseam is a woven detail visible only where a hem is turned up,
and a model that has not been told to look there reports the hem as a hem.
So the denim rule names each place to look, what each looks like, and the
years each supports -- and it rides with the identify pass, the tag
locator, the zoom-and-transcribe pass and the specifics fill, as one text.

The other half of the rule is what it forbids. Every marker is a PRICE claim
a buyer checks on arrival, so a marker that is not in the photos is never
written; and the size on the patch is the tag size of a pair that has
shrunk, never the size it measures now.
"""
from __future__ import annotations

import pytest

from backend.services.listing_prompt import (
    DENIM_TAG_SCAN_RULE,
    DENIM_TRANSCRIBE_LINES,
    LISTING_SCHEMA,
    STICKER_AND_BARCODE_RULE,
    VINTAGE_DENIM_RULE,
)


def _flat(text: str) -> str:
    """The rule with its line wrapping removed, so a test pins the WORDS a
    pass reads and not the column the prompt happens to wrap at."""
    return " ".join(text.split())


# --- the markers, and the years each one supports ---------------------------

def test_the_rule_says_where_the_selvedge_edge_is_and_what_it_looks_like():
    """The one that is not a tag. It is only visible at a turned-up hem or a
    turned-out outseam, and a model not told that reports a hem as a hem."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "SELVEDGE" in rule
    assert "OUTSEAM" in rule
    assert "turned-up hem" in rule
    # What a selvedge edge is, against what a non-selvedge edge is.
    assert "self-finished edge" in rule
    assert "overlock" in rule
    # The Levi's-specific form, by the name buyers type.
    assert "REDLINE" in rule
    # And the era it puts a US-made pair in.
    assert "early-to-mid 1980s" in rule
    assert "before about 1986" in rule


def test_selvedge_is_not_read_as_levis_or_as_vintage_on_its_own():
    """Selvedge is a fabric, not a brand or a decade: LVC reproductions and
    Japanese makers use it today, and the brand still comes off the patch."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "NOT Levi's-only and NOT vintage-only" in rule
    assert "Levi's Vintage Clothing" in rule
    assert "name the brand from the patch and tab, never from the selvedge" in rule


@pytest.mark.parametrize("marker, year", [
    # The red tab: capitals until 1971, lowercase e since.
    ("BIG E", "1971"),
    # XX on the patch: the 1966-68 pairs or earlier.
    ("XX", "1966-68"),
    # Hidden rivets on the back pockets: 1937 until about 1966.
    ("HIDDEN RIVETS", "1937"),
    ("HIDDEN RIVETS", "1966"),
    # A single-needle arcuate: before about 1947.
    ("SINGLE-NEEDLE", "1947"),
    # The care tag itself: introduced about 1971-73, so none means earlier.
    ("care tag", "1971-73"),
    # US production ended 2002-2003.
    ("MADE IN U.S.A.", "2003"),
    # A leather patch: until the mid-1950s.
    ("LEATHER", "mid-1950s"),
])
def test_each_marker_carries_the_year_collectors_agree_on(marker, year):
    rule = _flat(VINTAGE_DENIM_RULE)
    assert marker in rule, marker
    assert year in rule, year


def test_the_care_tag_row_is_read_as_a_lot_code_a_finish_a_size_and_a_date():
    """The digits on the care tag are four different facts in one row, and
    the expensive mistake is reading one as another: the four digits after
    the dash are a finish, not a size and not a date."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "LOT-AND-FINISH code" in rule
    assert '"501-0115"' in rule
    assert "NOT a size and NOT a date" in rule
    # The finishes the rule names, as the codes on the tag.
    assert "0000 is rigid Shrink-to-Fit" in rule
    assert "0115" in rule
    assert "0660 black" in rule
    # Levi Strauss & Co.'s registered label number, on every genuine tag.
    assert "WPL 423" in rule
    # The production code, in both of its forms.
    assert "PRODUCTION CODE" in rule
    assert "four-digit MMYY code" in rule
    assert "0496 = April 1996" in rule
    # A one-digit year is a decade the other markers settle, never the tag.
    assert "March 1977 or March 1987" in rule
    assert "never by the tag alone" in rule


def test_the_button_stamp_is_a_factory_and_never_a_build_year_on_its_own():
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "BACK OF THE TOP BUTTON" in rule
    assert "FACTORY code" in rule
    assert "555 is the Valencia Street plant" in rule
    assert "524 El Paso" in rule
    assert "never as a build year on its own" in rule


# --- what the rule forbids ---------------------------------------------------

def test_a_marker_that_is_not_in_the_photos_is_never_written():
    """Every marker is a price claim a buyer checks on arrival. "Selvedge"
    on a pair whose hem was never turned up is a return, not a guess."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert 'Say "selvedge" ONLY when the edge is in frame' in rule
    assert "turn up the hem and photograph the outseam" in rule
    # The tab claim is now conditional on BOTH halves -- the letter you can
    # read, and an inside that does not say "reproduction" (see
    # test_the_red_tab_is_read_as_three_facts_not_one).
    assert ('write "Big E" in the title when you can read a capital E AND '
            "nothing inside the pair contradicts it") in rule
    assert "Never write it when you cannot read the letter." in rule
    assert ('Never write "Big E", "selvedge", "XX", "hidden rivets" or '
            '"single stitch" about a detail that is not in the photos') in rule
    # And a marker looked for and not found is reported as exactly that.
    assert "hem not turned up" in rule


def test_markers_that_disagree_are_a_question_and_not_a_date():
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "Date the pair to the era the markers support and no tighter" in rule
    assert "when markers disagree" in rule
    assert "reproduction or a swapped tab" in rule


def test_the_tag_size_is_never_sold_as_the_size_the_pair_measures():
    """Shrink-to-Fit denim has shrunk. The W/L on the patch is what it was
    sold as; what it measures now is a fact only a tape in the photo gives."""
    rule = _flat(VINTAGE_DENIM_RULE)
    assert "SIZING" in rule
    assert "Shrink-to-Fit and have shrunk" in rule
    assert 'Always give the tag size as "Tag size W32 L34"' in rule
    assert "ONLY from a tape measure in the photos" in rule
    assert "measured waist, inseam and rise" in rule


def test_the_title_leads_with_the_lot_and_then_the_markers_buyers_type():
    rule = _flat(VINTAGE_DENIM_RULE)
    assert '("Levi\'s 501XX")' in rule
    assert '"Big E", "Selvedge" or "Redline Selvedge"' in rule
    assert '"Hidden Rivets", "Single Stitch"' in rule


# --- where the rule reaches --------------------------------------------------

def test_the_identify_pass_reads_under_the_denim_rule():
    """The rule rides with the schema, after the sticker rule, so the first
    draft is written under it -- not only a later enrichment."""
    assert VINTAGE_DENIM_RULE in LISTING_SCHEMA
    assert LISTING_SCHEMA.index(STICKER_AND_BARCODE_RULE) \
        < LISTING_SCHEMA.index(VINTAGE_DENIM_RULE)
    # The rule is appended after the condition list is formatted in, so a
    # percent sign in it would be harmless -- but the schema must still have
    # been formatted, or every condition name is a literal "%s".
    assert "%s" not in LISTING_SCHEMA
    assert "USED_EXCELLENT" in LISTING_SCHEMA


def test_the_identify_pass_boxes_the_tab_the_patch_the_button_and_the_edge():
    """The identify pass locates the tags the zoom pass will crop. On jeans
    the facts are on hardware and a fabric edge, so each gets a kind of its
    own -- and the tags rule says the edge is only visible at the hem."""
    assert "sticker|price|patch|tab|selvedge|button|other" in LISTING_SCHEMA
    assert 'turned-up hem or turned-out outseam ("selvedge")' in LISTING_SCHEMA


def test_the_tag_locator_is_told_the_facts_are_on_hardware_and_an_edge():
    rule = _flat(DENIM_TAG_SCAN_RULE)
    for target in ("RED TAB", "PATCH", "INSIDE CARE TAG", "BACK of the top button",
                   "OUTSEAM EDGE", "coin pocket"):
        assert target in rule, target
    assert 'Box it as "selvedge"' in rule
    assert 'sliver of the photo' in rule


def test_the_transcribe_pass_writes_one_line_per_marker():
    """So the specifics fill can quote the tab, the lot and the button stamp
    as ground truth the way it quotes a barcode."""
    lines = _flat(DENIM_TRANSCRIBE_LINES)
    for label in ("RED TAB:", "PATCH:", "LOT:", "CARE TAG ROW:", "BUTTON BACK:",
                  "RIVETS:", "SELVEDGE:", "ARCUATE:"):
        assert label in lines, label
    # A marker looked for and not seen is a line, never a silence.
    assert "SELVEDGE: not visible, hem not turned up" in lines


def test_every_vision_pass_reads_the_same_denim_text():
    """The locator, the zoom pass and the specifics fill live beside the
    Anthropic client; they must carry the SAME constants, not a paraphrase
    that drifts. Skipped where the SDK is not installed (CI's light job)."""
    pytest.importorskip("anthropic")
    pytest.importorskip("fastapi")
    from backend.services import claude_ai

    assert DENIM_TAG_SCAN_RULE in claude_ai._TAG_SCAN_SCHEMA
    assert "patch|tab|selvedge|button" in claude_ai._TAG_SCAN_SCHEMA
    assert DENIM_TRANSCRIBE_LINES in claude_ai._TAG_TRANSCRIBE_ASK
    assert VINTAGE_DENIM_RULE in claude_ai._TAG_TRANSCRIBE_ASK
    assert VINTAGE_DENIM_RULE in claude_ai._ASPECTS_SYSTEM
    # The fill maps the markers onto eBay's aspect names, and the finish
    # codes it names agree with the rule's.
    fill = _flat(claude_ai._ASPECTS_FILL_SCHEMA)
    assert '"Selvedge Denim"' in fill
    assert 'Closure "Button"' in fill
    assert "0000 rigid, 0115 stonewash, 0660 black" in fill
    assert "Big E = before 1971" in fill

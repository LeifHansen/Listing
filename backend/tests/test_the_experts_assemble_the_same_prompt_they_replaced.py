"""The verticals became experts, and not one word of a prompt changed.

The denim rule and the art rule used to reach a pass by being named in a `+`
expression in services/claude_ai -- eight of them, across six stages. That is
how a rule reached a prompt, and it meant EVERY item paid for EVERY vertical: a
coffee mug was drafted under the Levi's selvedge rule and the plate-mark rule,
because the prompt that drafted it was one string with all of them in it. At
two verticals that is affordable. At eight it is 30-40k tokens of competing
domain experts on every item, and the rules that already work get worse as new
ones land beside them.

So a vertical is now an expert, and services/experts/registry decides which
ones speak. That is a refactor of the thing that decides what every listing in
the app says, which is about as load-bearing as this codebase gets -- so it
ships inert. EXPERT_ROUTING defaults to off, off means every enabled expert
speaks in the order it always did, and this file is the proof: the assembled
text is the concatenation it replaced, rule for rule and stage for stage.

Turning routing ON is a separate commit and a separate decision. What this
file pins is that landing the machinery did not, by itself, change a single
prompt -- so if a draft gets worse next week, it was not this.

Read without the SDK on purpose: services/experts is import-free for the same
reason services/listing_prompt is, and a test that can only run in the heavy
job is a test that stops running the day the heavy job gets slow.
"""
from __future__ import annotations

import pytest

from backend.services.experts import registry
from backend.services.experts.art.rules import (
    ART_FRONT_AND_BACK_RULE,
    ART_PRESENTATION_RULE,
)
from backend.services.experts.base import Stage
from backend.services.listing_prompt import (
    ART_RULE,
    ART_TAG_SCAN_RULE,
    ART_TRANSCRIBE_LINES,
    BLANK_CANVAS_RULE,
    BLANK_CANVAS_RULE as _BLANK,
    DENIM_FRONT_AND_BACK_RULE,
    DENIM_TAG_SCAN_RULE,
    DENIM_TRANSCRIBE_LINES,
    LISTING_SCHEMA,
    RETAIL_TAG_RULE,
    STICKER_AND_BARCODE_RULE,
    VINTAGE_DENIM_RULE,
    listing_schema,
)


@pytest.fixture(autouse=True)
def _routing_off(monkeypatch):
    """Every assertion below is about the DEFAULT. Pinned rather than assumed,
    so that flipping the default later fails here -- loudly, in the file whose
    whole subject is that nothing changed -- instead of silently somewhere
    else."""
    monkeypatch.delenv("EXPERT_ROUTING", raising=False)
    monkeypatch.delenv("EXPERT_ART", raising=False)
    monkeypatch.delenv("EXPERT_DENIM", raising=False)
    monkeypatch.delenv("ART_LOOKUP", raising=False)


def test_routing_is_off_until_somebody_turns_it_on():
    assert registry.routing_enabled() is False


# --- the eight concatenations, one assertion each ---------------------------

# What the experts have ADDED since the migration, per stage. Every entry is a
# deliberate change with its own test file; anything arriving that is not on
# this list fails the assertions below, which is the point -- a rule must not
# be able to appear in the prompt that writes every listing without somebody
# writing it down here first.
ADDED = {
    Stage.IDENTIFY: (ART_PRESENTATION_RULE,),
    Stage.ASPECTS: (ART_PRESENTATION_RULE,),
    Stage.GROUPING: (ART_FRONT_AND_BACK_RULE,),
}


def _without_additions(text: str, stage: Stage) -> str:
    """`text` with this stage's deliberate additions removed, so what is left
    can be compared against what the concatenations produced."""
    for rule in ADDED.get(stage, ()):
        text = text.replace(rule, "")
    return text


def test_the_identify_schema_is_the_string_it_always_was():
    """The big one: the 52,193 characters that decide what every listing says.

    Compared with the deliberate additions taken back out, because the promise
    this file makes is about the REFACTOR -- that moving the rules into experts
    moved not one word -- and not that art may never learn anything new. What
    it has learned since is listed in ADDED above, each with its own test.
    """
    # The live constant is itself built by listing_schema(), so both carry
    # whatever has been added since; the comparison is against the shape the
    # concatenation had, with the additions taken back out of both.
    schema = _without_additions(LISTING_SCHEMA, Stage.IDENTIFY)
    assert schema == _without_additions(listing_schema(), Stage.IDENTIFY)
    assert schema == (
        # head, then the verticals, then the universal hang-tag rule
        schema[:schema.index(VINTAGE_DENIM_RULE)]
        + VINTAGE_DENIM_RULE + ART_RULE + BLANK_CANVAS_RULE + RETAIL_TAG_RULE)
    # ...and the head is still everything the schema had before the verticals,
    # including the universal sticker rule. 52,193 characters of it originally.
    assert len(schema) > 45000
    assert STICKER_AND_BARCODE_RULE in schema[:schema.index(VINTAGE_DENIM_RULE)]


def test_the_identify_stage_carries_denim_then_art_then_blank_canvas():
    assert _without_additions(registry.rules_for(Stage.IDENTIFY),
                              Stage.IDENTIFY) == (
        VINTAGE_DENIM_RULE + ART_RULE + BLANK_CANVAS_RULE)


def test_the_box_locator_carries_denim_then_art():
    assert registry.rules_for(Stage.TAG_SCAN) == (
        DENIM_TAG_SCAN_RULE + ART_TAG_SCAN_RULE)


def test_the_zoom_pass_carries_denim_then_art():
    assert registry.rules_for(Stage.TRANSCRIBE_LINES) == (
        DENIM_TRANSCRIBE_LINES + ART_TRANSCRIBE_LINES)
    assert _without_additions(registry.rules_for(Stage.TRANSCRIBE_RULES),
                              Stage.TRANSCRIBE_RULES) == (
        VINTAGE_DENIM_RULE + ART_RULE + BLANK_CANVAS_RULE)


def test_the_specifics_fill_carries_denim_then_art():
    assert _without_additions(registry.rules_for(Stage.ASPECTS),
                              Stage.ASPECTS) == (
        VINTAGE_DENIM_RULE + ART_RULE + BLANK_CANVAS_RULE)


def test_grouping_carries_every_experts_front_and_back_rule():
    """The one stage whose text is deliberately NOT what it was.

    Grouping used to carry denim's front-and-back rule alone, because denim
    was the only vertical that had one. Art has one now -- see
    test_the_back_of_a_painting_is_not_a_second_listing.py for the failure it
    is about -- and grouping is unroutable, so both ride every grouping call.

    That is an addition rather than a change: denim's rule is still there,
    still first, still byte-identical. Pinned here because the rest of this
    file is the promise that nothing moved, and the one thing that did move
    should be stated where a reader is looking for exactly that.
    """
    text = registry.rules_for(Stage.GROUPING)
    assert _without_additions(text, Stage.GROUPING) == DENIM_FRONT_AND_BACK_RULE
    assert DENIM_FRONT_AND_BACK_RULE in text
    assert ART_FRONT_AND_BACK_RULE in text


# --- the properties the order carries --------------------------------------

def test_the_sticker_rule_still_comes_before_the_verticals():
    """Pinned since before experts: the barcode rule teaches the model to read
    a label at all, and the vertical rules tell it which labels matter. The
    other order teaches the specific before the general."""
    schema = LISTING_SCHEMA
    assert schema.index(STICKER_AND_BARCODE_RULE) < schema.index(VINTAGE_DENIM_RULE)
    assert schema.index(VINTAGE_DENIM_RULE) < schema.index(ART_RULE)


def test_every_rule_appears_exactly_once():
    """A rule spliced in twice is a rule arguing with itself, and it is the
    easy mistake to make when the splicing moves from a `+` you can see to a
    registry you cannot."""
    schema = listing_schema()
    for rule in (STICKER_AND_BARCODE_RULE, VINTAGE_DENIM_RULE, ART_RULE,
                 _BLANK, RETAIL_TAG_RULE):
        assert schema.count(rule) == 1


def test_art_never_arrives_without_the_blank_canvas_rule():
    """The second is the case the first gets wrong on its own: the back of a
    painting IS a blank canvas, and reading it as one writes a $17 listing for
    a painting. They travelled together in every `+` expression; they travel
    together now because the art expert binds them."""
    for stage in (Stage.IDENTIFY, Stage.TRANSCRIBE_RULES, Stage.ASPECTS):
        text = registry.rules_for(stage)
        assert (ART_RULE in text) == (BLANK_CANVAS_RULE in text)


def test_the_experts_arrive_in_a_declared_order_not_an_accidental_one():
    """Two items matching the same experts must produce the same prompt, or
    they cannot share a prompt cache entry. The order is canonical -- declared
    in registry.EXPERTS -- rather than whatever the scores happened to be."""
    assert [e.name for e in registry.EXPERTS] == ["denim", "art"]


def test_nothing_has_been_added_that_is_not_written_down():
    """The list above is only worth having if an unlisted rule fails.

    A vertical's rule text reaches every listing this app writes. Adding one
    is a real decision and it should be impossible to make by accident -- so
    a stage whose text, with its declared additions removed, is not what the
    concatenation produced, fails here rather than shipping.
    """
    expected = {
        Stage.IDENTIFY: VINTAGE_DENIM_RULE + ART_RULE + BLANK_CANVAS_RULE,
        Stage.TAG_SCAN: DENIM_TAG_SCAN_RULE + ART_TAG_SCAN_RULE,
        Stage.TRANSCRIBE_LINES: DENIM_TRANSCRIBE_LINES + ART_TRANSCRIBE_LINES,
        Stage.TRANSCRIBE_RULES: VINTAGE_DENIM_RULE + ART_RULE + BLANK_CANVAS_RULE,
        Stage.ASPECTS: VINTAGE_DENIM_RULE + ART_RULE + BLANK_CANVAS_RULE,
        Stage.GROUPING: DENIM_FRONT_AND_BACK_RULE,
    }
    for stage, original in expected.items():
        assert _without_additions(registry.rules_for(stage), stage) == original, (
            f"{stage.value} carries rule text that is neither what it carried "
            f"before experts nor listed in ADDED. If it is deliberate, add it "
            f"to ADDED with a test; if it is not, this is the bug.")

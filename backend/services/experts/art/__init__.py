"""The art expert: a picture, and the artist behind it.

What this expert knows is spread over the modules beside it -- `rules` holds
the prompt text, `match` decides whether an item is art at all. This file is
the manifest: the name, the score, and which text goes at which stage.

The stage mapping is not a design decision made here; it is a transcription of
what services/claude_ai already concatenated, so that turning experts on
changes nothing. See experts/registry for the test that proves that.
"""
from __future__ import annotations

from ..base import Stage
from . import match as _match
from .rules import (
    ART_RULE,
    ART_TAG_SCAN_RULE,
    ART_TRANSCRIBE_LINES,
    BLANK_CANVAS_RULE,
)

name = "art"

# ART_RULE and BLANK_CANVAS_RULE travel together everywhere they travel at all:
# the second is the case the first gets wrong on its own (the back of a
# painting IS a blank canvas, and reading it as one writes a $17 listing for a
# painting). Bound here so no stage can pick up one without the other.
_FULL = ART_RULE + BLANK_CANVAS_RULE

_BY_STAGE = {
    Stage.IDENTIFY: _FULL,
    Stage.TAG_SCAN: ART_TAG_SCAN_RULE,
    Stage.TRANSCRIBE_LINES: ART_TRANSCRIBE_LINES,
    Stage.TRANSCRIBE_RULES: _FULL,
    Stage.ASPECTS: _FULL,
    # Art says nothing at GROUPING yet. It should -- the back of a painting is
    # not a second item, exactly as the back of a pair of jeans is not -- and
    # that rule is the verso work, not this migration. BLANK_CANVAS_RULE
    # currently carries the case at IDENTIFY instead, which catches it one pass
    # later than grouping does.
}


def matches(listing, observations: str = "") -> float:
    return _match.matches(listing, observations)


def rules(stage: Stage) -> str:
    return _BY_STAGE.get(stage, "")

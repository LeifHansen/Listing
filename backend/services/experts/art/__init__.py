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
    ART_FRONT_AND_BACK_RULE,
    ART_PRESENTATION_RULE,
    ART_RULE,
    ART_TAG_SCAN_RULE,
    ART_TRANSCRIBE_LINES,
    BLANK_CANVAS_RULE,
)

name = "art"

# The three rules that travel together everywhere they travel at all.
#
# BLANK_CANVAS_RULE is the case ART_RULE gets wrong on its own (the back of a
# painting IS a blank canvas, and reading it as one writes a $17 listing for a
# painting). ART_PRESENTATION_RULE is the case both get wrong: a mat COVERS
# the lower margin, so the signature and the edition number ART_RULE is all
# about are hidden rather than absent -- and ART_RULE's own prohibition on
# claiming what is not in the photos only has teeth if something tells the
# pass that a mat is why it cannot see them.
#
# Original note, still true of the first two:
# the second is the case the first gets wrong on its own (the back of a
# painting IS a blank canvas, and reading it as one writes a $17 listing for a
# painting). Bound here so no stage can pick up one without the other.
_FULL = ART_RULE + BLANK_CANVAS_RULE
# ...plus how the piece is presented, at the two stages that decide or
# write it: the draft, and the eBay specifics fill that answers Framing
# and Frame Material. The zoom pass reads CROPS of marks -- a signature,
# an edition fraction -- and cannot see a frame at all, so it is left out
# of that one rather than carried for the sake of symmetry.
_DRAFTING = _FULL + ART_PRESENTATION_RULE

_BY_STAGE = {
    Stage.IDENTIFY: _DRAFTING,
    Stage.TAG_SCAN: ART_TAG_SCAN_RULE,
    Stage.TRANSCRIBE_LINES: ART_TRANSCRIBE_LINES,
    Stage.TRANSCRIBE_RULES: _FULL,
    Stage.ASPECTS: _DRAFTING,
    # Art changes the COUNT of items in a pile, twice over: the back of a
    # picture looks nothing like its front, and one picture's back carries
    # half a dozen labels that all read differently. Both of those are the
    # general grouping rule's blind spot, so this rides every grouping call --
    # GROUPING is the one stage that cannot be routed, because working out
    # which photos are one item is what produces the draft a router would
    # read. See experts/base.Stage.
    Stage.GROUPING: ART_FRONT_AND_BACK_RULE,
}


def matches(listing, observations: str = "") -> float:
    return _match.matches(listing, observations)


def rules(stage: Stage) -> str:
    return _BY_STAGE.get(stage, "")

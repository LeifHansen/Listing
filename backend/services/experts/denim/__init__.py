"""The denim expert: the selvedge edge, the red tab, the patch, the lot code.

The manifest for the denim vertical -- see experts/art/__init__ for what a
manifest is and why the stage mapping is a transcription rather than a design.
"""
from __future__ import annotations

from ..base import Stage
from . import match as _match
from .rules import (
    DENIM_FRONT_AND_BACK_RULE,
    DENIM_TAG_SCAN_RULE,
    DENIM_TRANSCRIBE_LINES,
    VINTAGE_DENIM_RULE,
)

name = "denim"

_BY_STAGE = {
    Stage.IDENTIFY: VINTAGE_DENIM_RULE,
    Stage.TAG_SCAN: DENIM_TAG_SCAN_RULE,
    Stage.TRANSCRIBE_LINES: DENIM_TRANSCRIBE_LINES,
    Stage.TRANSCRIBE_RULES: VINTAGE_DENIM_RULE,
    Stage.ASPECTS: VINTAGE_DENIM_RULE,
    # Denim changes the COUNT of items in a pile -- a pair is shot front and
    # back, and a back view is never an item of its own -- so it is one of the
    # verticals with something to say before any draft exists.
    Stage.GROUPING: DENIM_FRONT_AND_BACK_RULE,
}


def matches(listing, observations: str = "") -> float:
    return _match.matches(listing, observations)


def rules(stage: Stage) -> str:
    return _BY_STAGE.get(stage, "")

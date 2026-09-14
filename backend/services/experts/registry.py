"""Which experts exist, and what text an item is drafted under.

This is the whole of the wiring. Before experts, a rule reached a prompt by
being named in a `+` expression in services/claude_ai -- five of them -- so
adding a vertical meant editing five prompts, and every item paid for every
vertical. Now a rule reaches a prompt because its expert is in EXPERTS below
and scored high enough to be asked.

THE ORDER OF `EXPERTS` IS LOAD-BEARING, twice. It is the order the rules are
concatenated in, which is the order they were concatenated in before this
module existed -- so `rules_for` with routing off reproduces the old strings
byte for byte, which is what makes the migration provable rather than merely
plausible (backend/tests/test_the_experts_assemble_the_same_prompt_they_
replaced.py). And it is a CANONICAL order rather than a score order, so that
two items matching the same experts produce the same prompt, and therefore hit
the same prompt cache entry, however differently they scored.

Nothing here imports anything outside the standard library and its own
package: routing and rule assembly must stay assertable in CI's light job.
"""
from __future__ import annotations

import os
from typing import Optional

from . import art, denim
from .base import NO_MATCH, WEAK, Stage, env_mode, rules_of, score_of

# Canonical order. Read the module docstring before changing it.
EXPERTS = (denim, art)

# How many experts may speak about one item. Two, because an item really can be
# two verticals at once -- a framed print of a denim advertisement -- and
# because the whole point of routing is that it is not "all of them". A third
# has never been needed and would put us back where we started.
MAX_ACTIVE = int(os.getenv("EXPERT_MAX_ACTIVE", "2") or 2)

# The floor a score must clear to put an expert's rules in the prompt.
#
# WEAK by default, which is generous on purpose. The two directions are not
# symmetric: routing an expert IN that was not needed costs some tokens and a
# little attention, and routing one OUT that WAS needed drafts a hand-signed
# lithograph with no art rule -- which is the exact failure ART_RULE was
# written for. So the floor sits low and MAX_ACTIVE does the narrowing.
_FLOORS = {"none": NO_MATCH, "weak": WEAK, "likely": 0.6, "certain": 1.0}
MATCH_FLOOR = _FLOORS.get(
    (os.getenv("EXPERT_MATCH_FLOOR", "weak") or "weak").strip().lower(), WEAK)


def routing_enabled() -> bool:
    """Whether an item's rules are chosen for it.

    OFF means every enabled expert speaks at every stage -- exactly what the
    concatenated constants did. That is the DEFAULT for now, so that landing
    the experts changes nothing observable; turning routing on is its own
    commit, and its own thing to revert.
    """
    return (os.getenv("EXPERT_ROUTING", "off") or "off").strip().lower() == "on"


def _mode(expert) -> str:
    """An expert's kill switch: EXPERT_ART, EXPERT_DENIM, ... on|off|auto.

    ART_LOOKUP is honoured as an alias for the art expert because it is the
    flag that already exists, is documented in .env.example, and may well be
    set to `off` in somebody's environment right now. An explicit EXPERT_ART
    wins over it.
    """
    name = getattr(expert, "name", "")
    mode = env_mode(f"EXPERT_{name.upper()}", os.getenv, "auto")
    if name == "art" and mode == "auto" and \
            (os.getenv("ART_LOOKUP", "") or "").strip().lower() == "off":
        return "off"
    return mode


def active(listing=None, observations: str = "",
           stage: Optional[Stage] = None) -> tuple:
    """The experts that speak about this item, in canonical order.

    Everything enabled, when routing is off, when there is no draft to score
    against yet, or when the stage is GROUPING -- which cannot be routed,
    because working out which photos are one item is what produces the title
    and the category that scoring would read. See base.Stage.
    """
    enabled = [e for e in EXPERTS if _mode(e) != "off"]
    if stage is Stage.GROUPING or not routing_enabled() or listing is None:
        return tuple(enabled)

    scored = []
    for expert in enabled:
        if _mode(expert) == "on":
            scored.append((1.0, expert))
            continue
        score = score_of(expert, listing, observations)
        if score >= MATCH_FLOOR and score > NO_MATCH:
            scored.append((score, expert))
    # Take the best MAX_ACTIVE by score, then put them BACK into canonical
    # order: which experts speak is the item's business, the order they speak
    # in is the prompt's, and a stable order is what lets two like items share
    # a cache entry.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    chosen = {id(e) for _, e in scored[:MAX_ACTIVE]}
    return tuple(e for e in enabled if id(e) in chosen)


def rules_for(stage: Stage, listing=None, observations: str = "") -> str:
    """The expert rule text for `stage`, for this item, ready to concatenate.

    Returns "" when no expert speaks -- which for a coffee mug at IDENTIFY is
    the entire point, and is why this returns a string to append rather than a
    list to format: every call site already had `+ SOME_RULE + SOME_OTHER` and
    now has `+ rules_for(...)`, with "" doing what the absence of a rule ought
    always to have done.
    """
    return "".join(rules_of(e, stage) for e in active(listing, observations, stage))


def names(listing=None, observations: str = "",
          stage: Optional[Stage] = None) -> list:
    """The names of the experts that would speak -- for logging and for the
    draft's own record of why it was read the way it was."""
    return [getattr(e, "name", "?") for e in active(listing, observations, stage)]

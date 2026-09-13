"""What an expert is, and the stages it may speak at.

The app's domain knowledge — the denim rule, the art rule — used to be string
constants concatenated into five places in services/claude_ai. That worked for
two verticals and does not work for eight, for a reason that is arithmetic
rather than aesthetic: EVERY item paid for EVERY rule. A coffee mug was drafted
under the Levi's selvedge rule and the plate-mark rule, because the prompt that
drafted it was one string with all of them in it.

    LISTING_SCHEMA                            13,048 tokens
      of which VINTAGE_DENIM_RULE              2,640
               ART_RULE                        2,394
               RETAIL_TAG_RULE                 1,359
               BLANK_CANVAS_RULE               1,013
               STICKER_AND_BARCODE_RULE        1,002

Prompt caching hides the COST of that. It does nothing about the other half,
which is that eight domain experts shouting at once about a mug is worse for
the mug AND worse for the eight — a rule competes for attention with every
other rule in the prompt, so adding a vertical quietly degrades the ones that
already work. At eight verticals that prompt is 30-40k tokens and the problem
is structural.

So a vertical becomes an EXPERT: a name, a way of saying whether it is relevant
to this item, the rule text it contributes AT EACH STAGE, and optionally its
own enrichment pass and merge rules. A router picks the ones that apply, and an
item is drafted under those rules and no others.

NOTHING IN THIS MODULE MAY IMPORT ANYTHING BUT THE STANDARD LIBRARY, and the
same goes for every expert's rules.py and match.py. services/listing_prompt
established that constraint and CI enforces it: the light job installs no heavy
stack, so a test that imports services.claude_ai skips with it, and a prompt
rule nothing can assert on is a rule that quietly rots. Routing and rule
assembly are exactly the kind of thing that must stay assertable, so the heavy
half of an expert (its lookup pass, its data sources) is resolved LAZILY, by
name, at call time -- never imported here.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


class Stage(Enum):
    """Where in a prompt an expert's text is spliced.

    These are the five places the rules were concatenated into before there
    were experts, kept one-for-one so the migration can be proved inert:

      IDENTIFY         the rule block inside LISTING_SCHEMA, which the main
                       draft is written under
      TAG_SCAN         what the box LOCATOR is told -- it draws boxes and does
                       not read, so it needs to know that a pencil signature
                       is a faint scrawl worth a box anyway
      TRANSCRIBE_LINES the per-mark lines the zoom pass writes, one per thing
                       it looked at
      TRANSCRIBE_RULES the full rules those crops are read under
      ASPECTS          the eBay item-specifics fill
      GROUPING         which photos are which ITEM -- the one stage that
                       cannot be routed; see below

    IDENTIFY and TRANSCRIBE_RULES take the same text from an expert. They are
    separate stages because the UNIVERSAL rules around them differ, and that
    belongs to the call site rather than to the expert.

    GROUPING IS NOT ROUTABLE, and that is a fact about the pipeline rather
    than a decision taken here. Grouping runs on a pile of photos BEFORE any
    draft exists: there is no title, no category and no observations to score
    against, because working out which photos are one item is what produces
    them. So every expert's grouping rule ships on every grouping call, and an
    expert should put text at this stage only when the shape of its items
    genuinely changes the count -- denim's does (a pair is shot front and back,
    and a back view is never an item of its own), and art's does for the same
    reason (the back of a painting looks exactly like a blank canvas). A
    vertical with nothing to say about counting says nothing here.
    """

    IDENTIFY = "identify"
    TAG_SCAN = "tag_scan"
    TRANSCRIBE_LINES = "transcribe_lines"
    TRANSCRIBE_RULES = "transcribe_rules"
    ASPECTS = "aspects"
    GROUPING = "grouping"


# What `matches` returns. Not a bool, because two experts can both be a little
# bit right about one item -- a framed print of a denim advertisement is a real
# thing -- and the router has to be able to take the best two rather than
# everything above a line.
#
#   0.0        this expert has nothing to say about this item
#   0.1-0.4    a word matched, but a weak one
#   0.5-0.8    the item reads as this vertical
#   0.9-1.0    the CATEGORY says so, or an unambiguous marker was read
NO_MATCH = 0.0
WEAK = 0.3
LIKELY = 0.6
CERTAIN = 1.0


@runtime_checkable
class Expert(Protocol):
    """One vertical's knowledge, as a plug-in.

    An expert is a module (not a class instance) exposing this shape, so that
    the pure half -- name, matches, rules -- can be imported with no heavy
    dependency, and the rest is fetched by name when it is actually needed.
    """

    #: The expert's name, lowercase, stable. It is the env flag (EXPERT_ART),
    #: the log tag, and the key a reference link is filed under, so it is not
    #: something to rename casually.
    name: str

    def matches(self, listing, observations: str = "") -> float:
        """How strongly this expert applies to this item, NO_MATCH..CERTAIN.

        Reads the drafted title, the category, the item specifics and the
        first pass's raw observations. Must not raise and must not call
        anything remote -- it runs for every expert on every item.
        """

    def rules(self, stage: Stage) -> str:
        """This expert's text for `stage`, or "" when it has nothing to add
        there. Pure: the same stage returns the same string every time."""


def has_lookup(expert) -> bool:
    """Whether `expert` carries its own enrichment pass.

    Looked up by attribute rather than declared, because the lookup lives in a
    module that imports the Anthropic SDK and this file may not import it.
    """
    return callable(getattr(expert, "lookup", None))


def rules_of(expert, stage: Stage) -> str:
    """`expert`'s text for `stage`, tolerantly.

    An expert that has no opinion at a stage may say so by returning "", by
    not implementing `rules` at all, or by raising -- and none of those should
    be able to take a draft down. A broken expert contributes nothing and the
    item is still drafted.
    """
    fn = getattr(expert, "rules", None)
    if not callable(fn):
        return ""
    try:
        return fn(stage) or ""
    except Exception:  # noqa: BLE001 - a rule is worth less than a draft
        return ""


def score_of(expert, listing, observations: str = "") -> float:
    """`expert`'s match score, tolerantly and clamped to 0..1.

    Same contract as rules_of: an expert that throws while scoring scores
    zero rather than failing the item.
    """
    fn = getattr(expert, "matches", None)
    if not callable(fn):
        return NO_MATCH
    try:
        return max(0.0, min(1.0, float(fn(listing, observations))))
    except Exception:  # noqa: BLE001 - a score is worth less than a draft
        return NO_MATCH


def env_mode(name: str, getenv, default: str = "auto") -> str:
    """An expert's kill switch, read from the environment as on|off|auto.

    `getenv` is passed in rather than imported so this module stays free of
    even os -- the caller (registry) owns the environment.

      off    the expert never fires, whatever it scores
      on     the expert always fires, whatever it scores
      auto   the router decides (the default)
    """
    value = (getenv(name, default) or default).strip().lower()
    return value if value in ("on", "off", "auto") else default


class Subject:
    """What is known about an item at the moment an expert is asked about it.

    Routing needs a draft to score, and THE EXPENSIVE PASSES RUN BEFORE THERE
    IS ONE. identify() takes photos and produces the listing; group_photos()
    and read_tag_text() take photos and nothing else. Asked for a Listing,
    routing could only ever have worked at the item-specifics fill -- the
    cheapest stage and the last one -- which is most of the benefit thrown
    away.

    But the pipeline already knows things about a pile of photos before it
    drafts them, and is already paying for them:

      * services/orient.screen() returns `art` -- the photos whose ITEM IS A
        PICTURE. It is asked so the cutout can route a painting away from the
        salient-object model, it runs before the cutout, and it is the single
        best art signal in the app. Free, and earlier than identify.
      * group_photos() returns a NAME per group ("two lacoste polos", "framed
        print") -- the seller's pile, described, before any draft exists.
      * the seller's own notes ride the upload.

    None of those is a Listing and all of them are enough to route on. So an
    expert is asked about a Subject, which a real draft and a handful of hints
    can both become, and which quacks enough like a Listing that a scorer can
    read either without knowing which it got.
    """

    __slots__ = ("title", "category_suggestion", "item_specifics",
                 "observations", "flags")

    def __init__(self, title: str = "", category_suggestion: str = "",
                 item_specifics=(), observations: str = "", flags=()) -> None:
        self.title = title or ""
        self.category_suggestion = category_suggestion or ""
        self.item_specifics = list(item_specifics or ())
        self.observations = observations or ""
        self.flags = frozenset(flags or ())

    @classmethod
    def of(cls, listing, observations: str = "") -> "Subject":
        """A Subject reading a drafted Listing -- the late stages."""
        if listing is None:
            return cls(observations=observations)
        if isinstance(listing, cls):
            return listing
        return cls(
            title=getattr(listing, "title", "") or "",
            category_suggestion=getattr(listing, "category_suggestion", "") or "",
            item_specifics=getattr(listing, "item_specifics", None) or (),
            observations=observations,
        )

    @classmethod
    def hint(cls, text: str = "", flags=(), observations: str = "") -> "Subject":
        """A Subject built from what is known BEFORE a draft: a group's name,
        the seller's notes, and flags another pass already established.

        `flags` is how a fact that is not a word gets in. orient's art answer
        is the one that matters today: Subject.hint(flags={"art"}) says "a pass
        that looked at this photo says the item is a picture", which no amount
        of reading a title can tell you about a photo with no title yet.
        """
        return cls(title=text, observations=observations, flags=flags)

    def text(self) -> str:
        """Everything readable about this item, lowercased, for word matching."""
        return " ".join([
            self.title, self.observations,
            " ".join(f"{getattr(s, 'name', '')} {getattr(s, 'value', '')}"
                     for s in self.item_specifics),
        ]).lower()


class Reference:
    """One owner-added reference link, after it has been fetched and distilled.

    The distinction this class exists to carry, and the reason it is not a
    plain dict: `note` is the OWNER'S words and is an instruction, while
    `distillate` came off somebody else's web page and is EVIDENCE. They are
    never concatenated into one string, and the distillate never reaches a
    system prompt. See experts/knowledge.py for the fencing this is used
    under; the rule is the one services/claude_ai._lead_text already applies
    to reverse-image leads.
    """

    __slots__ = ("expert", "url", "note", "distillate", "scope")

    def __init__(self, expert: str, url: str, note: str = "",
                 distillate: str = "", scope: str = "global") -> None:
        self.expert = expert
        self.url = url
        self.note = note
        self.distillate = distillate
        self.scope = scope

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Reference {self.expert} {self.url[:60]!r} scope={self.scope}>"

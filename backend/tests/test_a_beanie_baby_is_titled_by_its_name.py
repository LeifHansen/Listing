"""A Ty Beanie Baby is titled with its name, then "Beanie Baby Ty".

Asked for as: "for beanie baby listings, change the title formatting to Beanie
name + 'Beanie Baby Ty'".

So "Princess Beanie Baby Ty", not "Ty Beanie Baby Princess the Bear 1997
Retired PE Pellets Purple" -- the general title rule's answer, which leads with
the brand and spends all 80 characters. The format is the seller's and it
outranks that rule, for this one vertical and nothing else.

Two halves, pinned separately. The PROMPT tells every pass that writes a title
(the draft, research, the art lookup, End & relist's fresh copy, a refine); the
GUARD (services/beanie) holds what comes back to the format, because a
paragraph in a long prompt is a request and the seller asked for a rule. The
guard is careful in the other direction too: a newborn's knit "beanie baby
hat", a Teenie Beanie, a Beanie Boo and a lot of twelve are not touched, and a
Beanie Baby whose name cannot be found keeps its title and gets a note rather
than a guessed name.

services/beanie and services/listing_prompt are imported; claude_ai and main
are read as text, because the Anthropic SDK is not installed in CI's light job.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import beanie, listing_prompt

SERVICES = Path(__file__).resolve().parents[1] / "services"
CLAUDE_AI = SERVICES / "claude_ai.py"
MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _draft(title, brand="Ty", category="", specifics=(), missing=()):
    """Just the fields the guard reads -- a Listing without pydantic."""
    return SimpleNamespace(
        title=title, brand=brand, category_suggestion=category,
        item_specifics=[SimpleNamespace(name=n, value=v) for n, v in specifics],
        missing_info=list(missing))


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _title_rule() -> str:
    text = listing_prompt.LISTING_SCHEMA
    start = text.index("- Title must be")
    return _flat(text[start:text.index("\n- Description:", start)])


# --- the format ------------------------------------------------------------

@pytest.mark.parametrize("title, wanted", [
    # The general rule's answer, which is what the drafts used to say.
    ("Ty Beanie Baby Princess the Bear 1997 Retired PE Pellets Purple",
     "Princess Beanie Baby Ty"),
    # The format, with words tacked on after it.
    ("Princess Beanie Baby Ty Retired 1997", "Princess Beanie Baby Ty"),
    # The collector's "the Bear" is not part of the name on the tag.
    ("Princess the Bear Beanie Baby Ty", "Princess Beanie Baby Ty"),
    # ...and the trademark sign off the swing tag is not either.
    ("Ty Beanie Baby Spot the Dog™ 1993 Retired", "Spot Beanie Baby Ty"),
    # A name that starts with "The" keeps it.
    ("Ty Beanie Baby The End the Bear 1998", "The End Beanie Baby Ty"),
])
def test_the_title_is_the_name_then_beanie_baby_ty(title, wanted):
    draft = _draft(title)
    beanie.retitle(draft)
    assert draft.title == wanted
    assert not draft.missing_info


def test_a_title_already_in_the_format_is_left_exactly_alone():
    draft = _draft("Peace Beanie Baby Ty")
    assert beanie.retitle(draft) is False
    assert draft.title == "Peace Beanie Baby Ty"


def test_the_character_specific_names_it_when_the_title_does_not():
    """The draft is told to put the same name in Character, so a title that
    lost the name can get it back from there."""
    draft = _draft("Ty Beanie Babies Tie Dye Bear 1993",
                   specifics=[("Character", "Garcia")])
    beanie.retitle(draft)
    assert draft.title == "Garcia Beanie Baby Ty"


def test_a_description_is_never_taken_for_a_name():
    """"Purple Bear" in Character is what the toy looks like, not what Ty
    called it. Leading the title with it would be the one wrong word in a
    format that is only the name."""
    draft = _draft("Ty Beanie Baby Purple Bear 1997",
                   specifics=[("Character", "Purple Bear")])
    assert beanie.retitle(draft) is False
    assert draft.title == "Ty Beanie Baby Purple Bear 1997"


def test_no_name_means_the_title_stands_and_the_seller_is_told_once():
    draft = _draft("Ty Princess Diana Bear Purple 1997",
                   category="Toys & Hobbies > Beanbag Plush > Ty > "
                            "Beanie Babies-Original")
    beanie.retitle(draft)
    beanie.retitle(draft)
    assert draft.title == "Ty Princess Diana Bear Purple 1997"
    assert draft.missing_info == [beanie.NOTE]
    assert beanie.SUFFIX in beanie.NOTE


def test_the_category_alone_is_enough_to_know_it_is_one():
    """Research can hand back a title with no "Beanie Baby" in it; the
    category the draft asked for still says what the item is."""
    draft = _draft("Ty Spot the Dog 1993",
                   category="Toys & Hobbies > Beanbag Plush > Ty > "
                            "Beanie Babies-Original")
    beanie.retitle(draft)
    assert draft.title == "Spot Beanie Baby Ty"


# --- what is not a Beanie Baby ---------------------------------------------

@pytest.mark.parametrize("title, brand", [
    # A beanie, for a baby. Listed in exactly these words, and made by
    # anyone but Ty.
    ("Carter's Knit Beanie Baby Hat for the Winter 0-3 Months", "Carter's"),
    # Ty's other lines.
    ("Ty Teenie Beanie Babies Princess the Bear McDonald's 1997", "Ty"),
    ("Ty Beanie Boo Slush the Husky", "Ty"),
    ("Ty Beanie Buddies Peace the Bear", "Ty"),
    # More than one toy has no one name to lead with.
    ("Lot of 12 Ty Beanie Babies Bears Retired", "Ty"),
])
def test_anything_else_is_not_touched(title, brand):
    draft = _draft(title, brand=brand)
    assert beanie.retitle(draft) is False
    assert draft.title == title
    assert not draft.missing_info


def test_a_proposed_title_is_judged_against_the_draft_it_is_for():
    """Research and the fresh copy hand back a title to put on an existing
    draft; the draft still says what the item is."""
    draft = _draft("Princess Beanie Baby Ty")
    proposed = "Ty Beanie Baby Princess the Bear Retired 1997 Purple"
    assert beanie.house_title(draft, proposed) == "Princess Beanie Baby Ty"
    assert beanie.house_title(_draft("Pyrex 441 mixing bowl", brand="Pyrex"),
                              "Pyrex Cinderella 441 bowl") is None


def test_the_name_has_room_and_the_ceiling_is_ebays():
    pytest.importorskip("pydantic")
    from backend.models import TITLE_MAX_CHARS
    assert beanie.TITLE_MAX == TITLE_MAX_CHARS
    assert len("x" * beanie.MAX_NAME + " " + beanie.SUFFIX) == TITLE_MAX_CHARS
    draft = _draft("y" * (beanie.MAX_NAME + 1) + " Beanie Baby Ty")
    assert beanie.house_title(draft) is None


# --- the prompt -------------------------------------------------------------

def test_the_draft_is_told_the_format_with_an_example():
    rule = _title_rule()
    assert "TY BEANIE BABIES ARE TITLED DIFFERENTLY" in rule
    assert f"Princess {beanie.SUFFIX}" in rule
    assert "outranks the order and the budget" in rule
    assert "Character item specific" in rule
    assert "never \"Princess the Bear\"" in rule


def test_the_draft_is_told_what_is_not_the_format():
    rule = _title_rule()
    for other in ("Teenie Beanie Babies", "Beanie Buddies", "Beanie Boos",
                  "a lot of several"):
        assert other in rule, f"the rule no longer excludes {other!r}"


def test_every_later_pass_that_writes_a_title_carries_the_exception():
    """Research, the art lookup and the fresh copy read TITLE_BUDGET_AND_BANS;
    a refine and the fresh copy read REFINE_ORDER_RULE. Each of them is
    otherwise told to spend 70-80 characters, which is the old format."""
    exception = listing_prompt.BEANIE_TITLE_EXCEPTION
    assert f"Princess {beanie.SUFFIX}" in exception
    assert exception in listing_prompt.TITLE_BUDGET_AND_BANS
    assert exception in listing_prompt.REFINE_ORDER_RULE


# --- the wiring -------------------------------------------------------------

def _block(source: str, start: str) -> str:
    """One top-level function's text."""
    return re.split(r"\n(?=\S)", source[source.index(start):])[0]


def test_every_first_draft_is_held_to_the_format():
    """In BOTH identify backends, beside the subtitle they each clear at
    first draft -- the router is a pure pass-through, and the single route,
    the bulk worker and the shelf scan all come through one of the two."""
    claude = _block(CLAUDE_AI.read_text(), "def _identify_claude(")
    google = _block((SERVICES / "google_ai.py").read_text(), "def identify(")
    for name, body in (("claude", claude), ("google", google)):
        assert "beanie.retitle(listing)" in body, (
            f"the {name} draft is no longer held to the Beanie Baby format")


def test_a_fresh_copy_keeps_the_format():
    fresh = _block(CLAUDE_AI.read_text(), "def fresh_copy(")
    assert "beanie.house_title(" in fresh


def test_a_research_rewrite_keeps_the_format():
    research = _block(MAIN.read_text(), "def _research_draft(")
    assert "beanie.house_title(" in research
    assert "beanie.retitle(listing)" in research


def test_a_refine_is_the_sellers_and_is_not_rewritten():
    """The seller typing "add Retired to the title" is the seller deciding,
    and the editor is their hand: neither goes through the guard."""
    refine = _block(CLAUDE_AI.read_text(), "def refine(")
    assert "beanie." not in refine


def test_the_short_title_still_lands_on_the_beanie_babies_shelf():
    """The store-shelf match reads the draft's words, and the new title has
    far fewer of them than the old one did."""
    pytest.importorskip("pydantic")
    from backend.models import Listing
    from backend.services import store_category
    shelves = [
        {"id": "11", "name": "Clothing", "path": "Clothing", "level": 1},
        {"id": "21", "name": "Beanie Babies", "path": "Toys > Beanie Babies",
         "level": 2},
        {"id": "99", "name": "Other Items", "path": "Other Items", "level": 1},
    ]
    draft = Listing(title="Princess Beanie Baby Ty", brand="Ty",
                    category_suggestion="Toys & Hobbies > Beanbag Plush > Ty",
                    category_id="49019", price=24.99)
    hit = store_category.match(draft, shelves)
    assert hit and hit["name"] == "Beanie Babies"

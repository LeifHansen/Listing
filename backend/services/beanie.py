"""How a Ty Beanie Baby is titled: its name, then "Beanie Baby Ty".

"Princess Beanie Baby Ty". "Peace Beanie Baby Ty". "Garcia Beanie Baby Ty".
That is the seller's house format for the vertical, and it is the one place in
the app where a title deliberately does NOT spend the 80 characters or lead
with the brand: the name on the swing tag is what a Beanie Baby buyer types,
and the year, colour, tag generation and "retired" all have item specifics and
a description to live in.

The prompt says so (listing_prompt's title rule, and the shorter
BEANIE_TITLE_EXCEPTION every later pass that writes a title carries). This
module is the half that does not depend on a model reading a paragraph: given a
draft, is it a Beanie Baby, what is its name, and what is its title. It is
applied where a title is WRITTEN FOR the seller -- the first draft, a research
rewrite, End & relist's fresh copy -- and never where the seller wrote it: a
refine is the seller's instruction, and the editor is the seller's hand.

Where the name cannot be found, the title is left exactly as drafted and the
seller is told what the format wants. A guessed name is worse than a long
title: it is the one word in this format a buyer searches on.

Standard library only, like every other module that decides what a listing
says (see test_a_rule_can_be_read_without_the_sdk).
"""
from __future__ import annotations

import re
from typing import Optional

#: What follows the name. One copy, read by the prompt's tests and by the
#: guard below, so the two cannot drift apart.
SUFFIX = "Beanie Baby Ty"

#: eBay's ceiling, restated rather than imported from models (which drags in
#: pydantic). test_a_beanie_baby_is_titled_by_its_name pins the two equal.
TITLE_MAX = 80
MAX_NAME = TITLE_MAX - len(SUFFIX) - 1

_BEANIE_BABY = re.compile(r"\bbeanie\s*bab(?:y|ies)\b", re.I)

# Ty, as a word. "Beanie Baby" alone is not enough: a newborn's knit hat is a
# "beanie" for a "baby", and it is listed in exactly those words.
_TY = re.compile(r"\bty\b", re.I)

# Ty's OTHER lines, and the McDonald's minis. Every one has "Beanie" in its
# name and none of them is a Beanie Baby: a Teenie Beanie titled as a Beanie
# Baby promises a bigger toy than the one in the box.
_ANOTHER_LINE = re.compile(
    r"\bteenie\b|\bbeanie\s*(?:boos?|buddy|buddies|bellies|kids|ballz|boppers)\b",
    re.I)

# More than one toy. A lot has no ONE name to put first, so it keeps the
# general title rule.
_A_LOT = re.compile(
    r"\b(?:lot|lots|bundle|bulk|set\s+of|\d+\s*(?:pc|pcs|piece|pieces))\b", re.I)

# A title already in the house format, possibly with words tacked on after it.
_HOUSE = re.compile(r"^(?P<name>.*?)\s*\bbeanie\s+bab(?:y|ies)\s+ty\b", re.I)

# Words that are never a Beanie Baby's name, wherever they sit around one.
# Not "the": The End is a Beanie Baby.
_NOISE = {
    "ty", "ty's", "tys", "beanie", "baby", "babies", "original",
    "retired", "rare", "vintage", "new", "nwt", "mwmt", "mint", "authentic",
    "genuine", "collectible", "plush", "with", "w/", "tag", "tags", "-", "|",
}

# The species. "Purple Bear" is a description, not a name, so a Character
# specific that ENDS in one of these is not trusted to be the name on the tag.
_SPECIES = {
    "bear", "bears", "dog", "puppy", "cat", "kitten", "bunny",
    "rabbit", "pig", "frog", "duck", "bird", "fish", "lamb", "cow", "horse",
    "pony", "unicorn", "dragon", "lion", "tiger", "elephant", "monkey",
    "mouse", "owl", "penguin", "turtle", "snake", "lobster", "crab",
    "octopus", "dolphin", "whale", "seal", "giraffe", "zebra", "leopard",
    "panda", "koala", "kangaroo", "moose", "deer", "reindeer", "fox", "wolf",
    "raccoon", "skunk", "squirrel", "bat", "butterfly", "ladybug", "spider",
    "bee", "flamingo", "peacock", "rooster", "chicken", "goose", "swan",
    "eagle", "parrot", "toucan", "pelican", "alligator", "crocodile",
    "lizard", "iguana", "dinosaur", "hippo", "rhino", "camel", "goat",
    "sheep", "hedgehog", "otter", "platypus", "animal", "toy", "beanbag",
}

_YEAR = re.compile(r"^(?:19|20)\d{2}$")

NOTE = (f"Check the title — a Beanie Baby is titled with its name from the "
        f"swing tag, then “{SUFFIX}” (e.g. “Princess {SUFFIX}”).")


def _text(listing) -> str:
    """The fields that say what kind of thing this is: the title, the
    category the draft asked for, and the item specifics' values."""
    specs = " ".join(str(getattr(s, "value", "") or "")
                     for s in (getattr(listing, "item_specifics", None) or ()))
    return " ".join([str(getattr(listing, "title", "") or ""),
                     str(getattr(listing, "category_suggestion", "") or ""),
                     specs])


def is_beanie_baby(listing, title: Optional[str] = None) -> bool:
    """Whether this draft is ONE Ty Beanie Baby -- made by Ty, not a Teenie
    Beanie, a Beanie Boo or Buddy, and not a lot of several."""
    title = getattr(listing, "title", "") if title is None else title
    text = f"{title or ''} {_text(listing)}"
    by_ty = (str(getattr(listing, "brand", "") or "").strip().lower() == "ty"
             or bool(_TY.search(text)))
    return (by_ty and bool(_BEANIE_BABY.search(text))
            and not _ANOTHER_LINE.search(text)
            and not _A_LOT.search(title or ""))


def _bare(word: str) -> str:
    """One word as a dictionary would spell it: no quotes, brackets or
    trademark signs around it, lowercase."""
    return word.strip("\"'“”‘’()[],:;™®©").lower()


def _is_species(word: str) -> bool:
    return _bare(word) in _SPECIES


def _clean(name: str) -> str:
    """A candidate name with everything that is not the name taken off it:
    the trademark signs off the swing tag, Ty and "Beanie Baby" around it,
    and the collector's "the Bear" after it. "" when nothing is left that
    could be a name, or too much is left to be one."""
    name = re.sub(r"[™®©]", "", name or "")
    words = [w.strip("\"'“”‘’()[],:;") for w in name.split()]
    words = [w for w in words if w]
    if len(words) > 2 and words[-2].lower() == "the" and _is_species(words[-1]):
        words = words[:-2]
    while words and words[0].lower() in _NOISE:
        words.pop(0)
    while words and words[-1].lower() in _NOISE:
        words.pop()
    name = " ".join(words)
    if not re.search(r"[A-Za-z]", name) or len(name) > MAX_NAME:
        return ""
    return name


def _from_house_title(title: str) -> str:
    """The name in front of "Beanie Baby Ty", when the title already has the
    format -- with anything tacked on after it dropped."""
    match = _HOUSE.match(title or "")
    return _clean(match.group("name")) if match else ""


def _from_character(listing) -> str:
    """The Character item specific, which the draft is told to fill with the
    same name -- unless it reads as a description ("Purple Bear")."""
    for spec in getattr(listing, "item_specifics", None) or ():
        if str(getattr(spec, "name", "")).strip().lower() != "character":
            continue
        name = _clean(str(getattr(spec, "value", "") or ""))
        if name and not _is_species(name.split()[-1]):
            return name
    return ""


def _from_the_animal(title: str) -> str:
    """The name in "Princess the Bear", the way collectors write it: the one
    or two words right before "the <animal>", stopping at anything that is
    not a name ("Ty Beanie Baby Princess the Bear" -> "Princess")."""
    words = (title or "").split()
    for i, word in enumerate(words):
        if word.lower() != "the" or i == 0 or i + 1 >= len(words) \
                or not _is_species(words[i + 1]):
            continue
        name: list[str] = []
        for prior in reversed(words[max(0, i - 2):i]):
            bare = _bare(prior)
            if not bare or bare in _NOISE or _YEAR.match(bare) \
                    or bare in _SPECIES:
                break
            name.insert(0, prior)
        found = _clean(" ".join(name))
        if found:
            return found
    return ""


def name_of(listing, title: Optional[str] = None) -> str:
    """This Beanie Baby's name, from the best evidence there is, or ""."""
    title = getattr(listing, "title", "") if title is None else title
    return (_from_house_title(title) or _from_character(listing)
            or _from_the_animal(title))


def house_title(listing, title: Optional[str] = None) -> Optional[str]:
    """The title this draft carries in the house format, or None when it is
    not a Beanie Baby or its name cannot be found. `title` is a proposed
    title to judge in place of the draft's own (a research suggestion, a
    fresh copy) -- the rest of the draft still says what the item is."""
    if not is_beanie_baby(listing, title):
        return None
    name = name_of(listing, title)
    return f"{name} {SUFFIX}" if name else None


def retitle(listing) -> bool:
    """Put a Beanie Baby draft's title in the house format, in place.

    Returns whether the title changed. A Beanie Baby whose name cannot be
    found keeps its title and gets a note saying what the format wants,
    once; anything that is not a Beanie Baby is not touched at all.
    """
    if not is_beanie_baby(listing):
        return False
    wanted = house_title(listing)
    if wanted is None:
        missing = list(getattr(listing, "missing_info", None) or [])
        if NOTE not in missing:
            listing.missing_info = [*missing, NOTE]
        return False
    if wanted == (listing.title or ""):
        return False
    listing.title = wanted
    return True

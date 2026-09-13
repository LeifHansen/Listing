"""The artists who actually trade, and what their names are really spelled like.

An attribution is the most valuable thing the art expert produces and the most
dangerous. "Marc Chagall" on a lithograph is most of its price; the same words
on something Chagall never touched is a return, a case, and a seller who
believed us. The lookup already refuses to name anyone it cannot source -- "a
guessed attribution is worse than a blank" -- but there is a failure it cannot
see from inside one request: a name that is ALMOST right. "Salvadore Dali",
"Marc Chagal", "Piet Mondrain". Each is one letter from a real artist, each
reads as confident, and each is a title no collector will ever search for.

So the expert carries a roster of the artists whose work actually turns over,
and does four different things with a name depending on what the roster says:

  IN THE ROSTER          normalise the spelling to the catalogued form, and
                         take it as corroboration.
  A NEAR MISS OF ONE     rewrite to the canonical name and say so. This is the
                         only correction the roster is allowed to make, and it
                         is safe precisely because it only fires when a real
                         artist is plainly meant.
  ABSENT, NAME-SHAPED    LEAVE IT COMPLETELY ALONE. A thousand artists is a
                         rounding error against the long tail, and most of
                         what a reseller finds is by someone no roster holds.
  ABSENT, NOT A NAME     do not put it in the title or the brand. Ask instead.

THE THIRD ONE IS THE WHOLE DESIGN. A roster that rejects what it does not
recognise is a censor: it would demote every regional painter, every listed
local artist, every signature the lookup read correctly off a margin -- which
is the "NEVER resolve a doubt downward" rule the art path is built on, broken
by the thing meant to strengthen it. The roster may raise confidence and fix
spelling. It may never be the reason a name is dropped.

Data lives in data/artists.json, built by scripts/build_art_roster.py from
Getty ULAN, Wikidata and museum open data. Pure stdlib, loaded lazily, so it
stays readable in CI's light job like every other rule in this package.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).resolve().parent / "data" / "artists.json"

# How many single-character edits may separate a reading from a roster name
# before it stops being a misspelling and starts being a different person.
# Two: "Salvadore Dali" -> "Salvador Dali" is one, "Chagal" -> "Chagall" is
# one, and "Monet" -> "Manet" is also one -- which is why the surname guard
# below exists and why this is not simply raised.
MAX_EDITS = int(os.getenv("ART_ROSTER_MAX_EDITS", "2") or 2)

# ...and how long a name must be before an edit-distance match means anything.
# At four characters, two edits reaches most of the dictionary.
MIN_FUZZY_LEN = 6

_CACHE: Optional[dict] = None


def _strip_accents(text: str) -> str:
    """"Dalí" -> "Dali". Sellers type ASCII and the catalogues do not."""
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def normalise(name: str) -> str:
    """A name reduced to what two spellings of it have in common.

    Case, accents, punctuation and the "Surname, Forename" inversion the
    auction world writes in all go, because none of them is a difference
    between two artists and all of them are differences between two people
    typing the same artist.
    """
    text = _strip_accents(str(name or "")).lower().strip()
    if "," in text:
        # "Chagall, Marc" -> "marc chagall"
        surname, _, forename = text.partition(",")
        if forename.strip():
            text = f"{forename.strip()} {surname.strip()}"
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def surname(name: str) -> str:
    parts = normalise(name).split()
    return parts[-1] if parts else ""


def _edits(a: str, b: str) -> int:
    """Edit distance counting a TRANSPOSITION as one change, not two.

    Damerau-Levenshtein (optimal string alignment), because the commonest
    typo in a name is two letters swapped -- "Mondrain" for "Mondrian",
    "Chagalll" for "Chagall" -- and plain Levenshtein charges two for it,
    which puts exactly the misspellings this is for outside the budget.

    Short-circuits on a length gap wider than the budget, so a long name is
    not compared letter by letter with a short one.
    """
    if abs(len(a) - len(b)) > MAX_EDITS:
        return MAX_EDITS + 1
    rows = [list(range(len(b) + 1))]
    for i, ca in enumerate(a, 1):
        row = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            row[j] = min(rows[i - 1][j] + 1,        # deletion
                         row[j - 1] + 1,            # insertion
                         rows[i - 1][j - 1] + (ca != cb))   # substitution
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)   # transposition
        rows.append(row)
    return rows[-1][-1]


# A name is one to four words of letters, with the hyphens, apostrophes and
# full stops that real names carry. No digits -- "Untitled 1987" is a work,
# not a person -- and nothing long enough to be a sentence.
_NAME_TOKEN = re.compile(r"^[^\W\d_][\w'’.\-]*$", re.UNICODE)


def is_name_shaped(name: str) -> bool:
    """Whether a reading could be somebody's name at all.

    Not "is this a real artist" -- the roster is in no position to say. This
    only separates a plausible name the roster happens not to hold (which must
    be left alone) from the kind of string that is a misread of the picture
    rather than of a signature: a caption, a date, a phrase, a scrawl
    transcribed as punctuation.

    Single-word names pass. Hokusai, Banksy, Christo, Erté are all one word,
    and a rule that required two would demote them.
    """
    text = str(name or "").strip()
    if not (2 <= len(text) <= 65):
        return False
    tokens = text.split()
    if not (1 <= len(tokens) <= 4):
        return False
    return all(_NAME_TOKEN.match(t) for t in tokens)


def _load() -> dict:
    """The roster, indexed by every spelling it knows. Lazy and cached.

    A missing or unreadable file is an EMPTY roster, never an error: every
    caller's behaviour on "not in the roster" is to leave the attribution
    alone, so an app with no roster file drafts exactly as it did before there
    was one.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    index: dict = {}
    entries: list = []
    try:
        raw = json.loads(_DATA.read_text(encoding="utf-8"))
        entries = raw.get("artists") if isinstance(raw, dict) else raw
    except Exception:  # noqa: BLE001 - no roster is a working roster
        entries = []
    # A spelling that two artists answer to identifies NEITHER of them.
    #
    # The roster holds three Wyeths, and "Wyeth" is a perfectly good aka for
    # each. Taking the first one and writing "Andrew Wyeth" onto a listing
    # signed "N.C. Wyeth" is a worse error than the vagueness it replaced --
    # it is a confident attribution to the wrong person, which is the one
    # thing the art path exists to avoid. So a colliding key is dropped, and
    # the reading is left exactly as it was read.
    # A bare SURNAME shared by two artists on the roster is nobody's alias,
    # however the data lists it.
    #
    # The collision check below only fires when two entries actually claim the
    # same spelling -- and the roster holds three Wyeths of whom only one
    # happens to list "Wyeth" as an alias. That is a data accident, and the
    # cost of it is a listing signed "N.C. Wyeth" confidently attributed to
    # Andrew. So the shared surnames are computed from the canonical names and
    # struck out structurally, rather than trusted not to appear.
    shared = {}
    for entry in entries or []:
        if isinstance(entry, dict) and str(entry.get("name") or "").strip():
            last = surname(entry["name"])
            if last:
                shared.setdefault(last, set()).add(entry["name"].strip())
    ambiguous_surnames = {last for last, owners in shared.items()
                          if len(owners) > 1}

    claimed: dict = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("name") or "").strip()
        if not canonical:
            continue
        for spelling in [canonical, *(entry.get("aka") or [])]:
            key = normalise(spelling)
            if not key:
                continue
            if key in ambiguous_surnames and key != normalise(canonical):
                # A one-word alias that is a surname two artists share.
                continue
            owner = claimed.get(key)
            if owner is None:
                claimed[key] = canonical
                index[key] = entry
            elif owner != canonical:
                # Two different artists, one spelling. Neither gets it -- but
                # a canonical NAME always beats an aka, because "Marc Chagall"
                # in full is not ambiguous just because somebody else listed
                # it as an alternate.
                if spelling.strip() == canonical:
                    claimed[key] = canonical
                    index[key] = entry
                else:
                    index.pop(key, None)
                    claimed[key] = "\x00ambiguous"
    _CACHE = {"index": index, "entries": [e for e in (entries or [])
                                          if isinstance(e, dict)]}
    return _CACHE


def reload() -> None:
    """Drop the cache. For tests, and for a roster refreshed under a running
    process."""
    global _CACHE
    _CACHE = None


def size() -> int:
    return len(_load()["entries"])


def lookup(name: str) -> Optional[dict]:
    """The roster entry for `name` under any spelling it knows, or None."""
    if not name:
        return None
    return _load()["index"].get(normalise(name))


def near_miss(name: str) -> Optional[dict]:
    """The roster entry `name` is probably a misspelling of, or None.

    Guarded three ways, because a false positive here renames somebody else's
    painting:

      * the reading must not already BE a roster name (that is `lookup`);
      * the two must share a surname, exactly, or differ by one edit in a
        surname long enough for that to mean something -- which is what keeps
        Monet and Manet apart while still catching Chagal and Chagall;
      * and the whole name must be within MAX_EDITS.
    """
    if not name or lookup(name):
        return None
    key = normalise(name)
    if len(key) < MIN_FUZZY_LEN:
        return None
    mine = surname(name)
    best = None
    best_distance = MAX_EDITS + 1
    for candidate, entry in _load()["index"].items():
        theirs = candidate.split()[-1] if candidate else ""
        if not theirs:
            continue
        if theirs != mine:
            # A different surname is a different artist unless the surname
            # itself is what was misspelled -- and then only when it is long
            # enough that one letter is a typo rather than a whole name.
            if len(theirs) < MIN_FUZZY_LEN or _edits(mine, theirs) > 1:
                continue
        distance = _edits(key, candidate)
        if distance < best_distance:
            best, best_distance = entry, distance
    return best if best_distance <= MAX_EDITS else None


# --- what the merge actually asks -------------------------------------------

#: The four outcomes, as returned by `resolve`.
KNOWN = "known"            # in the roster; use the catalogued spelling
CORRECTED = "corrected"    # a near miss; rewrite and say so
UNKNOWN = "unknown"        # absent but name-shaped; leave completely alone
UNVERIFIABLE = "unverifiable"   # not a name at all; ask rather than publish


def _match_script(reading: str, canonical: str) -> str:
    """`canonical`, written in the alphabet the reading was written in.

    "Salvador Dali" is not a misspelling of "Salvador Dalí". It is the same
    name typed on a keyboard that has no í, which is every keyboard most
    sellers and most buyers own -- and eBay folds diacritics in search, so the
    accented form wins nothing there and can only lose: a title that renders
    as "Salvador Dal?" somewhere downstream is worse than one that never had
    the accent.

    So the roster fixes SPELLING and leaves the alphabet alone. A reading that
    arrived in plain ASCII goes back in plain ASCII, with the catalogued
    capitalisation -- "Leroy Neiman" still becomes "LeRoy Neiman", because
    case costs nothing and is how the artist writes it. A reading that already
    carried the accents keeps them.
    """
    if not canonical:
        return canonical
    folded = _strip_accents(canonical)
    if folded == canonical:
        return canonical                    # nothing to decide
    if _strip_accents(reading) == reading:
        # The reading is ASCII; give back the ASCII form of the right name.
        return folded
    return canonical


def resolve(name: str) -> tuple[str, str, Optional[dict]]:
    """(outcome, the name to use, the roster entry or None).

    The name to use is ALWAYS safe to write where the reading was going to be
    written -- except under UNVERIFIABLE, which means do not write it at all.
    """
    reading = str(name or "").strip()
    if not reading:
        return (UNVERIFIABLE, "", None)
    entry = lookup(reading)
    if entry:
        return (KNOWN,
                _match_script(reading, str(entry.get("name") or reading)), entry)
    entry = near_miss(reading)
    if entry:
        return (CORRECTED,
                _match_script(reading, str(entry.get("name") or reading)), entry)
    if is_name_shaped(reading):
        # The important branch. Absent from the roster is not absent from art
        # history, and the roster is in no position to say otherwise.
        return (UNKNOWN, reading, None)
    return (UNVERIFIABLE, reading, None)


def correction_note(reading: str, canonical: str) -> str:
    return (f"The signature was read as “{reading}”, which is one or "
            f"two letters from “{canonical}” -- a listed artist whose "
            f"work trades. The draft uses the catalogued spelling, because it "
            f"is the one buyers search for. Check it against the signature "
            f"before publishing.")


def unverifiable_note(reading: str) -> str:
    return (f"“{reading}” was read off the piece but does not look "
            f"like an artist's name, so it has not been used as one. If it is "
            f"the signature, type it as it reads; if it is a title, a caption "
            f"or a publisher, it belongs in the description instead.")

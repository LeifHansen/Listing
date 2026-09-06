"""Product identifiers read off a barcode — and whether they can be believed.

A vision model reading the digits printed under a barcode is doing OCR on
6-point type, and it is wrong often enough to matter: an 8 becomes a 3, a 5
becomes a 6, a leading zero disappears into the guard bar. Everywhere else in
this app a misread is a cosmetic problem the seller can fix. Here it is not.
A UPC is the one field on an eBay listing that names a DIFFERENT PRODUCT when
it is wrong: eBay matches it against its own catalogue, and a listing carrying
someone else's identifier shows the wrong product page, the wrong photos and
the wrong price history to every buyer who finds it.

The saving grace is that these codes carry their own proof. Every GTIN
(UPC-A, EAN-13, EAN-8, GTIN-14) ends in a mod-10 check digit computed from
the others, and ISBN-10 ends in a mod-11 one. A single misread digit fails
that check ~100% of the time; a transposition of two adjacent digits fails it
unless they differ by a multiple of 5. So a read that passes is evidence, and
a read that fails is not a code at all — it is an OCR error wearing twelve
digits, and it goes to the seller as "check this" rather than onto the
listing.

Nothing here imports anything but the standard library and the app's own
models, so it runs in CI's light job beside listing_prompt.py: the rule that
decides whether a product identifier reaches eBay is not one that may quietly
stop being tested because the image stack is not installed.
"""
from __future__ import annotations

import re

from ..config import log
from ..models import ItemSpecific, Listing

# The lengths a GTIN comes in. 12 is UPC-A (US retail), 13 is EAN-13 (the rest
# of the world, and every ISBN printed since 2007), 8 is EAN-8 (small
# packaging) and 14 is GTIN-14 (a case or inner pack — it appears on the outer
# carton of a boxed item, so photos of boxed stock do turn it up).
_GTIN_LENGTHS = (8, 12, 13, 14)

# ISBN-13 lives inside EAN-13 under these two prefixes (Bookland). 979 also
# carries ISMN music publications; both are "ISBN" as far as eBay's aspect
# names are concerned.
_BOOKLAND = ("978", "979")

# One group of digits as a barcode prints them. A code is transcribed either
# whole ("036000291452") or in the groups the symbology itself sets it in
# ("0 36000 29145 2"), and an ISBN normally arrives hyphenated, so `find`
# below reassembles consecutive groups rather than matching one greedy run —
# a greedy run would swallow two adjacent codes into a 24-digit string that
# is neither of them.
_GROUP = re.compile(r"\d+[Xx]?")
# What may sit BETWEEN two groups of one code. Anything else — a letter, a
# comma, a newline — ends the code, whatever follows it.
_SEPARATORS = frozenset(" \t-\u2010\u2011\u2012\u2013\u2014\u2015.\u00b7")
# How many groups one code may be split into, and how wide a gap may be.
# "0 36000 29145 2" is four groups; an ISBN sets as five.
_MAX_GROUPS = 5
_MAX_GAP = 2

# How long a candidate may be before it is not a mistyped identifier but a
# phone number, a date range or a paragraph of digits.
_MAX_RAW_LEN = 32


def normalize(raw: str) -> str:
    """`raw` reduced to the characters an identifier is actually made of.

    Separators go (a UPC is printed in groups and transcribed with them),
    a trailing X is kept and upper-cased because it is ISBN-10's check
    "digit" for the value ten, and anything else means this was never a
    code — returns "" rather than a partial one.
    """
    text = str(raw or "").strip()
    if not text or len(text) > _MAX_RAW_LEN:
        return ""
    body = "".join(c for c in text if not (c.isspace() or c in "-‐‑‒–—―.·"))
    if not body:
        return ""
    if body[-1] in "Xx" and body[:-1].isdigit():
        return body[:-1] + "X"
    return body if body.isdigit() else ""


def gtin_check_digit(body: str) -> int:
    """The mod-10 check digit for `body` (a GTIN with its own check digit
    removed). Weights alternate 3,1 from the RIGHT, which is what makes the
    rule independent of the code's length."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def valid_gtin(code: str) -> bool:
    """Whether `code` is a well-formed GTIN-8/12/13/14 whose check digit
    agrees with its body."""
    if not code.isdigit() or len(code) not in _GTIN_LENGTHS:
        return False
    return gtin_check_digit(code[:-1]) == int(code[-1])


def valid_isbn10(code: str) -> bool:
    """Whether `code` is a well-formed ISBN-10 (mod 11, final X = ten)."""
    if len(code) != 10 or not code[:9].isdigit():
        return False
    if not (code[9].isdigit() or code[9] == "X"):
        return False
    tail = 10 if code[9] == "X" else int(code[9])
    return (sum(int(c) * (10 - i) for i, c in enumerate(code[:9])) + tail) % 11 == 0


def verified(raw: str) -> bool:
    """Whether `raw` reads as an identifier that proves itself."""
    code = normalize(raw)
    return bool(code) and (valid_gtin(code) or valid_isbn10(code))


def kind(raw: str) -> str:
    """eBay's aspect name for this identifier — "UPC", "EAN" or "ISBN" — or
    "" when the value does not check out. eBay publishes all three as
    separate item specifics, and a code filed under the wrong one is a code
    its catalogue never matches."""
    code = normalize(raw)
    if not code:
        return ""
    if valid_isbn10(code):
        return "ISBN"
    if not valid_gtin(code):
        return ""
    if len(code) == 13 and code[:3] in _BOOKLAND:
        return "ISBN"
    if len(code) == 12:
        return "UPC"
    # A 13-digit code that is a UPC-A with the leading zero the US market
    # omits is still a UPC to a buyer and to eBay's catalogue.
    if len(code) == 13 and code[0] == "0":
        return "UPC"
    return "EAN"


def symbology(raw: str) -> str:
    """What the barcode itself is, for a sentence the seller reads
    ("UPC-A", "EAN-13", "ISBN-10"...). "" when the value does not check out."""
    code = normalize(raw)
    if not code:
        return ""
    if valid_isbn10(code):
        return "ISBN-10"
    if not valid_gtin(code):
        return ""
    if len(code) == 13 and code[:3] in _BOOKLAND:
        return "ISBN-13"
    return {8: "EAN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}[len(code)]


def isbn13(raw: str) -> str:
    """An ISBN-10 as its ISBN-13 (EAN-13) form, "" for anything else.

    Books are the category where this matters most: everything printed since
    2007 carries the 13-digit form, so a 10-digit ISBN off an older book's
    copyright page searches a marketplace that has moved on without it.
    """
    code = normalize(raw)
    if not valid_isbn10(code):
        return ""
    body = "978" + code[:9]
    return body + str(gtin_check_digit(body))


def ebay_gtin(raw: str) -> str:
    """The value to hand eBay's Browse `gtin` search, or "".

    Browse documents that parameter as taking a UPC, so only a code that IS
    one — 12 digits, or the 13-digit form with the leading zero the US market
    drops — goes there. Everything else (a true EAN-13, an ISBN) still
    searches perfectly well as keywords, and `search_terms` below is what
    the caller uses for that.
    """
    code = normalize(raw)
    if not valid_gtin(code):
        return ""
    if len(code) == 12:
        return code
    if len(code) == 13 and code[0] == "0" and code[:3] not in _BOOKLAND:
        return code[1:]
    return ""


def search_terms(raw: str) -> str:
    """The identifier as a keyword query — the digits themselves, which is
    how a buyer pastes an ISBN or an EAN into eBay's search box. "" when the
    value does not check out."""
    code = normalize(raw)
    if valid_isbn10(code):
        return isbn13(code) or code
    return code if valid_gtin(code) else ""


def find(text: str) -> list[dict]:
    """Every identifier in `text` that proves itself, in the order read.

    Written for the tag transcript: a page of prose with a barcode's digits
    somewhere in it, beside the prices, sizes, RN numbers and care symbols
    that live on the same label. The check digit is what separates them — a
    run of digits that is not a GTIN fails it — so nothing here has to
    understand the sentence it was found in. Each entry is
    {"value", "kind", "symbology"}; duplicates collapse.
    """
    text = str(text or "")
    groups = list(_GROUP.finditer(text))
    out: list[dict] = []
    seen: set[str] = set()
    consumed = -1                      # last group index already inside a hit
    for i in range(len(groups)):
        if i <= consumed:
            continue
        joined = ""
        for j in range(i, min(i + _MAX_GROUPS, len(groups))):
            if j > i:
                gap = text[groups[j - 1].end():groups[j].start()]
                if not gap or len(gap) > _MAX_GAP or set(gap) - _SEPARATORS:
                    break
            joined += groups[j].group(0)
            if len(joined) > max(_GTIN_LENGTHS):
                break
            name = kind(joined)
            if not name:
                continue
            code = normalize(joined)
            consumed = j               # these groups are spoken for
            if code not in seen:
                seen.add(code)
                out.append({"value": code, "kind": name,
                            "symbology": symbology(code)})
            break                      # a code is a code; stop extending it
    return out


def looks_like_a_code(raw: str) -> bool:
    """Whether `raw` is shaped like a GTIN/ISBN — the right length, all
    digits — regardless of whether its check digit agrees.

    This is the guard's other half. `verified` says what may be written; this
    says what may not be waved through as "probably an MPN": a bare 12-digit
    number in a UPC box is a UPC whatever the model called it, and if it does
    not check out it is a misread, not a part number.
    """
    code = normalize(raw)
    if not code:
        return False
    return (code.isdigit() and len(code) in _GTIN_LENGTHS) or len(code) == 10


# ---------------------------------------------------------------------------
# What the scanner read, and what may be written from it.
#
# Everything above is arithmetic over a string. Everything below is the policy
# the arithmetic exists to enforce, kept here rather than in main.py so it is
# testable without the vision stack installed — the same reason the prompt
# rules live in listing_prompt.py.
# ---------------------------------------------------------------------------

# eBay's aspect name for an identifier the scan verified. A mapping rather
# than "just use the kind", so a new kind cannot silently invent an aspect
# name eBay has never heard of.
_ASPECTS = {"UPC": "UPC", "EAN": "EAN", "ISBN": "ISBN"}
# The unverifiable ones. An MPN or a model number carries no check digit, so
# nothing can confirm it: it goes on at "medium" — the editor's review flag —
# rather than as if it had been read off a barcode and checked.
_UNVERIFIED_ASPECTS = {"mpn": "MPN", "model": "Model"}
# The aspects a listing might already be carrying a product code under.
CODE_ASPECTS = ("upc", "ean", "isbn", "gtin")


def from_scan(raw) -> list[dict]:
    """The identifiers in an identify response, sorted into what may be
    believed and what may not.

    Everything the model returns here is OCR on 6-point type, and the only
    thing separating a read from a guess is the code's own check digit. So a
    GTIN or ISBN is marked verified only when it agrees with itself; a
    code-shaped value that does not is kept and marked `checksum_failed`, so
    the seller can be asked rather than the listing quietly getting it wrong;
    and an alphanumeric MPN or model number, which has no checksum to agree
    with, rides along unverified.

    Returns [{"value", "kind", "symbology", "source", "verified"}], with
    "checksum_failed" set on the ones that were code-shaped and wrong.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for entry in (raw or [])[:8]:
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("value", "")).strip()[:64]
        declared = str(entry.get("type", "")).strip()[:16] or "other"
        source = str(entry.get("source", "")).strip()[:120]
        # An explicit "I could not read all of it" is taken at its word: a
        # code with a digit missing is not a code, whatever it checksums to.
        if not value or entry.get("legible") is False or "?" in value:
            continue
        name = kind(value)
        if name:
            code = normalize(value)
            if code in seen:
                continue
            seen.add(code)
            out.append({"value": code, "kind": name,
                        "symbology": symbology(code),
                        "source": source, "verified": True})
        elif looks_like_a_code(value):
            # The expensive case, and the reason this module exists: twelve
            # digits that fail their check digit are a misread, not a UPC.
            log.info("scan: %s %r failed its check digit — not written",
                     declared, value)
            out.append({"value": value, "kind": declared, "symbology": "",
                        "source": source, "verified": False,
                        "checksum_failed": True})
        elif value.lower() not in seen:
            seen.add(value.lower())
            out.append({"value": value, "kind": declared, "symbology": "",
                        "source": source, "verified": False})
    return out


def _holds(listing: Listing, name: str) -> bool:
    key = name.strip().lower()
    return any((s.name or "").strip().lower() == key and (s.value or "").strip()
               for s in listing.item_specifics)


def apply_to_listing(listing: Listing, identifiers: list) -> int:
    """Put the codes the scan read off the item onto the draft.

    This is the payoff for reading barcodes at all. A UPC is the only thing on
    a listing that names the EXACT product rather than describing it, which
    makes it both the best item specific eBay can be given and the best comp
    search this app can run (see `listing_code`).

    Three outcomes, and the split is the whole point:

      * a code whose check digit agrees goes on as UPC / EAN / ISBN at
        confidence "high" — it was read, not inferred;
      * a code whose check digit does NOT agree goes nowhere near the
        listing. eBay matches a UPC against its catalogue, so a misread one
        does not bounce: it quietly attaches another company's product page,
        photos and price history to this listing. It becomes a note asking
        the seller to read the barcode again, which takes them five seconds
        with the item in their hand;
      * an MPN or model number goes on at "medium", the editor's review flag.

    Never overwrites: an identifier the listing already carries belongs to the
    seller or to an earlier pass, and this only ever fills a blank. Returns
    how many values were written.
    """
    added = 0
    unreadable: list[str] = []
    for entry in identifiers or []:
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("value", "")).strip()
        entry_kind = str(entry.get("kind", "")).strip()
        if not value:
            continue
        if entry.get("verified"):
            name = _ASPECTS.get(entry_kind.upper())
            if not name or _holds(listing, name):
                continue
            listing.item_specifics.append(
                ItemSpecific(name=name, value=value, confidence="high"))
            added += 1
            log.info("scan: %s %s read from %s",
                     entry.get("symbology") or name,
                     value, entry.get("source") or "the photos")
            continue
        if entry.get("checksum_failed"):
            unreadable.append(value)
            continue
        name = _UNVERIFIED_ASPECTS.get(entry_kind.lower())
        if name and not _holds(listing, name):
            listing.item_specifics.append(
                ItemSpecific(name=name, value=value, confidence="medium"))
            added += 1
    if unreadable:
        # One note however many failed: the seller re-reads the barcode once.
        note = ("confirm the barcode number — we read "
                + ", ".join(unreadable[:3])
                + " off the photos and it isn't a valid code")
        if not any("barcode" in m.lower() for m in listing.missing_info):
            listing.missing_info = [*listing.missing_info, note]
    return added


def listing_code(listing: Listing) -> tuple[str, str]:
    """(the item's verified product code, the UPC eBay Browse will search by).

    Both come off the listing's own item specifics, which is where
    `apply_to_listing` wrote what the scan read — and only ever after the
    check digit agreed, so nothing here has to wonder whether the digits are
    real. The second value is empty for an EAN or ISBN: Browse documents its
    product search as taking a UPC, so those search as keywords instead.
    ("", "") when the item carries no code, which is most of them.
    """
    for spec in listing.item_specifics:
        if (spec.name or "").strip().lower() not in CODE_ASPECTS:
            continue
        value = (spec.value or "").strip()
        if verified(value):
            return search_terms(value), ebay_gtin(value)
    return "", ""

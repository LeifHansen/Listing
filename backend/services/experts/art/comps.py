"""Pricing a print against its ARTIST'S market, not against its title's words.

The generic comp search walks three rungs: the barcode, the whole title, then
the first few words of the title. That works for a thing with a model number
and fails specifically on art, in both directions.

A good art title is a bad search. The title rule packs it with everything that
identifies THIS one sheet -- the artist, the work, the medium, the edition
fraction, "Hand Signed", the size, "Framed" -- and a keyword search for all of
that at once matches nothing at all. That is not "this is rare"; it is "this
query is too specific", and the two are indistinguishable downstream.

Falling back to the first few words is better, and still arbitrary: it takes
whatever the title happened to start with and hopes it is the artist. Sometimes
it is "Marc Chagall Le Bouquet"; sometimes it is "Vintage Framed Original".

Art can do better than hope, because by the time pricing runs the draft has
been through the art lookup and the roster, and the artist, the medium and the
edition are FIELDS rather than guesses. So the rungs are built from those, in
descending specificity, and each one is a real question about the market:

    this exact work -> this artist's signed editions in this medium
    -> this artist in this medium -> this artist at all

The last rung is the one that matters most and is the hardest for a keyword
search to reach on its own. "What does a Chagall lithograph go for" is the
question a seller is really asking, and a $15 open-edition poster and a $1,500
hand-signed edition are both "Chagall lithograph" -- which is why the signed
and numbered words ride the second rung rather than the last, and why nothing
here ever decides a price on its own. It supplies QUERIES. pricing.suggest
still measures, and main._price_against_comps still refuses to lower a number.

Nothing here imports anything heavy.
"""
from __future__ import annotations

import re

# The mediums a collector actually searches, longest first so "oil on canvas"
# is found before "oil". Read out of the title and the specifics rather than
# guessed, because a medium the piece does not have is a query about somebody
# else's market.
MEDIUMS = (
    "oil on canvas", "oil on board", "oil on panel", "acrylic on canvas",
    "acrylic on board", "mixed media", "canvas print", "screenprint",
    "screen print", "silkscreen", "lithograph", "serigraph", "woodblock",
    "woodcut", "linocut", "etching", "engraving", "aquatint", "drypoint",
    "mezzotint", "monotype", "monoprint", "giclee", "giclée", "watercolor",
    "watercolour", "gouache", "pastel", "charcoal", "photograph", "poster",
)

# The words that separate a $1,500 sheet from a $15 one. They ride the middle
# rung: specific enough to find the right half of an artist's market, general
# enough to still match.
_EDITION_WORDS = ("hand signed", "signed", "numbered", "artist proof",
                  "artist's proof", "limited edition")

_ASPECT_MEDIUM = ("print type", "production technique", "medium", "material",
                  "type")


def _specific(listing, *names) -> str:
    wanted = {n.strip().lower() for n in names}
    for s in getattr(listing, "item_specifics", None) or []:
        if str(getattr(s, "name", "")).strip().lower() in wanted:
            value = str(getattr(s, "value", "") or "").strip()
            if value:
                return value
    return ""


def artist_of(listing) -> str:
    """The artist this draft names, from the Artist specific or the brand."""
    return _specific(listing, "artist", "signed by") or \
        str(getattr(listing, "brand", "") or "").strip()


def medium_of(listing) -> str:
    """The medium by its collector name, read off the specifics or the title.

    "" when nothing says -- and "" is honest: a medium invented here would
    price a gouache against the lithograph market.
    """
    stated = _specific(listing, *_ASPECT_MEDIUM).lower()
    haystack = f"{stated} {str(getattr(listing, 'title', '') or '').lower()}"
    for medium in MEDIUMS:
        if medium in haystack:
            return medium
    return ""


def edition_words(listing) -> str:
    """"hand signed numbered", or "" -- the words that move the price, only
    where the draft actually earned them."""
    title = str(getattr(listing, "title", "") or "").lower()
    signed = _specific(listing, "signed").strip().lower() == "yes"
    found = []
    if signed or "hand signed" in title:
        found.append("hand signed")
    elif "signed" in title:
        found.append("signed")
    if "numbered" in title or _specific(listing, "edition size") or \
            re.search(r"\b\d{1,4}\s*/\s*\d{1,5}\b", title):
        found.append("numbered")
    return " ".join(found)


def comp_queries(listing) -> list[str]:
    """Comp searches for this piece, most specific first.

    [] when the draft does not name an artist -- which is most items in this
    app and every item that is not art. An empty list means "nothing to add",
    and the generic title rungs run exactly as they did.
    """
    artist = artist_of(listing)
    if not artist:
        return []
    work = _specific(listing, "title of work", "work", "subject")
    medium = medium_of(listing)
    words = edition_words(listing)

    rungs = []
    if work:
        rungs.append(" ".join(x for x in (artist, work, medium) if x))
    if words:
        rungs.append(" ".join(x for x in (artist, medium, words) if x))
    if medium:
        rungs.append(f"{artist} {medium}")
    rungs.append(artist)

    # Dedupe, keeping order: with no work and no medium, several rungs
    # collapse onto the artist's name and asking eBay the same question three
    # times is three times the latency for one answer.
    seen = set()
    out = []
    for rung in rungs:
        key = " ".join(rung.lower().split())
        if key and key not in seen:
            seen.add(key)
            out.append(" ".join(rung.split()))
    return out

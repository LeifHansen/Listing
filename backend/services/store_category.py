"""Which of the SELLER'S OWN store categories a listing belongs in.

An eBay Store's custom categories are the left-hand nav of that store —
"Vintage Tees", "Beanie Babies", "Tools > Hand Tools" — invented by the seller
and numbered by eBay per account. They are nothing like eBay's site category
tree: no API suggests one, because nobody but this seller knows what their
shelves are called. A listing published without one lands at the top level of
the store, which is where every listing this app ever published landed.

So the match is made here, from words the draft already carries, and it is
made deliberately conservatively:

  * the site category PATH eBay itself resolved is the strongest signal
    ("Clothing, Shoes & Accessories > Men > Men's Clothing > T-Shirts" against
    a shelf called "Men's Tees"), then the title and brand, then the item
    specifics;
  * a category earns its score by having its OWN name matched -- "Vintage
    Tees" needs both words seen, and gets most of its score for it, so a shelf
    called "Tees" cannot beat it on the word they share;
  * a shelf whose name is nothing but filler ("Other", "More items") can never
    be matched INTO, because every listing matches it equally well and the one
    thing worse than an unfiled listing is a wrongly filed one; and
  * below a real threshold the answer is None. Nothing is guessed. The editor
    shows the store's own list, and an empty answer is one dropdown away from
    the seller's own.

Pure and dependency-free -- the eBay call that reads the tree lives in
services/ebay_trading.store_categories, and this decides what to do with it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Where each word can come from, and what it is worth. The path is eBay's own
# answer to "what kind of thing is this", already resolved for this listing;
# the title is the seller's words for the same thing; the specifics are facts
# about it that only sometimes name its shelf.
WEIGHTS = {"path": 3.0, "title": 2.0, "brand": 2.0, "specifics": 1.0}

# What a shelf has to be worth to be filed into: one whole shelf name, seen in
# the listing's own words. A title word carries exactly this, so a shelf called
# "Denim" is matched by a title that says denim — while HALF of "Vintage Tees"
# scores half (the share below), and a word seen only in an item specific is
# worth less than one on its own. Specifics reinforce a match; they never make
# one.
MIN_SCORE = 2.0

# Words that carry no shelf meaning. A name made only of these ("Other",
# "More Items") is a catch-all rather than a description, and matching into it
# is how everything ends up in the same place.
FILLER = frozenset({
    "and", "or", "the", "a", "an", "of", "for", "in", "with", "by", "to",
    "other", "others", "misc", "miscellaneous", "more", "all", "item", "new",
    "sale", "shop", "store", "stuff", "thing", "general", "assorted", "etc",
    "clearance", "featured", "everything", "else",
})

_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text: Any) -> list[str]:
    """The comparable words in a phrase.

    Lowercased, punctuation dropped, and simple plurals folded to their
    singular so "Tees" and "tee" are the same word -- a store shelf is almost
    always named in the plural and an item's title almost never is, which is
    the single most common way a real match used to score zero.
    """
    words = []
    for raw in _WORD.findall(str(text or "").lower()):
        word = raw.strip("'")
        if len(word) > 3 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("es") and word[-3] in "sxzo":
            word = word[:-2]
        elif len(word) > 2 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        if word:
            words.append(word)
    return words


def listing_words(listing: Any) -> dict[str, float]:
    """Every word the draft offers, each worth the most any source pays for it.

    Takes anything with the Listing fields -- the model, or the plain dict a
    bulk draft is still held as.
    """
    def field(name: str, default: str = "") -> Any:
        if isinstance(listing, dict):
            return listing.get(name, default)
        return getattr(listing, name, default)

    sources: list[tuple[str, Iterable[Any]]] = [
        ("path", [field("category_suggestion")]),
        ("title", [field("title")]),
        ("brand", [field("brand")]),
    ]
    specifics = []
    for spec in field("item_specifics", []) or []:
        value = spec.get("value") if isinstance(spec, dict) else getattr(spec, "value", "")
        if value:
            specifics.append(value)
    sources.append(("specifics", specifics))

    words: dict[str, float] = {}
    for source, values in sources:
        weight = WEIGHTS[source]
        for value in values:
            for word in _tokens(value):
                if word in FILLER:
                    continue
                if words.get(word, 0.0) < weight:
                    words[word] = weight
    return words


def score(name: str, words: dict[str, float]) -> float:
    """What one shelf name is worth against those words.

    The sum of what its matched words are worth, scaled by the share of the
    NAME that matched: a two-word shelf with both words seen beats a one-word
    shelf that shares only one of them, which is what makes the more specific
    shelf win where a store has both.
    """
    tokens = [t for t in _tokens(name) if t not in FILLER]
    if not tokens:
        return 0.0                      # a catch-all shelf, never matched into
    hits = [words[t] for t in dict.fromkeys(tokens) if t in words]
    if not hits:
        return 0.0
    return sum(hits) * (len(hits) / len(dict.fromkeys(tokens)))


def match(listing: Any, categories: list[dict]) -> Optional[dict]:
    """The store category this listing belongs in, or None when none does.

    `categories` is the flat list ebay_trading.store_categories returns:
    {"id", "name", "path", "level"}. The whole PATH of a nested shelf is
    scored, not just its leaf name, so "Clothing > Vintage" is reachable by a
    listing that only ever says "vintage" once -- but the leaf's own words are
    what a tie is broken on, then depth, so the most specific shelf that
    actually earned its score wins.
    """
    words = listing_words(listing)
    if not words:
        return None
    best: Optional[tuple[float, int, dict]] = None
    for cat in categories or []:
        if not cat.get("id"):
            continue
        leaf = score(cat.get("name", ""), words)
        # The parents count, at a discount: they are context for the leaf,
        # not the shelf itself.
        whole = score(cat.get("path", "") or cat.get("name", ""), words)
        total = max(leaf, leaf * 0.5 + whole * 0.5)
        if total < MIN_SCORE:
            continue
        rank = (total, int(cat.get("level") or 1), cat)
        if best is None or rank[:2] > best[:2]:
            best = rank
    if best is None:
        return None
    winner = best[2]
    return {"id": str(winner["id"]), "name": winner.get("name", ""),
            "path": winner.get("path", "") or winner.get("name", ""),
            "score": round(best[0], 2)}

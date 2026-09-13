"""Whether an item is a picture with an artist behind it.

The word lists here were main._ART_WORDS and main._ART_CATEGORY_WORDS, which
gated the art lookup. They move to the expert because they now decide two
things rather than one: whether the lookup runs, and -- once routing is on --
whether the item is DRAFTED under the art rule at all. A word list that was
only ever a gate on a $0.02 enrichment call is now also what stops a coffee mug
being read for a plate mark, so it is worth having in one place with a score
attached rather than a bool.

Nothing here imports anything: see experts/base.
"""
from __future__ import annotations

from ..base import CERTAIN, LIKELY, NO_MATCH, WEAK, Subject

name = "art"

# An eBay category that IS art settles it. `category_suggestion` is a
# "A > B > C" path, so the head is checked exactly and the rest by substring.
CATEGORY_WORDS = ("art prints", "paintings", "posters & prints",
                  "art posters", "prints & posters", "mixed media art",
                  "drawings", "art photographs")

# The medium and the marks, in the words a draft actually uses. A hit here is
# strong but not certain, because "poster" and "drawing" turn up in plenty of
# things that are not art (a film poster's frame, a technical drawing).
WORDS = (
    "art print", "giclee", "giclée", "lithograph", "serigraph", "screenprint",
    "screen print", "silkscreen", "etching", "engraving", "woodblock",
    "woodcut", "linocut", "poster", "painting", "watercolor", "watercolour",
    "gouache", "canvas print", "framed print", "artwork", "fine art",
    "exhibition print", "museum print",
    # The words the first pass writes when it has read a margin and not a
    # name: the piece is art, and it is the lookup's to name.
    "drawing", "oil on canvas", "oil on board", "oil on panel",
    "acrylic on canvas", "acrylic on board", "mixed media", "signed print",
    "numbered print", "hand signed", "hand-signed", "artist proof",
    "artist's proof", "original art", "wall art", "sculpture",
    # The words a draft uses when it read the BACK of a painting and took it
    # for a blank canvas or an empty frame. That draft names no artist and no
    # medium, so nothing else here catches it -- and it is precisely the one
    # that needs the expert, because the piece is art and the pass that wrote
    # the title did not know it.
    "stretched canvas", "stretcher bar", "stretcher frame", "blank canvas",
    "artist canvas", "empty frame", "verso",
)

# Marks that are art's and nothing else's. A draft that read one of these off
# the piece is art whatever its title says, which is the case the category and
# the medium words both miss: a photo of a margin with "84/250" penciled in it.
MARKS = ("edition of", "a/p", "artist's proof", "hors commerce", "h/c",
         "plate mark", "blind stamp", "chop mark", "deckled", "catalogue "
         "raisonné", "signed in the plate", "pencil signature", "lower margin")


def category_is_art(listing) -> bool:
    """Whether eBay's own category for this draft is an art category.

    Kept as its own predicate because the merge rules use it directly: an art
    CATEGORY is what licenses creating an Artist specific that eBay would
    carry, as opposed to merely filling one that is already there.
    """
    category = (getattr(listing, "category_suggestion", "") or "").lower()
    return (category.split(">")[0].strip() == "art"
            or any(w in category for w in CATEGORY_WORDS))


def matches(listing, observations: str = "") -> float:
    """How strongly this item reads as art.

    CERTAIN when a pass that LOOKED at the photo says the item is a picture.
    That is the "art" flag off services/orient.screen(), and it outranks every
    word below it: orient is asked the right question ("is this thing's own
    front surface an image?") about the right evidence (the photograph), it
    runs before the cutout and therefore before any draft exists, and it is
    already paid for. A title can be absent or wrong; that answer cannot be
    absent, because the cutout needs it either way.

    Then CERTAIN when eBay's own category says art -- the taxonomy's answer,
    not a keyword guess -- or when a mark only art carries has been read.
    LIKELY on a medium word. WEAK is reserved for the single ambiguous word
    ("poster", "drawing") with nothing else supporting it, because those are
    the ones that would put a film poster or a schematic under the plate-mark
    rule.
    """
    subject = Subject.of(listing, observations)
    if "art" in subject.flags:
        return CERTAIN
    if category_is_art(subject):
        return CERTAIN
    hay = subject.text()
    if any(m in hay for m in MARKS):
        return CERTAIN
    hits = [w for w in WORDS if w in hay]
    if not hits:
        return NO_MATCH
    if len(hits) == 1 and hits[0] in ("poster", "drawing"):
        return WEAK
    return LIKELY

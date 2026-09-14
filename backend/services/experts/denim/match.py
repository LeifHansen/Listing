"""Whether an item is a pair of jeans or a denim jacket.

Denim had no gate before experts: VINTAGE_DENIM_RULE rode in every prompt, so
nothing ever had to decide whether an item was denim. This is that decision,
written for the first time, and it is deliberately narrow -- the cost of a miss
is that a pair of 501s is drafted without the lot-code rule, which is the
failure VINTAGE_DENIM_RULE exists to prevent, so the words below are generous
about what counts as denim and the router keeps a WEAK match rather than
dropping it.

Nothing here imports anything: see experts/base.
"""
from __future__ import annotations

from ..base import CERTAIN, LIKELY, NO_MATCH, WEAK, Subject

name = "denim"

CATEGORY_WORDS = ("jeans", "denim")

# The garment. A hit is LIKELY on its own: "jeans" in a title is nearly always
# a pair of jeans.
WORDS = ("jeans", "denim", "jean jacket", "trucker jacket", "dungarees",
         "overalls", "chore coat")

# The markers the rule is actually about. Any of these means the pass has
# already read something only denim carries, so the rule must be in the prompt
# that reads it next.
MARKS = ("levi", "levis", "levi's", "wrangler", "lee riders", "big e",
         "red tab", "selvedge", "selvage", "arcuate", "lot 501", "501xx",
         "505", "517", "big blue", "care tag", "leather patch", "redline")


def matches(listing, observations: str = "") -> float:
    """How strongly this item reads as denim.

    Denim has no equivalent of orient's art flag -- nothing looks at a photo
    before the draft and asks whether it is jeans, because nothing downstream
    needed to know. So this reads words only, and reads them off whatever is
    available: a group's name before there is a draft, the title and the
    specifics after.
    """
    subject = Subject.of(listing, observations)
    if "denim" in subject.flags:
        return CERTAIN
    category = subject.category_suggestion.lower()
    hay = subject.text()
    if any(m in hay for m in MARKS):
        return CERTAIN
    if any(w in category for w in CATEGORY_WORDS):
        return CERTAIN
    hits = [w for w in WORDS if w in hay]
    if not hits:
        return NO_MATCH
    return LIKELY if len(hits) > 1 or "jeans" in hits else WEAK

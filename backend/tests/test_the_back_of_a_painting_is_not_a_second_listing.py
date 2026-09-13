"""One picture, however many labels are stapled to the back of it.

The grouping pass counts the items in a pile of photos before anything has
been drafted, and the rule it counts by is IDENTITY EVIDENCE OUTRANKS LOOKS:
"Count the tags and patches that READ DIFFERENTLY and expect at least that
many items." That rule is right, and it was written for a pile of clothes,
where a second care label really is a second garment.

Art is the case it inverts, twice.

The back of one framed picture is a wall of marks that all read differently --
the gallery's label, the framer's label, an exhibition label, an auction
house's lot sticker, a certificate in a sleeve, an inventory number, an old
price in grease pencil -- and every one of them belongs to the SAME piece.
Counted the general way, one painting becomes four listings. And the front and
the back of a picture look less alike than any two photos in a pile: one is a
picture, the other is brown paper and wire. So the back gets counted as an
item of its own, and then drafted as one -- which is the "Gronda Blank
Stretched Artist Canvas, $17.99" listing that BLANK_CANVAS_RULE exists for.

BLANK_CANVAS_RULE catches that at IDENTIFY, which is one pass too late: by
then grouping has already decided the back is its own item and identify is
merely being asked to draft it. The only place to stop an item being invented
is the pass that counts them, so art now has the rule denim has had since a
pile of 501s came back as twelve listings for six pairs -- with the axis
turned over. Denim counts pairs by BACKS, because the markers are all on the
back. Art counts pieces by FRONTS, because the picture is the thing and the
back is where the labels pile up.

Grouping is also the one stage that cannot be ROUTED -- there is no draft yet
to decide an item is art from -- so this rule ships on every grouping call,
beside denim's. That is the right trade: grouping is one call per pile, and a
routing miss there makes two live eBay listings out of one painting.
"""
from __future__ import annotations

import pytest

from backend.services.experts import registry
from backend.services.experts.art.rules import ART_FRONT_AND_BACK_RULE
from backend.services.experts.base import Stage
from backend.services.listing_prompt import DENIM_FRONT_AND_BACK_RULE


def _flat(text: str) -> str:
    """The rule with its line wrapping removed, so a test pins the WORDS a
    pass reads and not the column the prompt happens to wrap at."""
    return " ".join(text.split())


# --- what the rule says -----------------------------------------------------

def test_a_front_and_a_back_are_one_listing():
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    assert "A PICTURE IS SHOT FRONT AND BACK, AND BOTH SHOTS ARE ONE LISTING" in rule
    assert "a stack of five prints is five listings, not ten" in rule


def test_the_two_sides_looking_nothing_alike_is_normal():
    """The front is a picture and the back is brown paper and wire. Said
    outright, because it is the single most misleading thing in the pile."""
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    assert "THE TWO SIDES OF ONE PIECE LOOK NOTHING ALIKE, AND THAT IS NORMAL" in rule
    assert "It is the same object, turned over" in rule
    for back in ("stretcher bars", "staples", "cross-brace", "hanging wire",
                 "dust paper"):
        assert back in rule, f"the rule no longer describes a back by its {back}"


def test_pieces_are_counted_by_fronts_not_by_labels():
    """The inversion, and the reason this file exists."""
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    assert "COUNT PIECES BY FRONTS, NOT BY LABELS" in rule
    assert "never the number of labels, stamps and numbers in frame" in rule
    # ...and it names the general rule it is overriding, so a reader of either
    # one finds the other.
    assert "This is the one place the general rule about identity evidence " \
           "must not be applied" in rule
    assert "several marks that read differently are the NORMAL state of a " \
           "single item's back" in rule


def test_the_labels_on_one_back_are_listed_so_none_is_mistaken_for_an_item():
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    for label in ("gallery label", "framer's label", "exhibition label",
                  "auction lot number", "certificate", "old price"):
        assert label.lower() in rule.lower(), f"{label} is no longer named"


def test_a_second_piece_is_a_second_front():
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    assert "A SECOND PIECE IS A SECOND FRONT WHOSE PICTURE DIFFERS" in rule
    assert "Two backs, or two labels, are never two pieces on their own" in rule


def test_the_error_directions_are_named_and_they_are_not_equal():
    """The same asymmetry every rule in this app is built on: a spare photo in
    a draft is one drag to fix, and two live listings for one painting is
    not."""
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    assert "nobody can undo two live eBay listings for one painting" in rule


def test_a_close_up_of_a_margin_is_never_its_own_item():
    """A pencil signature crop is a few square inches of paper with writing on
    it -- it looks like nothing and it is the most valuable photo in the set."""
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    assert "A CLOSE-UP OF A MARGIN IS NEVER ITS OWN ITEM" in rule
    for mark in ("pencil signature", "edition fraction", "embossed chop",
                 "plate mark"):
        assert mark in rule


def test_a_back_shown_alone_rejoins_the_front_before_it():
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    assert "belongs with the group holding the FRONT it was shot with" in rule
    assert "almost always the group immediately before it" in rule
    # ...and the converse is refused, or every pile collapses into one item.
    assert "The reverse is NOT true" in rule


def test_a_photo_is_never_moved_between_pieces():
    """A swapped back is the failure nothing downstream can see: it puts one
    artist's signature and edition number onto another artist's picture."""
    rule = _flat(ART_FRONT_AND_BACK_RULE)
    assert "NEVER MOVE A PHOTO BETWEEN PIECES" in rule
    assert "The back that follows a front is the back OF that front" in rule
    assert "on two listings at once" in rule


# --- and that it reaches the pass that counts ------------------------------

def test_the_rule_reaches_every_grouping_pass():
    """Grouping is unroutable -- there is no draft yet to decide an item is
    art from -- so this rides every grouping call, beside denim's."""
    text = registry.rules_for(Stage.GROUPING)
    assert ART_FRONT_AND_BACK_RULE in text
    assert DENIM_FRONT_AND_BACK_RULE in text


def test_grouping_is_never_routed_away_even_when_the_item_is_plainly_not_art(
        monkeypatch):
    """The load-bearing half. With routing ON and an item that scores zero for
    art, art's grouping rule must STILL be in the prompt -- because at
    grouping there is no item yet, and the thing being decided is how many
    items there are."""
    from backend.services.experts.base import Subject
    monkeypatch.setenv("EXPERT_ROUTING", "on")
    mug = Subject.hint("ceramic coffee mug")
    # Routed away everywhere it can be...
    assert "art" not in registry.names(mug)
    # ...and present at grouping regardless.
    assert ART_FRONT_AND_BACK_RULE in registry.rules_for(Stage.GROUPING, mug)
    assert DENIM_FRONT_AND_BACK_RULE in registry.rules_for(Stage.GROUPING, mug)


def test_the_three_grouping_passes_all_read_it():
    """group_photos runs three prompts -- the split, the verify and the
    re-split -- and a rule in one but not the others is a rule the next pass
    undoes."""
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.services import claude_ai
    for schema in (claude_ai._GROUP_SCHEMA, claude_ai._GROUP_VERIFY_SCHEMA,
                   claude_ai._GROUP_SPLIT_SCHEMA):
        assert ART_FRONT_AND_BACK_RULE in schema
        assert DENIM_FRONT_AND_BACK_RULE in schema

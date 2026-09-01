"""A men's garment sized XXL or larger goes out as Size Type "Big & Tall".

eBay does not treat Size and Size Type as two independent boxes. On men's
apparel it knows that a 2XL is not a "Regular" — send "Size: XXL" with
"Size Type: Regular" and the answer is an item-specifics error and no
listing, after the photos have already uploaded.

Nothing here ever asked, because the two values arrive from different places:
Size is read off the tag, Size Type off whatever the category defaults to. So
the rule now lives in the code — on the draft, where the seller can see and
change it, and again in the sanitize pass, which is the last thing that
touches a listing before it is sent.

The rule fires only where eBay's own aspect list offers a Big & Tall value.
That is what tells a men's category from a women's one, where XXL means Size
Type "Plus", "Regular" is perfectly legal, and stamping "Big & Tall" on it
would be the same mistake pointing the other way.
"""
from __future__ import annotations

import pytest

from backend.models import ItemSpecific, Listing
from backend.services import taxonomy


def _aspect(name, *, values=(), mode="SELECTION_ONLY"):
    return {"name": name, "required": False, "mode": mode,
            "values": list(values), "cardinality": "SINGLE",
            "data_type": "STRING", "format": "", "max_length": 0}


# What eBay answers for a men's casual shirt category.
MENS = [_aspect("Size", values=["S", "M", "L", "XL", "XXL", "3XL", "4XL"]),
        _aspect("Size Type", values=["Regular", "Big & Tall", "Tall", "Big"])]

# ...and for a women's one. Note what is NOT here: the value this rule needs.
WOMENS = [_aspect("Size", values=["S", "M", "L", "XL", "XXL", "3X"]),
          _aspect("Size Type", values=["Regular", "Plus", "Petite",
                                       "Maternity", "Juniors"])]


def _listing(specifics, **kw):
    base = dict(title="Carhartt Flannel Shirt", description="Good shirt.",
                price=32.0, quantity=1, condition="USED_EXCELLENT",
                images=["a.jpg"], category_id="57990",
                item_specifics=specifics)
    base.update(kw)
    return Listing(**base)


def _size_type(listing) -> list[str]:
    return [s.value for s in listing.item_specifics
            if s.name.strip().lower() == "size type"]


# --- reading the size ---------------------------------------------------------

@pytest.mark.parametrize("size, multiple", [
    ("XXL", 2), ("2XL", 2), ("XX-Large", 2), ("XXLarge", 2), ("2X", 2),
    ("XXXL", 3), ("3XL", 3), ("XXX-Large", 3), ("3XLT", 3),
    ("4XL", 4), ("5XL", 5),
    # Pairs of spellings on one tag — the bigger one is the size.
    ("XXL/3XL", 3), ("2XL (XXL)", 2),
])
def test_a_size_past_xl_is_read_as_such(size, multiple):
    assert taxonomy.size_multiplier(size) == multiple
    assert taxonomy.is_big_and_tall_size(size)


@pytest.mark.parametrize("size", [
    "", "S", "M", "L", "XL", "X-Large", "XLT", "XS", "XXS",
    "1X", "Large", "One Size", "42R", "10.5",
    # An X that is not an extra-large: waist by inseam, a dimension, a word.
    "32 X 34", "36X30", "12 x 14 in", "MAXX", "2 x 3 ft",
])
def test_a_size_at_or_below_xl_is_left_alone(size):
    assert not taxonomy.is_big_and_tall_size(size)


# --- the rule -----------------------------------------------------------------

def test_a_2xl_shirt_gets_big_and_tall_instead_of_regular():
    listing = _listing([ItemSpecific(name="Size", value="2XL"),
                        ItemSpecific(name="Size Type", value="Regular",
                                     confidence="medium")])
    assert taxonomy.apply_big_and_tall(listing, MENS) == "Big & Tall"
    assert _size_type(listing) == ["Big & Tall"]


def test_a_2xl_shirt_with_no_size_type_at_all_gets_one():
    listing = _listing([ItemSpecific(name="Size", value="XXL")])
    assert taxonomy.apply_big_and_tall(listing, MENS) == "Big & Tall"
    assert _size_type(listing) == ["Big & Tall"]
    # One row per SINGLE aspect, and the category's own spelling of the name.
    assert [s.name for s in listing.item_specifics] == ["Size", "Size Type"]


def test_an_xl_shirt_keeps_its_regular_size_type():
    listing = _listing([ItemSpecific(name="Size", value="XL"),
                        ItemSpecific(name="Size Type", value="Regular")])
    assert taxonomy.apply_big_and_tall(listing, MENS) == ""
    assert _size_type(listing) == ["Regular"]


@pytest.mark.parametrize("answered", ["Big", "Tall", "Big & Tall"])
def test_an_answer_the_seller_already_gave_stands(answered):
    """"Big" and "Tall" are both honest answers on a 3XL. Only "Regular" —
    the one eBay refuses — and a blank get overwritten."""
    listing = _listing([ItemSpecific(name="Size", value="3XL"),
                        ItemSpecific(name="Size Type", value=answered)])
    assert taxonomy.apply_big_and_tall(listing, MENS) == ""
    assert _size_type(listing) == [answered]


def test_a_womens_xxl_is_not_made_big_and_tall():
    """The women's category offers Plus, not Big & Tall — and "Regular" there
    is legal. A rule that fired anyway would break the listings it touched."""
    listing = _listing([ItemSpecific(name="Size", value="XXL"),
                        ItemSpecific(name="Size Type", value="Regular")])
    assert taxonomy.apply_big_and_tall(listing, WOMENS) == ""
    assert _size_type(listing) == ["Regular"]


def test_a_category_without_a_size_type_aspect_is_untouched():
    listing = _listing([ItemSpecific(name="Size", value="XXL")])
    assert taxonomy.apply_big_and_tall(listing, [_aspect("Size")]) == ""
    assert _size_type(listing) == []


def test_the_categorys_own_spelling_is_what_gets_sent():
    """A fixed-choice aspect takes ONE of eBay's strings. Sending our own
    spelling of the same idea is how the value gets dropped."""
    spelled = [_aspect("Size"),
               _aspect("Size Type", values=["Regular", "Big and Tall"])]
    listing = _listing([ItemSpecific(name="Size", value="4XL")])
    assert taxonomy.apply_big_and_tall(listing, spelled) == "Big and Tall"


def test_a_category_offering_big_and_tall_only_separately_is_left_alone():
    """"Big" or "Tall"? There is no honest way to pick, so nothing is picked."""
    split = [_aspect("Size"),
             _aspect("Size Type", values=["Regular", "Big", "Tall"])]
    listing = _listing([ItemSpecific(name="Size", value="XXL"),
                        ItemSpecific(name="Size Type", value="Regular")])
    assert taxonomy.apply_big_and_tall(listing, split) == ""
    assert _size_type(listing) == ["Regular"]


def test_the_size_is_read_from_the_aspect_that_holds_it():
    """Categories name it "Men's Size" or "Shirt Size" as often as "Size"."""
    named = [_aspect("Men's Size"),
             _aspect("Size Type", values=["Regular", "Big & Tall"])]
    listing = _listing([ItemSpecific(name="Men's Size", value="XXL"),
                        ItemSpecific(name="Size Type", value="Regular")])
    assert taxonomy.apply_big_and_tall(listing, named) == "Big & Tall"
    assert _size_type(listing) == ["Big & Tall"]


def test_a_neck_size_does_not_stand_in_for_the_size():
    """A 17.5" neck is not an XXL. Nothing in the listing says XXL, so the
    rule has nothing to fire on."""
    listing = _listing([ItemSpecific(name="Size", value="L"),
                        ItemSpecific(name="Neck Size", value="17.5"),
                        ItemSpecific(name="Size Type", value="Regular")])
    assert taxonomy.apply_big_and_tall(listing, MENS) == ""
    assert _size_type(listing) == ["Regular"]


# --- where it runs ------------------------------------------------------------

def test_the_sanitize_pass_fixes_it_before_the_listing_is_sent(monkeypatch):
    """The last thing that touches a listing before eBay does. A seller who
    set "Regular" by hand in the editor still gets a listing, not error."""
    monkeypatch.setattr(taxonomy, "item_aspects",
                        lambda cid, marketplace_id=None: {"aspects": MENS})
    listing = _listing([ItemSpecific(name="Size", value="3XL"),
                        ItemSpecific(name="Size Type", value="Regular")])
    taxonomy.sanitize_specifics(listing)
    assert _size_type(listing) == ["Big & Tall"]


def test_the_draft_carries_the_answer_out_of_identify(monkeypatch):
    """So the seller reads it in the editor rather than meeting it as a
    silent rewrite at publish time."""
    # Inline, not at module scope: importing the app pulls in the vision
    # client, and the rest of this file is pure and runs in the lint+unit job
    # that deliberately doesn't install it.
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main
    monkeypatch.setattr(taxonomy, "item_aspects",
                        lambda cid, marketplace_id=None: {"aspects": MENS})
    listing = _listing([])
    added = main._merge_filled_specifics(
        listing,
        [ItemSpecific(name="Size", value="XXL", confidence="high"),
         ItemSpecific(name="Size Type", value="Regular", confidence="medium")],
        MENS)
    assert added == 2
    assert _size_type(listing) == ["Big & Tall"]


def test_a_lookup_that_fails_does_not_take_the_listing_with_it(monkeypatch):
    """No category, no Taxonomy API, no aspects — the listing passes through
    as it is. This rule is a convenience, never a blocker."""
    def boom(*_a, **_k):
        raise RuntimeError("taxonomy is down")
    monkeypatch.setattr(taxonomy, "item_aspects", boom)
    listing = _listing([ItemSpecific(name="Size", value="XXL"),
                        ItemSpecific(name="Size Type", value="Regular")])
    assert taxonomy.apply_big_and_tall(listing) == ""
    assert _size_type(listing) == ["Regular"]
    assert taxonomy.apply_big_and_tall(_listing([], category_id="")) == ""

"""eBay's Size for men's bottoms is the WAIST ALONE. Nothing else goes in it.

Two rejections, both after the photos had uploaded, both from a draft that
had read the item perfectly correctly:

    "W33 L34" is not a valid value for Size. Select a value from the
    available options.
    "Regular" is not a valid value for Size. Select a value from the
    available options.

The tag prints "W33 L34" and the vision pass reads it right. "Regular" is the
word on the label of nearly every pair of straight-leg jeans made. Neither is
wrong about the item — both are in the wrong box. eBay wants Size "33", the
inseam under Inseam, and the cut under Size Type or Fit.

So the Size field is put right before anything is sent, and what comes out of
it is put where it belongs rather than thrown away: the inseam is a real
measurement off the same tag, and the fit is a real answer to a different
question.

A third thing had to change for any of it to reach the value. Specifics were
only ever matched against eBay's list for an aspect eBay reported as
SELECTION_ONLY, and this Size came back FREE_TEXT and was then enforced at
publish anyway — eBay's publish validation does not always agree with the
mode its own Taxonomy API reports.
"""
from __future__ import annotations

import pytest

from backend.models import ItemSpecific, Listing
from backend.services import taxonomy


def _aspect(name, *, values=(), mode="SELECTION_ONLY", **kw):
    base = {"name": name, "required": False, "mode": mode,
            "values": list(values), "cardinality": "SINGLE",
            "data_type": "STRING", "format": "", "max_length": 0}
    base.update(kw)
    return base


# What eBay answers for a men's jeans category: Size is the waist, and the
# inseam and the cut are questions of their own.
JEANS = [
    _aspect("Size", values=["30", "31", "32", "33", "34", "36", "38"],
            required=True),
    _aspect("Inseam", values=["30", "32", "34", "36"]),
    _aspect("Size Type", values=["Regular", "Big & Tall", "Tall", "Big"]),
    _aspect("Fit", values=["Regular", "Slim", "Relaxed", "Bootcut"]),
]


@pytest.fixture()
def jeans(monkeypatch):
    monkeypatch.setattr(taxonomy, "item_aspects",
                        lambda cid, marketplace_id=None: {"aspects": JEANS})
    return JEANS


def _listing(size, **kw):
    specifics = [ItemSpecific(name="Size", value=size)] if size else []
    specifics += kw.pop("extra", [])
    return Listing(title="Levi's 501 Jeans", description="Good jeans.",
                   price=45.0, quantity=1, condition="USED_EXCELLENT",
                   images=["a.jpg"], category_id="11483",
                   item_specifics=specifics, **kw)


def _specifics(listing) -> dict:
    return {s.name: s.value for s in listing.item_specifics if s.value.strip()}


# --- reading the tag ---------------------------------------------------------

@pytest.mark.parametrize("printed", [
    "W33 L34", "W33L34", "w33 l34", "W33xL34",
    "33W 34L", "33W x 34L",
    "Waist 33 Inseam 34", "Waist 33 Length 34",
    "33x34", "33X34", "33 x 34", "33/34", "33 X 34 in", 'W33 L34"',
])
def test_every_way_a_tag_prints_it_is_the_same_pair(printed):
    assert taxonomy.size_pair(printed) == ("33", "34")


@pytest.mark.parametrize("value", [
    "50/50 Cotton Poly",   # a blend, not a 50x50 pair
    "60/40 Wool Nylon", "100% Cotton",
    "XXL", "Large", "10.5", "1980", "16.5",
])
def test_a_pair_inside_a_longer_value_is_not_a_size(value):
    """The patterns match the WHOLE value only. A loose pair in a longer
    string is a fibre blend or a year, and rewriting it would corrupt a
    Material that had nothing wrong with it."""
    assert taxonomy.size_pair(value) is None


def test_a_blend_still_normalizes_the_way_it_always_did():
    assert taxonomy._norm_value("50/50 Cotton Poly") == "5050cottonpoly"


# --- the waist is the size ---------------------------------------------------

@pytest.mark.parametrize("printed", [
    "W33 L34", "33x34", "Waist 33 Inseam 34", "33W 34L", "33/34",
])
def test_the_size_is_the_waist_and_the_inseam_gets_its_own_field(jeans, printed):
    listing = _listing(printed)
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Size": "33", "Inseam": "34"}


def test_a_waist_only_size_is_left_exactly_as_it_is(jeans):
    listing = _listing("33")
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Size": "33"}


def test_the_inseam_the_seller_already_gave_is_not_overwritten(jeans):
    listing = _listing("W33 L34",
                       extra=[ItemSpecific(name="Inseam", value="32")])
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Size": "33", "Inseam": "32"}


def test_a_category_with_no_inseam_aspect_still_gets_the_waist_right():
    """Losing the inseam is the right trade when eBay doesn't ask for it —
    what must not happen is the whole listing being refused over it."""
    only_size = [_aspect("Size", values=["32", "33", "34"])]
    listing = _listing("W33 L34")
    taxonomy.fix_size_specifics(listing, only_size)
    assert _specifics(listing) == {"Size": "33"}


# --- a fit word is not a size ------------------------------------------------

def test_regular_is_a_size_type_not_a_size(jeans):
    """The rejection the seller hit over and over."""
    listing = _listing("Regular")
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Size Type": "Regular"}


def test_a_fit_word_goes_to_the_aspect_that_actually_takes_it(jeans):
    """"Slim" is not one of Size Type's values but is one of Fit's. Stopping
    at the first aspect the category happens to offer threw it away."""
    listing = _listing("Slim")
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Fit": "Slim"}

    listing = _listing("Bootcut")
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Fit": "Bootcut"}


def test_a_fit_word_never_overwrites_an_answer_already_there(jeans):
    """Size Type is already answered, so "Regular" moves on to the next
    aspect that takes it rather than overwriting the seller. A Big & Tall
    garment with a regular cut is a real pair of jeans, not a contradiction."""
    listing = _listing("Regular",
                       extra=[ItemSpecific(name="Size Type", value="Big & Tall")])
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Size Type": "Big & Tall", "Fit": "Regular"}


def test_a_fit_word_with_every_home_taken_is_simply_dropped(jeans):
    """Nowhere honest to put it, so it goes nowhere — and Size is still
    cleared, because leaving it there is the rejection this exists to stop."""
    listing = _listing("Regular", extra=[
        ItemSpecific(name="Size Type", value="Big & Tall"),
        ItemSpecific(name="Fit", value="Slim"),
    ])
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Size Type": "Big & Tall", "Fit": "Slim"}


def test_a_fit_word_with_nowhere_to_go_still_leaves_size(jeans):
    """Better a blank Size the checklist asks about than a value eBay refuses:
    one is a question, the other is a rejected listing."""
    listing = _listing("Regular")
    taxonomy.fix_size_specifics(listing, [_aspect("Size", values=["32", "33"])])
    assert "Size" not in _specifics(listing)


@pytest.mark.parametrize("word, is_fit", [
    ("Regular", True), ("Slim", True), ("Bootcut", True), ("Relaxed", True),
    ("Big & Tall", True), ("Husky", True), ("One Size", True),
    ("33", False), ("W33 L34", False), ("XXL", False), ("34x30", False),
])
def test_a_measurement_is_never_mistaken_for_a_fit(word, is_fit):
    assert taxonomy.is_fit_word(word) is is_fit


# --- the aspect mode eBay reports is not the one it enforces -----------------

def test_a_free_text_size_is_still_matched_against_its_own_list():
    """The third half of the bug: this Size came back FREE_TEXT and was
    enforced at publish anyway, so the value was never compared at all."""
    free = _aspect("Size", values=["32", "33", "34"], mode="FREE_TEXT")
    assert taxonomy.coerce_aspect_value("33", free) == "33"
    listing = _listing("W33 L34")
    taxonomy.fix_size_specifics(listing, [free])
    assert _specifics(listing)["Size"] == "33"


def test_a_free_text_value_that_matches_nothing_is_kept():
    """A free-text list is a set of SUGGESTIONS. A brand eBay hasn't heard of
    is still legal, and dropping it would be a worse bug than the one fixed."""
    brand = _aspect("Brand", values=["Levi's", "Wrangler"], mode="FREE_TEXT")
    assert taxonomy.coerce_aspect_value("Kirkland Signature", brand) \
        == "Kirkland Signature"


def test_a_free_text_value_is_never_traded_for_a_shorter_suggestion():
    """Containment is right for a fixed-choice aspect, where the alternative
    is dropping the value. On free text it is pure loss: the suggestion says
    "Nike", the seller wrote "Nike Air", and what they wrote must survive."""
    free = _aspect("Brand", values=["Nike", "Adidas"], mode="FREE_TEXT")
    assert taxonomy.coerce_aspect_value("Nike Air", free) == "Nike Air"
    fixed = _aspect("Brand", values=["Nike", "Adidas"])
    assert taxonomy.coerce_aspect_value("Nike Air", fixed) == "Nike"


def test_a_fixed_choice_value_that_matches_nothing_is_still_dropped():
    fixed = _aspect("Size", values=["32", "33", "34"])
    assert taxonomy.coerce_aspect_value("W31 L31", fixed) is None


# --- it can never be the thing that breaks a listing -------------------------

def test_a_lookup_that_fails_leaves_the_listing_alone(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("taxonomy is down")
    monkeypatch.setattr(taxonomy, "item_aspects", boom)
    listing = _listing("W33 L34")
    assert taxonomy.fix_size_specifics(listing) == []
    assert _specifics(listing) == {"Size": "W33 L34"}


def test_a_category_with_no_size_aspect_is_untouched():
    listing = _listing("W33 L34")
    assert taxonomy.fix_size_specifics(listing, [_aspect("Brand")]) == []
    assert _specifics(listing) == {"Size": "W33 L34"}

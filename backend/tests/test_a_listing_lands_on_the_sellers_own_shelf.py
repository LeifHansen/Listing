"""A listing is filed on the seller's own store shelf, not just in eBay's tree.

Asked for as: "in addition to ebay category, assign proper store category (if
present in user's ebay account)."

Two different questions with two different answers. eBay's category says what
the item IS and decides which fields eBay demands; a STORE category is a shelf
in the seller's own storefront nav ("Vintage Tees"), invented by them, numbered
per account, and browsed by returning buyers. Everything this app published
before landed at the top level of the store, because nothing ever sent one.

No API suggests a store category — nobody but the seller knows what their
shelves mean — so the match is made from the draft's own words, and made
conservatively: a shelf has to earn it, a catch-all shelf can never be matched
into, and no confident answer means no answer at all. A listing left unfiled is
one dropdown away from filed; a wrongly filed one is invisible on the shelf it
should have been on.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("pydantic")

from backend.models import ItemSpecific, Listing        # noqa: E402
from backend.services import ebay_trading, store_category  # noqa: E402

NS = "urn:ebay:apis:eBLBaseComponents"

# One seller's storefront, as GetStore reports it: a nested tree of their own
# invention, with the catch-all shelf every store ends up with.
SHELVES = [
    {"id": "11", "name": "Clothing", "path": "Clothing", "level": 1},
    {"id": "12", "name": "Vintage Tees", "path": "Clothing > Vintage Tees",
     "level": 2},
    {"id": "13", "name": "Denim", "path": "Clothing > Denim", "level": 2},
    {"id": "21", "name": "Beanie Babies", "path": "Toys > Beanie Babies",
     "level": 2},
    {"id": "99", "name": "Other Items", "path": "Other Items", "level": 1},
]


def _draft(**kw) -> Listing:
    base = dict(title="Vintage 1994 single stitch tee", brand="Harley-Davidson",
                category_suggestion="Clothing, Shoes & Accessories > Men > "
                                    "Men's Clothing > T-Shirts",
                category_id="15687", price=24.99)
    base.update(kw)
    return Listing(**base)


# ------------------------------------------------------------- the match

def test_the_draft_lands_on_the_shelf_it_belongs_on():
    hit = store_category.match(_draft(), SHELVES)
    assert hit and (hit["id"], hit["name"]) == ("12", "Vintage Tees")


def test_a_shelf_is_matched_through_the_plural_it_is_named_in():
    """Shelves are named in the plural ("Tees", "Beanie Babies") and item
    titles almost never are. Comparing the words as typed scores zero on the
    most ordinary match there is."""
    hit = store_category.match(
        _draft(title="Ty Beanie Baby Princess the Bear 1997", brand="Ty",
               category_suggestion="Toys & Hobbies > Beanbag Plush > Ty"),
        SHELVES)
    assert hit and hit["name"] == "Beanie Babies"


def test_the_more_specific_shelf_wins_over_the_one_above_it():
    """A store with "Clothing" and "Clothing > Denim" has already said which
    it wants a pair of jeans on."""
    hit = store_category.match(
        _draft(title="Levi's 501 selvedge denim jeans W32 L34", brand="Levi's",
               category_suggestion="Clothing, Shoes & Accessories > Men > "
                                   "Men's Clothing > Jeans"),
        SHELVES)
    assert hit and hit["name"] == "Denim"


def test_nothing_is_ever_filed_under_the_catch_all():
    """"Other Items" describes nothing, so every listing matches it as well as
    every other — and a store where everything lands in Other is a store with
    no shelves at all."""
    hit = store_category.match(
        _draft(title="Assorted other items lot", brand="",
               category_suggestion="Everything Else > Other"),
        SHELVES)
    assert hit is None


def test_a_listing_with_no_shelf_for_it_is_left_alone():
    hit = store_category.match(
        _draft(title="Pyrex Cinderella mixing bowl 441", brand="Pyrex",
               category_suggestion="Home & Garden > Kitchen, Dining & Bar > "
                                   "Bowls"),
        SHELVES)
    assert hit is None


def test_a_seller_with_no_shelves_gets_no_answer_rather_than_a_crash():
    assert store_category.match(_draft(), []) is None
    assert store_category.match(Listing(title=""), SHELVES) is None


def test_an_item_specific_reinforces_a_match_but_never_makes_one():
    """"Material: Denim" on a listing whose own words never say denim is a
    fact about the item, not a statement about where the seller shelves it.
    The same word in the title is."""
    specifics = [ItemSpecific(name="Material", value="Denim"),
                 ItemSpecific(name="Style", value="Trucker")]
    assert store_category.match(
        _draft(title="Faded blue jacket", brand="", category_suggestion="",
               item_specifics=specifics), SHELVES) is None
    hit = store_category.match(
        _draft(title="Faded blue denim jacket", brand="",
               category_suggestion="", item_specifics=specifics), SHELVES)
    assert hit and hit["name"] == "Denim"


# ------------------------------------------------- what eBay is actually sent

def test_the_publish_carries_the_shelf():
    _call, body = ebay_trading.build_add_item(
        _draft(store_category_id="12", store_category_name="Vintage Tees"),
        [], postal_code="97214")
    root = ET.fromstring(f"<r xmlns='{NS}'>{body}</r>")
    assert root.findtext(f".//{{{NS}}}Storefront/{{{NS}}}StoreCategoryID") == "12"


def test_a_listing_with_no_shelf_sends_no_storefront_at_all():
    """An empty <StoreCategoryID> is not "leave it where it is" — on a revise
    eBay reads it as a request to move the listing back to the top level."""
    _call, body = ebay_trading.build_add_item(_draft(), [], postal_code="97214")
    assert "Storefront" not in body


def test_a_revise_carries_the_shelf_only_when_the_seller_moved_it():
    listing = _draft(store_category_id="12", store_category_name="Vintage Tees",
                     ebay_listing_id="110000000001", source="ebay")
    listing.mark_dirty("title")
    _call, quiet = ebay_trading.build_revise_item(listing, "110000000001")
    assert "Storefront" not in quiet

    listing.mark_dirty("store_category_id")
    _call, moved = ebay_trading.build_revise_item(listing, "110000000001")
    assert "<StoreCategoryID>12</StoreCategoryID>" in moved


# ---------------------------------------------------- what eBay reports back

def _item(storefront: str) -> ET.Element:
    return ET.fromstring(
        f"<Item xmlns='{NS}'><ItemID>110000000001</ItemID>"
        "<Title>A jacket</Title>"
        "<PrimaryCategory><CategoryID>15687</CategoryID></PrimaryCategory>"
        f"{storefront}</Item>")


def test_the_shelf_comes_back_from_ebay():
    got = ebay_trading._item_to_listing(_item(
        "<Storefront><StoreCategoryID>12</StoreCategoryID>"
        "<StoreCategoryName>Vintage Tees</StoreCategoryName></Storefront>"))
    assert got["store_category_id"] == "12"
    assert got["store_category_name"] == "Vintage Tees"


def test_ebays_nought_means_no_shelf_not_shelf_nought():
    """eBay writes "no store category" as 0. Read as an id it becomes a shelf
    the store does not have — shown in the editor, and sent back on the next
    revise."""
    assert ebay_trading._item_to_listing(
        _item("<Storefront><StoreCategoryID>0</StoreCategoryID></Storefront>")
    )["store_category_id"] == ""
    assert ebay_trading._item_to_listing(_item(""))["store_category_id"] == ""


# -------------------------------------------------------- reading the store

def _get_store(xml: str, monkeypatch) -> list[dict]:
    root = ET.fromstring(f"<GetStoreResponse xmlns='{NS}'>"
                         f"<Ack>Success</Ack>{xml}</GetStoreResponse>")
    monkeypatch.setattr(ebay_trading, "_call", lambda *a, **k: root)
    return ebay_trading.store_categories("tok")


def test_a_child_shelf_keeps_the_path_its_parent_gives_it(monkeypatch):
    cats = _get_store(
        "<Store><CustomCategories>"
        "<CustomCategory><CategoryID>11</CategoryID><Name>Clothing</Name>"
        "<ChildCategory><CategoryID>12</CategoryID>"
        "<Name>Vintage Tees</Name></ChildCategory>"
        "</CustomCategory>"
        "<CustomCategory><CategoryID>21</CategoryID><Name>Toys</Name>"
        "</CustomCategory>"
        "</CustomCategories></Store>", monkeypatch)
    assert [(c["id"], c["path"], c["level"]) for c in cats] == [
        ("11", "Clothing", 1),
        ("12", "Clothing > Vintage Tees", 2),
        ("21", "Toys", 1),
    ]


def test_a_seller_without_a_store_is_answered_not_failed(monkeypatch):
    """Most sellers have no eBay Store. That is the feature having nothing to
    offer them, not an error to put in front of them — and it is what stops
    the draft path asking eBay again for every photo in a 50-item batch."""
    def _refuse(*a, **k):
        raise ebay_trading.TradingError(
            "The specified user does not have a Store subscription.",
            code="21916564")
    monkeypatch.setattr(ebay_trading, "_call", _refuse)
    with pytest.raises(ebay_trading.NoStore):
        ebay_trading.store_categories("tok")


def test_a_lookup_that_actually_broke_still_breaks(monkeypatch):
    """"No store" is recognised by eBay's wording, so everything else has to
    keep raising — a rate limit reported as "no store" would quietly stop
    filing every listing for the rest of the day."""
    def _refuse(*a, **k):
        raise ebay_trading.TradingError("Auth token is invalid.", code="931")
    monkeypatch.setattr(ebay_trading, "_call", _refuse)
    with pytest.raises(ebay_trading.TradingError):
        ebay_trading.store_categories("tok")

"""A publish eBay's rulebook could not be read for is still corrected.

Reported twice in a row, in the seller's own words:

    "ebay refusing legitimate inseam sizes for mens jeans. works after re
    submitting same value."
    "same issue with item color."

"Works after re-submitting the same value" is the whole diagnosis. The values
were never wrong; what changed between the two attempts was on our side.
sanitize_specifics is the last pass before eBay -- it is what turns a tag's
"W33 L34" into the Size eBay lists, an inch mark into a number, and a colour
into eBay's own spelling of it -- and it opened with:

    try:
        aspects = item_aspects(listing.category_id)["aspects"]
    except Exception:
        return                      # every value goes out exactly as typed

So one slow or refused Taxonomy call (it runs on an application-wide
allowance shared by every seller of this app) sent the seller's specifics
against no rules at all, and eBay refused the listing AFTER the photos had
uploaded. The seller pressed publish again, the lookup answered that time,
and the identical values sailed through.

Two things now stand between them and that. The list eBay last gave us for
the category is kept with no expiry and used when the live call won't answer
-- rules an hour old beat no rules. And where there has never been one, the
corrections that never needed eBay still run: a tag's waist-by-inseam is a
waist and an inseam whatever any list says.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.models import ItemSpecific, Listing        # noqa: E402
from backend.services import taxonomy                   # noqa: E402


def _aspect(name, *, values=(), mode="SELECTION_ONLY", required=False):
    return {"name": name, "required": required, "mode": mode,
            "values": list(values), "cardinality": "SINGLE",
            "data_type": "STRING", "format": "", "max_length": 0,
            "pairs_with": {}}


# What eBay answers for men's jeans: the Size is the waist, the inseam is its
# own question, and Color is a fixed list that a tag's wording is not on.
JEANS = [
    _aspect("Size", values=["30", "32", "33", "34", "36"], required=True),
    _aspect("Inseam", values=["30", "32", "34", "36"]),
    _aspect("Color", values=["Blue", "Black", "Grey", "White"], required=True),
]

CATEGORY = "11483"


def _jeans_listing():
    """The draft as the tag reads and the AI writes it — all three values
    correct about the item, none of them in eBay's spelling."""
    return Listing(
        title="Levi's 501 Original Fit Jeans", description="Good jeans.",
        price=45.0, quantity=1, condition="USED_EXCELLENT", images=["a.jpg"],
        category_id=CATEGORY,
        item_specifics=[
            ItemSpecific(name="Size", value="W33 L34"),
            ItemSpecific(name="Inseam", value='34"'),
            ItemSpecific(name="Color", value="Dark Blue Wash"),
        ])


def _specifics(listing) -> dict:
    return {s.name: s.value for s in listing.item_specifics if s.value.strip()}


@pytest.fixture(autouse=True)
def _no_remembered_list(monkeypatch):
    """Each test says for itself what has been read before."""
    monkeypatch.setattr(taxonomy, "_ASPECTS_LAST_GOOD", {})


def _lookup_fails(monkeypatch):
    def _boom(cid, marketplace_id=None):
        raise RuntimeError("eBay Taxonomy: 429 Too Many Requests")
    monkeypatch.setattr(taxonomy, "item_aspects", _boom)


def _lookup_works(monkeypatch, aspects=None):
    monkeypatch.setattr(taxonomy, "item_aspects",
                        lambda cid, marketplace_id=None:
                        {"aspects": list(aspects if aspects is not None else JEANS)})


# ------------------------------------------------- the list we already have

def test_the_list_last_read_stands_in_when_ebay_will_not_answer(monkeypatch):
    """The report, from both messages at once: the size, the inseam and the
    colour all reach eBay in eBay's own spelling, on the FIRST attempt."""
    taxonomy._ASPECTS_LAST_GOOD[f"{CATEGORY}|"] = {"aspects": JEANS}
    _lookup_fails(monkeypatch)
    listing = _jeans_listing()

    taxonomy.sanitize_specifics(listing)

    assert _specifics(listing) == {"Size": "33", "Inseam": "34", "Color": "Blue"}


def test_a_successful_lookup_is_still_what_wins(monkeypatch):
    """The fallback is a fallback. A live answer -- even one that disagrees
    with what we remember -- is the rulebook."""
    taxonomy._ASPECTS_LAST_GOOD[f"{CATEGORY}|"] = {"aspects": [
        _aspect("Color", values=["Indigo"])]}
    _lookup_works(monkeypatch)
    listing = _jeans_listing()

    taxonomy.sanitize_specifics(listing)

    assert _specifics(listing)["Color"] == "Blue"


def test_reading_a_list_is_what_remembers_it(monkeypatch):
    """Nothing else fills the fallback, so it has to be the read itself."""
    monkeypatch.setattr(taxonomy, "_ASPECTS_CACHE", {})
    monkeypatch.setattr(taxonomy, "default_tree_id", lambda m=None: "0")
    monkeypatch.setattr(taxonomy, "_headers", lambda: {})

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"aspects": [{
                "localizedAspectName": "Inseam",
                "aspectConstraint": {"aspectMode": "SELECTION_ONLY"},
                "aspectValues": [{"localizedValue": "34"}]}]}

    monkeypatch.setattr(taxonomy.httpx, "get", lambda *a, **k: _Resp())
    taxonomy.item_aspects(CATEGORY)

    assert [a["name"] for a in taxonomy.last_known_aspects(CATEGORY)] == ["Inseam"]


# ------------------------------------------- and where there is no list at all

def test_the_tag_is_still_read_when_nothing_can_be(monkeypatch):
    """A first publish in a category nobody has ever read the aspects for.
    The waist-by-inseam split and the inch mark need no list -- they are facts
    about how a tag is printed, and eBay refuses both spellings."""
    _lookup_fails(monkeypatch)
    listing = _jeans_listing()

    taxonomy.sanitize_specifics(listing)

    got = _specifics(listing)
    assert (got["Size"], got["Inseam"]) == ("33", "34")
    # The colour cannot be mapped without eBay's list, and is not guessed at:
    # it goes as the seller wrote it, which is the best anyone can do here.
    assert got["Color"] == "Dark Blue Wash"


def test_an_answer_with_no_aspects_in_it_is_the_same_silence(monkeypatch):
    """eBay answering with an empty list is not a rulebook either."""
    _lookup_works(monkeypatch, aspects=[])
    listing = _jeans_listing()

    taxonomy.sanitize_specifics(listing)

    assert _specifics(listing)["Size"] == "33"
    assert _specifics(listing)["Inseam"] == "34"


def test_an_inseam_typed_with_its_unit_loses_the_unit(monkeypatch):
    """The seller's own inseam, in the shape a person writes one."""
    _lookup_fails(monkeypatch)
    for typed in ('34"', "34 in", "34 inches", "34in."):
        listing = Listing(title="Jeans", price=45.0, quantity=1, images=["a.jpg"],
                          category_id=CATEGORY, condition="USED_EXCELLENT",
                          item_specifics=[ItemSpecific(name="Inseam", value=typed)])
        taxonomy.sanitize_specifics(listing)
        assert _specifics(listing) == {"Inseam": "34"}, typed


def test_a_listing_with_nothing_to_fix_is_left_alone(monkeypatch):
    _lookup_fails(monkeypatch)
    listing = Listing(title="Jeans", price=45.0, quantity=1, images=["a.jpg"],
                      category_id=CATEGORY, condition="USED_EXCELLENT",
                      item_specifics=[ItemSpecific(name="Brand", value="Levi's"),
                                      ItemSpecific(name="Size", value="33")])
    taxonomy.sanitize_specifics(listing)
    assert _specifics(listing) == {"Brand": "Levi's", "Size": "33"}

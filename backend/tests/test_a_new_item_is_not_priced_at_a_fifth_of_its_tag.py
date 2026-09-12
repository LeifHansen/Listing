"""The floor under a still-new item, and the brand its own tag disagrees with.

The Scotch & Soda report was two failures wearing one coat. The condition half
lives in test_the_tag_still_on_it_is_not_a_used_item.py; this is the other two:

  * $130 on the tag, $49 in the draft. `_price_against_comps` could not catch it
    -- it asks eBay for comparable listings and only ever raises a price it
    finds an order of magnitude under them, and with the condition misgraded it
    was asking about USED shirts, which duly agreed. The evidence that would
    have settled it was printed on the item and in the photo the whole time.
  * The brand. The identify pass reads a hang tag at whole-photo resolution,
    where a brand is a smudge it half-recognises, and once it wrote ANYTHING
    into `brand` nothing downstream looked again: the maker hunt ran only on a
    blank. So a wrong brand was permanent -- in the field eBay's search weights
    most heavily -- while a zoomed, readable crop of that same tag was already
    being passed to the very next call for something else.

Both fixes go one way only. The floor never lowers a price and never touches a
used item; the brand is only overruled by the adversarial verifier confirming
at HIGH confidence, and only when the two names are actually different.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend.models import ItemSpecific, Listing  # noqa: E402


def _shirt(**fields) -> Listing:
    base = {"title": "Scotch & Soda Amsterdam Regular Fit Oxford Shirt Mens L",
            "brand": "Scotch & Soda", "condition": "NEW", "price": 49.0,
            "retail_price": 130.0, "images": ["img_000.jpg"]}
    base.update(fields)
    return Listing(**base)


# ------------------------------------------------------------- the floor

def test_the_tagged_shirt_that_started_this():
    """$130 on the tag, drafted at $49 — 38% of retail for something nobody
    has worn. The number moves, and the seller is told it moved."""
    from backend import main

    listing = _shirt()
    got = main._price_against_retail(listing)

    assert got is not None
    assert listing.price > 49.0
    assert listing.price == main.charm_price(130.0 * main.RETAIL_FLOOR_RATIO)
    note = " ".join(listing.missing_info).lower()
    assert "130" in note and "confirm the price" in note


def test_a_new_item_with_no_price_at_all_gets_the_floor():
    """The prompt tells the model to return null rather than guess when it
    cannot judge the market. A tag is still evidence."""
    from backend import main

    listing = _shirt(price=None)
    assert main._price_against_retail(listing) is not None
    assert listing.price > 0
    assert "wouldn't put a number on it" in " ".join(listing.missing_info)


def test_a_price_already_above_the_floor_is_left_exactly_alone():
    """The floor is a "that cannot be right" line, not a second opinion on a
    keen price. A seller pricing to move is not overruled."""
    from backend import main

    listing = _shirt(price=59.99)
    assert main._price_against_retail(listing) is None
    assert listing.price == 59.99
    assert listing.missing_info == []


def test_it_never_lowers_a_price():
    """The same rule the comp check follows, for the same reason: a high
    number is the seller's to reduce, a low one sells within the hour."""
    from backend import main

    listing = _shirt(price=125.0)
    assert main._price_against_retail(listing) is None
    assert listing.price == 125.0


@pytest.mark.parametrize("condition", [
    "USED_EXCELLENT", "USED_GOOD", "PRE_OWNED_EXCELLENT", "PRE_OWNED_FAIR",
    "LIKE_NEW", "FOR_PARTS_OR_NOT_WORKING",
])
def test_a_used_item_is_not_held_up_by_a_tag_it_no_longer_has(condition):
    """A worn shirt really does sell for a fifth of what it retailed at. The
    tag is evidence about a NEW item and about nothing else — which is also
    what stops this becoming a machine for inflating every listing."""
    from backend import main

    listing = _shirt(condition=condition, price=19.99)
    assert main._price_against_retail(listing) is None
    assert listing.price == 19.99


def test_the_ways_of_saying_new_all_get_the_floor():
    from backend import main

    for condition in ("NEW", "NEW_OTHER", "NEW_WITH_DEFECTS"):
        listing = _shirt(condition=condition, price=20.0)
        assert main._price_against_retail(listing) is not None, condition


@pytest.mark.parametrize("retail", [None, 0, -5])
def test_no_tag_means_no_opinion(retail):
    """Most items have no readable retail price, and this must be silent for
    every one of them rather than inventing a floor out of nothing."""
    from backend import main

    listing = _shirt(retail_price=retail, price=12.0)
    assert main._price_against_retail(listing) is None
    assert listing.price == 12.0
    assert listing.missing_info == []


def test_it_runs_without_ebay_credentials():
    """The half of the fix the comp check cannot do. `_price_against_comps`
    returns immediately when there is no taxonomy configured; the tag is in
    the photo either way."""
    from backend import main

    listing = _shirt()
    assert main._price_against_comps(listing) is None   # nothing configured
    assert listing.price == 49.0
    assert main._price_against_retail(listing) is not None
    assert listing.price > 49.0


# --------------------------------------------- the market has the last word

def test_the_market_caps_what_the_tag_can_argue_for():
    """The guard that keeps this from becoming a machine for inflating
    listings. A $130 tag on something comparable listings ask $30 for does not
    make it a $58 item — real listings for the real item beat arithmetic on an
    MSRP, and the tag only gets to say the draft was under them."""
    from backend import main

    listing = _shirt(price=12.0)
    got = main._price_against_retail(listing, {"price": 30.0, "high": 32.0})

    assert got is not None
    assert listing.price == main.charm_price(32.0)   # the market's ceiling
    assert listing.price < 130.0 * main.RETAIL_FLOOR_RATIO


def test_a_draft_already_above_the_capped_floor_stands():
    from backend import main

    listing = _shirt(price=35.0)
    assert main._price_against_retail(listing, {"price": 30.0, "high": 32.0}) is None
    assert listing.price == 35.0


def test_a_generous_market_does_not_raise_the_floor_above_the_tag_fraction():
    """The cap only ever lowers the floor. When comparable listings ask more
    than the tag fraction, this still stops at the tag fraction and leaves the
    rest to the comp check, which has already had its say."""
    from backend import main

    listing = _shirt(price=20.0)
    main._price_against_retail(listing, {"price": 200.0, "high": 400.0})

    assert listing.price == main.charm_price(130.0 * main.RETAIL_FLOOR_RATIO)


@pytest.mark.parametrize("market", [None, {}, {"price": 0}, {"high": None},
                                    {"high": "n/a"}])
def test_a_market_that_said_nothing_does_not_cap_anything(market):
    """Comps are silent far more often than not — no credentials, no
    comparable listings, a keyword query that matched nothing. The tag is then
    the only evidence there is, and a missing answer must not read as zero."""
    from backend import main

    listing = _shirt(price=20.0)
    main._price_against_retail(listing, market)

    assert listing.price == main.charm_price(130.0 * main.RETAIL_FLOOR_RATIO)


def test_the_comp_check_reports_what_the_market_said_even_when_it_changed_nothing(
        monkeypatch):
    """The plumbing the cap depends on. `_price_against_comps` returns None
    both when it found nothing and when the draft's own number stood — two
    very different facts — so the market answer rides out in `market_out`
    instead."""
    from backend import main

    monkeypatch.setattr(main, "DRAFT_PRICE_COMPS", True)
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.pricing, "suggest", lambda *a, **k: {
        "suggestion": {"price": 44.99, "low": 40.0, "high": 60.0, "count": 9,
                       "basis": "Live asking prices on eBay", "sold_data": False}})

    listing = _shirt(price=49.0)
    market: dict = {}
    changed = main._price_against_comps(listing, uid="u1", market_out=market)

    assert changed is None            # $49 is not an order of magnitude under
    assert listing.price == 49.0      # ...so the draft's number stood
    assert market["high"] == 60.0     # ...and the market still got reported


# ------------------------------------------------------------- the brand

@pytest.mark.parametrize("a,b", [
    ("Scotch & Soda", "Scotch and Soda"),
    ("Scotch & Soda", "SCOTCH & SODA AMSTERDAM"),
    ("Scotch & Soda Amsterdam Couture", "Scotch & Soda"),
    ("Levi's", "Levis"),
    ("Patagonia", "Patagonia, Inc."),
    ("The North Face", "North Face"),
])
def test_the_same_brand_written_differently_is_not_a_disagreement(a, b):
    """A verify call and a rewritten brand are for a genuinely different
    maker. Punctuation, "and", a legal suffix and the city a brand prints on
    its own tag are not that."""
    from backend import main

    assert main._same_maker(a, b), f"{a!r} vs {b!r}"


@pytest.mark.parametrize("a,b", [
    ("Scotch & Soda", "Tommy Hilfiger"),
    ("Levi's", "Wrangler"),
    ("Pyrex", "Fire King"),
    ("Scotch & Soda", ""),
])
def test_a_different_maker_is_a_disagreement(a, b):
    from backend import main

    assert not main._same_maker(a, b), f"{a!r} vs {b!r}"


def test_a_corrected_brand_is_carried_into_the_title():
    """Fixing `brand` alone leaves the draft contradicting itself where eBay's
    search actually reads it — the first words of the title."""
    from backend import main

    listing = _shirt(brand="Scotch & Soda",
                     title="Scotch & Soda Oxford Shirt Mens L Blue NWT")
    main._retitle_for_brand(listing, "Ted Baker")

    assert listing.title == "Ted Baker Oxford Shirt Mens L Blue NWT"


def test_a_title_that_never_named_the_old_brand_is_not_rebuilt():
    """The function edits where the old brand literally appears and nowhere
    else. Prepending to a title it cannot parse would invent word order and
    could leave two brands in one listing."""
    from backend import main

    listing = _shirt(brand="Scotch & Soda", title="Mens Blue Oxford Shirt L")
    main._retitle_for_brand(listing, "Ted Baker")

    assert listing.title == "Mens Blue Oxford Shirt L"
    assert any("ted baker" in m.lower() for m in listing.missing_info)


def test_a_corrected_brand_reaches_the_brand_specific_too():
    """A Brand item specific still holding the old name keeps filtering the
    listing under it."""
    from backend import main

    listing = _shirt(brand="Scotch & Soda",
                     title="Scotch & Soda Oxford Shirt",
                     item_specifics=[
                         ItemSpecific(name="Brand", value="Scotch & Soda",
                                      confidence="medium"),
                         ItemSpecific(name="Color", value="Blue",
                                      confidence="high")])
    main._retitle_for_brand(listing, "Ted Baker")

    by_name = {s.name: s.value for s in listing.item_specifics}
    assert by_name["Brand"] == "Ted Baker"
    assert by_name["Color"] == "Blue"


# ------------------------------- ...and the same thing through the real chain
#
# The unit tests above call `_retitle_for_brand` directly, which is exactly how
# the bug they did not catch got in: at the call site the brand was reassigned
# BEFORE the retitle, so the function looked for a brand the title no longer
# held and fell through to its "cannot edit this" note every time. Only the
# chain can see that, so the chain is what these drive.


def _chain(monkeypatch, listing, maker, verdict_confidence, tags=(("x",),)):
    """Run `_enrich_listing_v2` with the two vision calls stubbed."""
    from backend import main

    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main.taxonomy, "item_aspects",
                        lambda cid, **k: {"aspects": [{"name": "Color"}]})
    monkeypatch.setattr(main.claude_ai, "tag_crops",
                        lambda paths, t: [{"type": "image"}] if tags else [])
    monkeypatch.setattr(main.claude_ai, "fill_aspects_combined",
                        lambda *a, **k: ([], {"maker": maker,
                                              "evidence": "the neck label",
                                              "confidence": "high"}))
    monkeypatch.setattr(
        main.claude_ai, "verify_maker",
        lambda *a, **k: ({"maker": maker, "evidence": "the neck label",
                          "confidence": verdict_confidence}
                         if verdict_confidence else None))
    monkeypatch.setattr(main, "_cover_remaining_specifics", lambda *a, **k: 0)
    monkeypatch.setattr(main, "_pair_aspects", lambda *a, **k: None)

    class _Path:
        @staticmethod
        def is_file():
            return True

    main._enrich_listing_v2(listing, [_Path()], list(tags))


def test_the_chain_carries_a_corrected_brand_into_the_title(monkeypatch):
    """The ordering bug, held down. Correcting `brand` and leaving the title
    leading with the old one is a listing that contradicts itself in the two
    places eBay's search reads."""
    listing = _shirt(brand="Zara", title="Zara Mens Oxford Shirt L Blue NWT",
                     category_id="155226")

    _chain(monkeypatch, listing, "Scotch & Soda", "high")

    assert listing.brand == "Scotch & Soda"
    assert listing.title == "Scotch & Soda Mens Oxford Shirt L Blue NWT"


def test_a_medium_verdict_does_not_overrule_a_brand_that_is_already_there(
        monkeypatch):
    """Filling a blank takes medium; overwriting an answer takes high. And the
    nag stays: `_apply_maker` ends by dropping "confirm the brand" notes, so
    reaching it on a medium dispute would silence the one thing telling the
    seller to look."""
    listing = _shirt(brand="Zara", title="Zara Mens Oxford Shirt L Blue NWT",
                     category_id="155226",
                     missing_info=["Confirm the brand."])

    _chain(monkeypatch, listing, "Scotch & Soda", "medium")

    assert listing.brand == "Zara"
    assert listing.title == "Zara Mens Oxford Shirt L Blue NWT"
    assert listing.missing_info == ["Confirm the brand."]


def test_a_tag_that_agrees_with_the_draft_costs_no_verify_call(monkeypatch):
    """The maker is now asked for on every item with tag crops, so the gate on
    the SECOND call has to be the disagreement — otherwise every listing pays
    for an extra vision round trip to be told what it already knew."""
    from backend import main

    calls = []
    listing = _shirt(brand="Scotch & Soda", category_id="155226")
    monkeypatch.setattr(main.claude_ai, "verify_maker",
                        lambda *a, **k: calls.append(1))
    _chain(monkeypatch, listing, "SCOTCH & SODA AMSTERDAM", "high")

    assert calls == []
    assert listing.brand == "Scotch & Soda"


def test_a_blank_brand_is_still_filled_at_medium(monkeypatch):
    """The behaviour that already existed and must not regress."""
    listing = _shirt(brand="", title="Mens Oxford Shirt L Blue NWT",
                     category_id="155226")

    _chain(monkeypatch, listing, "Scotch & Soda", "medium")

    assert listing.brand == "Scotch & Soda"


def test_a_brand_the_seller_typed_is_never_overwritten():
    """Confidence "" means the seller entered or confirmed it (models
    .ItemSpecific). Nothing the AI decides outranks that."""
    from backend import main

    listing = _shirt(brand="Scotch & Soda",
                     title="Scotch & Soda Oxford Shirt",
                     item_specifics=[ItemSpecific(name="Brand",
                                                  value="Scotch & Soda",
                                                  confidence="")])
    main._retitle_for_brand(listing, "Ted Baker")

    assert listing.item_specifics[0].value == "Scotch & Soda"

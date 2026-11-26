"""Art's comp rungs are for art. A pair of Levi's is not "Levi's" the artist.

The art comp ladder (experts/art/comps) asks eBay what an ARTIST's work goes
for, and it reads the artist off the Artist specific -- or, failing that, the
brand. Every draft has a brand. So every branded draft whose full title found
no comps was priced next against the brand ALONE: "Levi's", "Pyrex" -- a
query that nearly always matches something, and whose median is the price of
everything that brand ever made. It ran before the head-of-title rung that
would have asked a sensible question, and its answer could fill a missing
price or raise a "low" one.

The rungs now run only for a draft that reads as art, or whose artist is on
the art roster (a draft titled "Untitled" by Marc Chagall is still a Chagall).
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.models import ItemSpecific, Listing  # noqa: E402
from backend.services.experts.art import comps  # noqa: E402


@pytest.mark.parametrize("listing", [
    Listing(title="Levis 501 Original Fit Mens Jeans 32x30 Medium Wash",
            brand="Levi's",
            item_specifics=[ItemSpecific(name="Type", value="Jeans"),
                            ItemSpecific(name="Material", value="Cotton")]),
    Listing(title="Pyrex Butterprint 403 Mixing Bowl 2.5 qt Turquoise",
            brand="Pyrex",
            item_specifics=[ItemSpecific(name="Pattern", value="Butterprint"),
                            ItemSpecific(name="Subject", value="Butterprint")]),
    # The Artist specific is a MUSIC aspect too, and a record is not a print.
    Listing(title="The Beatles Abbey Road Vinyl LP 1969 Apple UK Pressing",
            brand="Apple Records",
            item_specifics=[ItemSpecific(name="Artist", value="The Beatles")]),
], ids=["jeans", "bowl", "record"])
def test_a_branded_draft_that_is_not_art_gets_no_art_rungs(listing):
    assert comps.comp_queries(listing) == []


def test_a_draft_that_reads_as_art_still_gets_them():
    listing = Listing(title="Peter Max Umbrella Man Serigraph Hand Signed",
                      brand="Peter Max")
    assert comps.comp_queries(listing)[-1] == "Peter Max"


def test_a_rostered_artist_is_art_whatever_the_title_says():
    listing = Listing(title="Untitled", brand="Marc Chagall")
    assert comps.comp_queries(listing) == ["Marc Chagall"]


def test_a_branded_non_art_draft_walks_the_chain_it_always_did(monkeypatch):
    """Through the pricing chain: the full title, then its head -- and never
    the brand on its own."""
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main
    from backend.services import pricing

    asked: list[str] = []
    monkeypatch.setattr(pricing, "suggest",
                        lambda query, **kw: (asked.append(query),
                                             {"suggestion": {}})[1])
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "DRAFT_PRICE_COMPS", True)

    listing = Listing(title="Levis 501 Original Fit Mens Jeans 32x30 Medium Wash",
                      brand="Levi's", price=12.99)
    main._price_against_comps(listing)
    head = " ".join(listing.title.split()[:main._COMP_QUERY_WORDS])
    assert asked == [listing.title, head], asked

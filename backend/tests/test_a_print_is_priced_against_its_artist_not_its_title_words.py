""""What does a Chagall lithograph go for" is the question, and nobody asked it.

The comp search walks three rungs: the barcode, the whole title, then the
first few words of the title. That works for a thing with a model number and
fails specifically on art, in both directions.

A good art title is a bad search. The title rule packs it with everything that
identifies THIS one sheet -- the artist, the work, the medium, the edition
fraction, "Hand Signed", the size, "Framed" -- and a keyword search for all of
that at once matches nothing at all. That is not "this piece is rare"; it is
"this query is too specific", and downstream the two are the same answer.

Falling back to the first few words is better and still arbitrary: it takes
whatever the title happened to start with and hopes it is the artist.
Sometimes it is "Marc Chagall Le Bouquet". Sometimes it is "Vintage Framed
Original", and the app has just priced a signed lithograph against every
vintage frame on eBay.

By the time pricing runs, a drafted piece of art has been through the art
lookup and the roster, so the artist, the medium and the edition are FIELDS
rather than words that happen to be at the front of a string. The rungs are
built from those, in descending specificity, and each is a real question about
a real market.

And the last rung is the one that matters most, because it is the one a
keyword search cannot reach on its own: what this ARTIST's work sells for.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.models import ItemSpecific, Listing  # noqa: E402
from backend.services.experts.art import comps  # noqa: E402


def _print(**kw):
    base = dict(
        title="Marc Chagall Le Bouquet Lithograph Hand Signed Numbered 84/250 Framed",
        brand="Marc Chagall",
        item_specifics=[
            ItemSpecific(name="Artist", value="Marc Chagall"),
            ItemSpecific(name="Signed", value="Yes"),
            ItemSpecific(name="Edition Size", value="250"),
            ItemSpecific(name="Print Type", value="Lithograph"),
        ])
    base.update(kw)
    return Listing(**base)


# --- the rungs --------------------------------------------------------------

def test_the_rungs_run_from_this_piece_out_to_this_artists_market():
    rungs = comps.comp_queries(_print())
    assert rungs, "a drafted print with an artist must produce comp queries"
    # Every rung names the artist -- that is what makes it an art comp rather
    # than a keyword search that happens to match.
    assert all("Marc Chagall" in r for r in rungs)
    # ...and they get less specific, never more.
    assert rungs[-1] == "Marc Chagall"
    assert len(rungs[0]) >= len(rungs[-1])


def test_the_words_that_move_the_price_ride_a_rung_of_their_own():
    """A $15 open-edition poster and a $1,500 hand-signed edition are both
    "Chagall lithograph". Asking for the signed half is a different question
    from asking for the artist, and both are worth asking."""
    rungs = comps.comp_queries(_print())
    signed = [r for r in rungs if "hand signed" in r and "numbered" in r]
    assert signed, rungs
    # But not on the last rung, which has to stay broad enough to answer.
    assert "signed" not in rungs[-1]


def test_the_medium_is_read_not_guessed():
    """A medium invented here prices a gouache against the lithograph
    market."""
    assert comps.medium_of(_print()) == "lithograph"
    assert comps.medium_of(_print(
        title="Marcia Alpert Baby in a Basket Gouache", item_specifics=[]
    )) == "gouache"
    # Nothing says -> "" , and the rungs simply do without it.
    bare = _print(title="Untitled", item_specifics=[])
    assert comps.medium_of(bare) == ""
    assert comps.comp_queries(bare) == ["Marc Chagall"]


def test_edition_words_are_only_claimed_where_the_draft_earned_them():
    """The same rule the rest of the art path runs on: a mark that is not
    established is never asserted -- here it would be asserted at eBay, which
    quietly returns the wrong market."""
    unsigned = _print(title="Marc Chagall Le Bouquet Lithograph",
                      item_specifics=[ItemSpecific(name="Artist",
                                                   value="Marc Chagall")])
    assert comps.edition_words(unsigned) == ""
    assert not [r for r in comps.comp_queries(unsigned) if "signed" in r]


# --- and what it must NOT do ------------------------------------------------

def test_an_item_with_no_artist_produces_nothing_at_all():
    """Which is most items in this app and every item that is not art. An
    empty list means the generic title rungs run exactly as they did."""
    assert comps.comp_queries(Listing(title="Ceramic Coffee Mug")) == []
    assert comps.comp_queries(Listing(title="Levi's 501 W32 L34")) == []


def test_a_rung_is_never_asked_twice():
    """With no work and no medium several rungs collapse onto the artist's
    name, and asking eBay the same question three times is three times the
    latency for one answer."""
    rungs = comps.comp_queries(_print(title="Untitled", item_specifics=[]))
    assert len(rungs) == len(set(rungs))


def test_the_artist_comes_from_the_specific_before_the_brand():
    """The Artist specific is what the zoom pass and the roster write; the
    brand is where the identify pass puts a maker, which on art is sometimes
    the publisher or the frame shop."""
    listing = _print(brand="Some Gallery",
                     item_specifics=[ItemSpecific(name="Artist",
                                                  value="Marc Chagall")])
    assert comps.artist_of(listing) == "Marc Chagall"


# --- through the pricing chain ---------------------------------------------

def test_the_artist_rung_is_tried_before_the_head_of_the_title(monkeypatch):
    """The order is the point: the full title first (it is the most specific
    question), then the artist's market, and only then the crude fallback of
    whatever words the title happens to start with."""
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main
    from backend.services import pricing

    asked: list[str] = []

    def _suggest(query, **kw):
        asked.append(query)
        # Nothing matches until the artist's own market is asked about.
        if query == "Marc Chagall lithograph hand signed numbered":
            return {"suggestion": {"price": 450.0, "low": 300.0, "high": 600.0,
                                   "count": 12, "basis": "sold", "sold_data": True}}
        return {"suggestion": {}}

    monkeypatch.setattr(pricing, "suggest", _suggest)
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "DRAFT_PRICE_COMPS", True)

    listing = _print(price=19.99)
    main._price_against_comps(listing)

    assert asked[0] == listing.title, "the whole title is still asked first"
    assert "Marc Chagall lithograph hand signed numbered" in asked
    # The crude head-of-title rung was never needed.
    head = " ".join(listing.title.split()[:main._COMP_QUERY_WORDS])
    assert head not in asked, asked
    assert listing.price and float(listing.price) > 19.99


def test_a_non_art_draft_walks_exactly_the_chain_it_always_did(monkeypatch):
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main
    from backend.services import pricing

    asked: list[str] = []
    monkeypatch.setattr(pricing, "suggest",
                        lambda query, **kw: (asked.append(query), {"suggestion": {}})[1])
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "DRAFT_PRICE_COMPS", True)

    listing = Listing(title="Blue Ceramic Coffee Mug Handmade Stoneware",
                      price=8.0)
    main._price_against_comps(listing)
    head = " ".join(listing.title.split()[:main._COMP_QUERY_WORDS])
    assert asked == [listing.title, head], asked

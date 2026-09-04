"""A fresh AI draft's price gets checked against the market before a seller
ever sees it.

The identify pass prices from the PHOTOS ALONE. It never sees a comparable
listing, so its number is a guess — and the guess it makes about an item it
could not pin down is a low one. A hand-signed Fanch Ledan lithograph came
back drafted at $85, which is not a cautious answer: an underpriced listing
sells within the hour at a number nobody can take back, while an overpriced
one just sits there until the seller lowers it.

So every drafted price now gets one question put to eBay's own comparable
listings, and the answer may only ever push UP:

  * no price at all (the prompt asks for null rather than a guess when the
    value turns on an attribution the photos cannot confirm) -> the market
    fills it in;
  * a price far under what comparable listings ask -> the market overrules it;
  * anything else -> the draft's own number stands, including a high one.

Whatever changes is said out loud in missing_info, because a number that moved
under the seller on the one field where being wrong is unrecoverable is not
something to do quietly.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main
from backend.models import Listing


def _listing(**over) -> Listing:
    base = dict(title="Fanch Ledan hand signed lithograph Interior with Matisse",
                description="A signed lithograph.", price=85.0, quantity=1,
                condition="USED_EXCELLENT", images=["a.jpg"],
                category_id="360")
    base.update(over)
    return Listing(**base)


def _comps(price, low, high, count=12):
    """A pricing.suggest() answer with a usable suggestion in it."""
    return {"suggestion": {"price": price, "low": low, "high": high,
                           "count": count, "basis": "Live asking prices on eBay",
                           "sold_data": False},
            "checked": True}


@pytest.fixture()
def market(monkeypatch):
    """eBay's comps, stubbed. Records the query each call was made with."""
    calls: list[dict] = []

    def _install(answer):
        def suggest(query, category_id=None, condition=None, strategy=""):
            calls.append({"query": query, "category_id": category_id,
                          "condition": condition, "strategy": strategy})
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(main.pricing, "suggest", suggest)
        return calls

    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "DRAFT_PRICE_COMPS", True)
    return _install


def test_a_draft_far_under_the_market_is_repriced(market):
    """The bug this exists for: $85 against comps asking $450-$1,200."""
    market(_comps(price=700.0, low=450.0, high=1200.0))
    listing = _listing(price=85.0)

    used = main._price_against_comps(listing, uid="u1")

    # The comp median as a price to list at: the nearest .99 (money.charm_price).
    assert used and listing.price == 699.99
    note = " ".join(listing.missing_info)
    assert "85" in note and "450" in note and "1,200" in note
    assert "Confirm the price" in note


def test_a_draft_with_no_price_takes_the_market_s(market):
    """The prompt asks for null rather than an invented number when the value
    turns on an attribution the photos cannot confirm. null is not a hole in
    the draft — it is the question this lookup answers."""
    market(_comps(price=700.0, low=450.0, high=1200.0))
    listing = _listing(price=None)

    assert main._price_against_comps(listing, uid="u1")
    assert listing.price == 699.99
    assert "wouldn't put a number on this one" in " ".join(listing.missing_info)


def test_a_defensible_price_is_left_alone(market):
    """Under the comps is not the same as wrong: comps are ASKING prices on a
    keyword match, and a fair draft often sits below them on purpose."""
    market(_comps(price=700.0, low=450.0, high=1200.0))
    listing = _listing(price=400.0)          # 89% of the low quartile

    assert main._price_against_comps(listing, uid="u1") is None
    assert listing.price == 400.0
    assert listing.missing_info == []


def test_the_market_never_lowers_a_price(market):
    """A high price is the seller's to reduce after it fails to sell. Only the
    direction that cannot be undone gets overruled here."""
    market(_comps(price=700.0, low=450.0, high=1200.0))
    listing = _listing(price=2500.0)

    assert main._price_against_comps(listing, uid="u1") is None
    assert listing.price == 2500.0
    assert listing.missing_info == []


def test_the_lookup_is_scoped_to_the_drafted_item(market):
    calls = market(_comps(price=700.0, low=450.0, high=1200.0))
    listing = _listing()

    main._price_against_comps(listing, uid="u1")

    assert calls[0]["query"] == listing.title
    assert calls[0]["category_id"] == "360"
    assert calls[0]["condition"] == "USED_EXCELLENT"


@pytest.mark.parametrize("answer", [
    {"suggestion": None, "checked": True},          # nothing comparable listed
    {"suggestion": {"price": 0, "low": 0, "high": 0}, "checked": True},
    RuntimeError("eBay is down"),                   # the lookup itself failed
])
def test_a_market_that_cannot_answer_leaves_the_draft_alone(market, answer):
    """Best-effort, always: the draft is worth more than the comp."""
    market(answer)
    listing = _listing(price=85.0)

    assert main._price_against_comps(listing, uid="u1") is None
    assert listing.price == 85.0
    assert listing.missing_info == []


def test_a_title_too_specific_to_match_falls_back_to_its_head(market,
                                                              monkeypatch):
    """The valuable one-off is exactly the item whose full title matches
    nothing: artist, work, edition and size together return zero comps, and a
    silent zero leaves the $85 standing. The head of the title is the artist
    and the item, which is what a comp search can actually answer."""
    answers = {}
    calls: list[str] = []

    def suggest(query, category_id=None, condition=None, strategy=""):
        calls.append(query)
        return answers.get(query, {"suggestion": None, "checked": True})

    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "DRAFT_PRICE_COMPS", True)
    monkeypatch.setattr(main.pricing, "suggest", suggest)
    title = "Fanch Ledan hand signed lithograph Interior with Matisse 84/250"
    answers["Fanch Ledan hand signed lithograph"] = _comps(
        price=700.0, low=450.0, high=1200.0)
    listing = _listing(title=title, price=85.0)

    assert main._price_against_comps(listing, uid="u1")
    assert calls == [title, "Fanch Ledan hand signed lithograph"]
    assert listing.price == 699.99


def test_the_fallback_is_not_a_second_identical_search(market):
    """A short title IS its own head — asking eBay the same question twice
    spends the shared allowance for nothing."""
    calls = market({"suggestion": None, "checked": True})
    listing = _listing(title="Pyrex mixing bowl", price=4.0)

    assert main._price_against_comps(listing, uid="u1") is None
    assert [c["query"] for c in calls] == ["Pyrex mixing bowl"]


def test_nothing_is_looked_up_without_a_title_to_look_up(market):
    calls = market(_comps(price=700.0, low=450.0, high=1200.0))
    listing = _listing(title="", price=85.0)

    assert main._price_against_comps(listing, uid="u1") is None
    assert calls == []


def test_the_check_can_be_switched_off(market, monkeypatch):
    """One eBay call per drafted item, which a 50-item batch multiplies —
    so it has a kill switch. On by default."""
    calls = market(_comps(price=700.0, low=450.0, high=1200.0))
    monkeypatch.setattr(main, "DRAFT_PRICE_COMPS", False)
    listing = _listing(price=85.0)

    assert main._price_against_comps(listing, uid="u1") is None
    assert (calls, listing.price) == ([], 85.0)


def test_a_seller_without_ebay_credentials_still_gets_their_draft(market,
                                                                  monkeypatch):
    calls = market(_comps(price=700.0, low=450.0, high=1200.0))
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: False)
    listing = _listing(price=85.0)

    assert main._price_against_comps(listing, uid="u1") is None
    assert (calls, listing.price) == ([], 85.0)

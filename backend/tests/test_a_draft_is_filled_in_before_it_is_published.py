"""A draft arrives FINISHED. Nothing is left for the seller to press.

The app used to end the listing workflow with a "Finish up" card: one button,
above Publish, offering a pass over the listing's own photos that would fill
in the eBay category if it was still blank, every item specific the photos
could answer, and the maker. It was the right pass in the wrong place. The
seller had just watched the app spend a minute and several model calls
drafting the listing, and the last thing it said was that it had not finished.

So the pass runs at the end of drafting instead (`main._fill_what_is_left`),
where nothing is in its way: the photos are on the volume, research has
settled what the item is, and the category lookup has had its second attempt
at a number off the researched title. That last part is the whole reason this
step exists separately from the enrichment that runs earlier in the chain —
item specifics are per CATEGORY, so a draft that had no category number when
the first pass ran got no specifics at all, and that draft is exactly the one
the seller was then asked to finish by hand.

What has to hold:

  * a category that only arrived after research still gets its specifics;
  * a draft the earlier pass already enriched is NOT read a second time — the
    same vision calls seconds apart, charged twice;
  * a draft with no category at all stands the pass down rather than failing;
  * notes the fill answered stop being asked;
  * and none of it can take the draft down. Every step here is best-effort:
    a listing that is merely incomplete is still a listing, and an exception
    escaping this would lose the whole draft the seller's photos became.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main
from backend.models import ItemSpecific, Listing


def _draft(**over) -> Listing:
    return Listing(**{"title": "Nike hoodie", "category_id": "11450",
                      "images": ["img_00.jpg"], "missing_info": ["size"],
                      **over})


def _fills_size(seen: list):
    """A stand-in for the AI pass: records the listing it was handed and adds
    one specific, exactly as the real one does when it finds something."""
    def _fill(listing, paths, tags=None, progress=None):
        seen.append(listing.title)
        listing.item_specifics.append(
            ItemSpecific(name="Size", value="M", confidence="high"))
        return 1
    return _fill


@pytest.fixture(autouse=True)
def no_etsy(monkeypatch):
    """Etsy's own boxes have their own tests below; everywhere else this is a
    seller with no Etsy shop, which is every seller by default."""
    monkeypatch.setattr(main.marketplaces, "get", lambda key: None)


def test_a_category_that_arrived_late_still_gets_its_specifics(monkeypatch):
    seen: list = []
    monkeypatch.setattr(main, "_enrich_listing", _fills_size(seen))
    listing = _draft()

    added = main._fill_what_is_left(listing, [], uid="u1")

    assert added == 1
    assert seen == ["Nike hoodie"]
    assert [s.name for s in listing.item_specifics] == ["Size"]


def test_the_note_the_fill_answered_stops_being_asked(monkeypatch):
    monkeypatch.setattr(main, "_enrich_listing", _fills_size([]))
    listing = _draft()

    main._fill_what_is_left(listing, [], uid="u1")

    assert listing.missing_info == []


def test_a_draft_already_enriched_is_not_read_again(monkeypatch):
    """`enriched_at` is the app-wide record that the AI has read this listing
    against eBay's aspect list. Ignoring it would run the same vision calls
    seconds after the earlier pass and charge the seller for both."""
    seen: list = []
    monkeypatch.setattr(main, "_enrich_listing", _fills_size(seen))
    listing = _draft(enriched_at="2026-01-01T00:00:00+00:00")

    assert main._fill_what_is_left(listing, [], uid="u1") is None
    assert seen == []


def test_a_draft_with_no_category_stands_the_pass_down(monkeypatch):
    """Item specifics are per category. With no number there is nothing to
    ask eBay about — and nothing to charge for finding that out."""
    seen: list = []
    monkeypatch.setattr(main, "_enrich_listing", _fills_size(seen))
    listing = _draft(category_id="")

    assert main._fill_what_is_left(listing, [], uid="u1") is None
    assert seen == []


def test_a_fill_that_blows_up_does_not_take_the_draft_with_it(monkeypatch):
    def _boom(listing, paths, tags=None, progress=None):
        raise RuntimeError("the model fell over")
    monkeypatch.setattr(main, "_enrich_listing", _boom)
    listing = _draft()

    assert main._fill_what_is_left(listing, [], uid="u1") is None
    assert listing.title == "Nike hoodie"


# --- Etsy's three boxes -----------------------------------------------------
# The Etsy card asks for a category, when the item was made and who made it,
# and left them blank they are three dropdowns on the seller's list. Two of
# them can be read off the item; the third is an ATTESTATION, and the rule
# below is the one that keeps this fill honest.

class _Shop:
    def creds_for(self, uid):
        return {"access_token": "t"}


@pytest.fixture()
def etsy(monkeypatch):
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda key: _Shop() if key == "etsy" else None)
    monkeypatch.setattr(main.etsy_service, "suggest_taxonomy",
                        lambda listing: {"taxonomy_id": 1633, "path": "Vintage"})
    monkeypatch.setattr(main, "_enrich_listing",
                        lambda listing, paths, tags=None, progress=None: 0)


def test_etsy_reads_the_age_off_the_item(etsy):
    listing = _draft(item_specifics=[ItemSpecific(name="Decade", value="1970s")])

    main._fill_what_is_left(listing, [], uid="u1")

    assert listing.etsy.when_made == "1970s"
    assert listing.etsy.taxonomy_id == 1633
    # Vintage by Etsy's own line (20+ years), so "someone else made it" is a
    # listing Etsy allows — and the seller is not asked to tick it.
    assert listing.etsy.who_made == "someone_else"


def test_etsy_never_attests_to_an_item_it_cannot_date(etsy):
    """Etsy takes handmade, vintage (20+ years) and craft supplies and nothing
    else. "Someone else made it" on an item that is NOT vintage is an
    attestation that the listing breaks those rules, so an item the photos
    cannot date leaves both boxes for the seller, who owns it."""
    listing = _draft()

    main._fill_what_is_left(listing, [], uid="u1")

    assert listing.etsy.when_made == ""
    assert listing.etsy.who_made == ""
    # The category is not an attestation, so it is still filled in.
    assert listing.etsy.taxonomy_id == 1633


def test_etsy_leaves_a_seller_answer_alone(etsy):
    listing = _draft(etsy={"when_made": "1990s", "who_made": "i_did",
                           "taxonomy_id": 99})

    main._fill_what_is_left(listing, [], uid="u1")

    assert listing.etsy.when_made == "1990s"
    assert listing.etsy.who_made == "i_did"
    assert listing.etsy.taxonomy_id == 99


def test_a_seller_with_no_etsy_shop_is_not_asked_about_etsy(monkeypatch):
    """Not unanswered — irrelevant. The category lookup behind these boxes is
    a model call, and spending it on every draft in the app to fill a card the
    seller never opens is the cost of guessing wrong about that."""
    monkeypatch.setattr(main.marketplaces, "get", lambda key: None)
    monkeypatch.setattr(main, "_enrich_listing",
                        lambda listing, paths, tags=None, progress=None: 0)
    asked: list = []
    monkeypatch.setattr(main.etsy_service, "suggest_taxonomy",
                        lambda listing: asked.append(listing) or {})
    listing = _draft(item_specifics=[ItemSpecific(name="Decade", value="1970s")])

    main._fill_what_is_left(listing, [], uid="u1")

    assert asked == []
    assert listing.etsy.taxonomy_id == 0
    assert listing.etsy.when_made == ""


def test_an_etsy_lookup_that_fails_is_not_the_drafts_problem(etsy, monkeypatch):
    def _boom(listing):
        raise RuntimeError("Etsy's taxonomy is down")
    monkeypatch.setattr(main.etsy_service, "suggest_taxonomy", _boom)
    listing = _draft(item_specifics=[ItemSpecific(name="Decade", value="1970s")])

    main._fill_what_is_left(listing, [], uid="u1")

    assert listing.etsy.taxonomy_id == 0
    # The two it could answer without the lookup still landed.
    assert listing.etsy.when_made == "1970s"

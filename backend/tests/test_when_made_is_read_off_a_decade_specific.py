"""Etsy asks when an item was made; an eBay seller has usually said.

"Decade: 1990s" is an item specific a vintage seller fills in, and "90s" is
in half the titles. Reading it is the difference between a crosspost that
asks one question per batch and one that asks it per listing — and between
a batch answer that is right for everything and one that is wrong for the
oldest item in the pile.
"""
from backend.services import crosspost
from backend.models import EtsyFields, ItemSpecific, Listing


def _listing(title="A thing", **specifics):
    return Listing(title=title, price=10.0, quantity=1,
                   item_specifics=[ItemSpecific(name=k, value=v)
                                   for k, v in specifics.items()])


def test_a_decade_specific_is_read_straight():
    assert crosspost.when_made_from(_listing(Decade="1990s")) == "1990s"
    assert crosspost.when_made_from(_listing(Era="1970s")) == "1970s"


def test_a_year_lands_in_etsys_own_bucket():
    assert crosspost.when_made_from(_listing(**{"Year Manufactured": "1985"})) == "1980s"
    assert crosspost.when_made_from(_listing(**{"Year Manufactured": "2003"})) == "2000_2006"
    assert crosspost.when_made_from(_listing(**{"Year Manufactured": "2008"})) == "2007_2009"


def test_a_year_older_than_etsys_own_list_is_left_to_the_seller():
    """The bucket exists, and the SCANNER deliberately stops at 1700: a
    four-digit number below it in a listing is far more often a model or a
    lot number than a year, and reading one wrongly would attest to Etsy
    that a modern item is an antique. Unread falls through to the batch
    answer, which a person chose."""
    assert crosspost._bucket_for_year(1650) == "before_1700"
    assert crosspost.when_made_from(_listing(**{"Year Manufactured": "1650"})) == ""


def test_the_title_answers_when_the_specifics_do_not():
    assert crosspost.when_made_from(_listing("Vintage 1970s Pyrex bowl")) == "1970s"
    assert crosspost.when_made_from(_listing("Levi's 501 90s wash")) == "1990s"
    assert crosspost.when_made_from(_listing("Nike hoodie")) == ""


def test_the_decade_specific_wins_over_the_title():
    listing = _listing("Vintage 1970s style repro tee", Decade="2010s")
    assert crosspost.when_made_from(listing) == "2010_2019"


def test_the_listings_own_answer_beats_everything_and_the_batch_is_last():
    batch = {"who_made": "someone_else", "when_made": "1980s", "is_supply": False,
             "shipping_profile_id": "7"}
    # Nothing on the listing, nothing in the details: the batch answers.
    plain = crosspost.apply_defaults(_listing("Nike hoodie"), batch)
    assert (plain.who_made, plain.when_made) == ("someone_else", "1980s")
    assert plain.shipping_profile_id == "7"
    # The details answer, over the batch.
    read = crosspost.apply_defaults(_listing(Decade="1990s"), batch)
    assert read.when_made == "1990s"
    # The seller's own answer, over both.
    own = _listing(Decade="1990s")
    own.etsy = EtsyFields(who_made="i_did", when_made="made_to_order",
                          shipping_profile_id="99")
    kept = crosspost.apply_defaults(own, batch)
    assert (kept.who_made, kept.when_made) == ("i_did", "made_to_order")
    assert kept.shipping_profile_id == "99"

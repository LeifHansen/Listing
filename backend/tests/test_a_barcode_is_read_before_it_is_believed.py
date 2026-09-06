"""The scanner reads the stickers, and the check digit decides what it keeps.

Two things were sitting unread in almost every photo a seller uploads.

The first is the barcode. Every prompt in the app mentioned it — always as a
clause inside a paragraph about something else ("...and the human-readable
digits printed under any barcode") — and nothing downstream ever did anything
with the answer. So a boxed item whose UPC was in frame got a listing priced
off a keyword search for the words in its own title, which is a search for
items that SOUND like it. A UPC is not a keyword: it is the same product, and
eBay will search by it.

The second is every sticker that is not in English. An importer label, a
Japanese neck tag, a Cyrillic factory stamp, a "Fabriqué en France" line — a
domestic-market marking is often what makes a piece worth more than its US
equivalent, and it is also the thing that dates it. The prompts named neck
labels and care tags and stopped there.

Reading them is only half of it, and the other half is what these tests are
mostly about. A model reading twelve digits off 6-point type misreads them,
and a misread UPC is the single most expensive mistake this app can make: it
does not bounce, because eBay matches it against its CATALOGUE, so the
listing quietly comes up carrying another company's product page, photos and
price history. GTINs and ISBNs carry their own check digit precisely so that
cannot happen silently — so nothing here trusts a code it has not verified:

  * a code whose check digit agrees is written as UPC/EAN/ISBN at "high";
  * a code whose check digit does NOT agree never reaches the listing, and
    becomes a note asking the seller to read the barcode again;
  * the same guard runs again at the very last moment before eBay, over
    values from any pass — but never over a number the seller typed
    themselves, which is theirs;
  * an MPN, which has no checksum to agree with, goes on flagged for review.
"""
from __future__ import annotations

import pytest

from backend.services import barcodes
from backend.services.listing_prompt import (
    LISTING_SCHEMA,
    STICKER_AND_BARCODE_RULE,
)

# Real, well-formed codes. Every one of these is a published example of its
# symbology, so a change that breaks the arithmetic breaks these first.
UPC_A = "036000291452"
EAN_13 = "5901234123457"
EAN_8 = "96385074"
ISBN_13 = "9780306406157"
ISBN_10 = "0306406152"
ISBN_10_X = "080442957X"


# --- the arithmetic ---------------------------------------------------------

@pytest.mark.parametrize("code", [UPC_A, EAN_13, EAN_8, ISBN_13, ISBN_10,
                                  ISBN_10_X])
def test_a_real_code_verifies(code):
    assert barcodes.verified(code)


@pytest.mark.parametrize("code", [
    "036000291453",     # last digit misread
    "036000291542",     # two digits transposed
    "5901234123456",
    "9780306406158",
    "0306406153",
    "12345",            # not a code length at all
    "",
])
def test_a_misread_code_does_not(code):
    assert not barcodes.verified(code)


def test_the_separators_a_barcode_is_printed_with_are_not_part_of_it():
    """UPC-A is printed in groups and ISBN is set with hyphens; a transcript
    carries them through, and a code that only verifies when it arrives as one
    unbroken string is a code this app would keep throwing away."""
    assert barcodes.verified("0 36000 29145 2")
    assert barcodes.verified("978-0-306-40615-7")
    assert barcodes.normalize("0 36000 29145 2") == UPC_A


def test_each_code_is_filed_under_the_aspect_ebay_publishes_it_as():
    """eBay has separate UPC, EAN and ISBN specifics, and its catalogue never
    matches a code filed under the wrong one."""
    assert barcodes.kind(UPC_A) == "UPC"
    assert barcodes.kind(EAN_13) == "EAN"
    assert barcodes.kind(ISBN_13) == "ISBN"
    assert barcodes.kind(ISBN_10) == "ISBN"
    # A 13-digit code that is a UPC-A carrying the leading zero the US market
    # drops is still a UPC to eBay and to a buyer.
    assert barcodes.kind("0" + UPC_A) == "UPC"
    assert barcodes.symbology(UPC_A) == "UPC-A"
    assert barcodes.symbology(ISBN_10) == "ISBN-10"
    # Nothing is filed at all until it verifies.
    assert barcodes.kind("036000291453") == ""


def test_an_old_isbn_searches_the_market_that_moved_on_without_it():
    """Everything printed since 2007 carries the 13-digit form. A 10-digit
    ISBN off a copyright page searches a marketplace that has stopped using
    it, so it is converted rather than sent as-is."""
    assert barcodes.isbn13(ISBN_10) == ISBN_13
    assert barcodes.search_terms(ISBN_10) == ISBN_13
    assert barcodes.isbn13(UPC_A) == ""


def test_only_a_upc_is_handed_to_ebays_product_search():
    """Browse documents its `gtin` parameter as taking a UPC. An EAN or ISBN
    sent there is a query eBay answers with nothing, which reads exactly like
    "this item has no comps" — so those search as keywords instead."""
    assert barcodes.ebay_gtin(UPC_A) == UPC_A
    assert barcodes.ebay_gtin("0" + UPC_A) == UPC_A     # leading zero dropped
    assert barcodes.ebay_gtin(EAN_13) == ""
    assert barcodes.ebay_gtin(ISBN_13) == ""
    assert barcodes.ebay_gtin("036000291453") == ""


def test_codes_are_picked_out_of_a_tag_transcript():
    """What the zoom pass hands back is prose: sizes, RN numbers, care
    symbols and prices around the digits. The check digit is what separates
    the code from the rest, so nothing has to parse the sentence."""
    transcript = (
        "NECK LABEL: UNIQLO / ユニクロ  SIZE L\n"
        "CARE: 100% COTTON  MADE IN CHINA  中国製\n"
        "RN 12345   STYLE 441-A   $4.99 price sticker\n"
        f"BARCODE UPC-A: 0 36000 29145 2\n"
        f"Second sticker: {EAN_13}\n"
        "Blurred one: 036000291453\n"
        "ISBN 978-0-306-40615-7 on the back cover\n"
    )
    found = barcodes.find(transcript)
    assert [f["value"] for f in found] == [UPC_A, EAN_13, ISBN_13]
    assert [f["kind"] for f in found] == ["UPC", "EAN", "ISBN"]


def test_two_codes_on_one_line_do_not_merge_into_a_third_thing():
    """A single greedy run over "036000291452 5901234123457" is 24 digits,
    which is no code at all — and swallowing the first one is how a verified
    UPC goes missing."""
    assert [f["value"] for f in barcodes.find(f"{UPC_A} {EAN_13}")] \
        == [UPC_A, EAN_13]


def test_a_code_shaped_value_is_recognised_even_when_it_fails():
    """The guard's other half. Twelve digits in a UPC box are a UPC whatever
    the model called them; if they do not check out they are a misread, not a
    part number, and something has to be able to say so."""
    assert barcodes.looks_like_a_code("036000291453")
    assert not barcodes.verified("036000291453")
    assert not barcodes.looks_like_a_code("ABC-123-XY")   # a real MPN


# --- the prompt the scanner is actually run under ---------------------------

def test_the_scanner_is_told_to_read_stickers_in_other_languages():
    rule = STICKER_AND_BARCODE_RULE
    for script in ("Japanese", "Korean", "Chinese", "Cyrillic", "Greek",
                   "Arabic", "Hebrew", "Thai", "Devanagari"):
        assert script in rule, script
    # Verbatim first, then a romanization — a mark translated into a brand it
    # does not say is a false claim about the item.
    assert "VERBATIM" in rule
    assert "romanization" in rule
    assert "NEVER translate a mark into a brand it does not say" in rule
    # The stickers that carry the money: importer labels and licence lines.
    assert "IMPORTER" in rule
    assert "copyright and licence lines" in rule


def test_the_scanner_is_told_never_to_complete_a_code_it_cannot_read():
    rule = STICKER_AND_BARCODE_RULE
    assert "NEVER complete, correct, pad or infer a code" in rule
    assert "legible" in rule
    # And why, in the terms that make it worth obeying.
    assert "DIFFERENT product" in rule


def test_the_identify_schema_asks_for_the_codes_and_boxes_the_stickers():
    assert '"identifiers"' in LISTING_SCHEMA
    # The rule itself rides along with the schema, so the identify pass reads
    # under the same instructions as the zoom pass.
    assert STICKER_AND_BARCODE_RULE in LISTING_SCHEMA
    # A barcode earns a crop of its own even with no garment tag near it.
    assert "sticker|price" in LISTING_SCHEMA


# --- what reaches the listing ----------------------------------------------

def _listing(**kw):
    from backend.models import Listing
    return Listing(**kw)


def _scan(*entries):
    return barcodes.from_scan(list(entries))


def test_a_verified_code_is_written_as_the_aspect_ebay_asks_for():
    listing = _listing()
    found = _scan({"type": "UPC", "value": UPC_A, "source": "box end",
                   "legible": True})
    assert barcodes.apply_to_listing(listing, found) == 1
    written = [(s.name, s.value, s.confidence) for s in listing.item_specifics]
    assert written == [("UPC", UPC_A, "high")]
    # It was READ, not inferred, so there is nothing for the seller to check.
    assert listing.missing_info == []


def test_a_misread_code_becomes_a_question_and_never_a_specific():
    """The one that costs real money. eBay matches a UPC against its
    catalogue, so a wrong one does not fail — it succeeds, and the listing
    comes up carrying somebody else's product."""
    listing = _listing()
    found = _scan({"type": "UPC", "value": "036000291453", "legible": True})
    assert barcodes.apply_to_listing(listing, found) == 0
    assert listing.item_specifics == []
    assert any("barcode" in m.lower() for m in listing.missing_info)
    assert any("036000291453" in m for m in listing.missing_info)


def test_a_code_the_model_says_it_could_not_fully_read_is_dropped_outright():
    """A code with a digit missing is not a code, whatever the rest of it
    happens to check out as — and it must not even reach the seller as a
    number to confirm, because it is not one."""
    listing = _listing()
    assert _scan({"type": "UPC", "value": UPC_A, "legible": False}) == []
    assert _scan({"type": "UPC", "value": "03600029145?"}) == []
    assert barcodes.apply_to_listing(listing, _scan(
        {"type": "UPC", "value": UPC_A, "legible": False})) == 0
    assert listing.item_specifics == []


def test_an_mpn_has_no_check_digit_so_it_goes_on_flagged_for_review():
    listing = _listing()
    found = _scan({"type": "MPN", "value": "CB-441-A", "source": "plate"})
    assert barcodes.apply_to_listing(listing, found) == 1
    spec = listing.item_specifics[0]
    assert (spec.name, spec.value, spec.confidence) == ("MPN", "CB-441-A",
                                                        "medium")


def test_a_code_the_seller_already_entered_is_never_overwritten():
    from backend.models import ItemSpecific
    listing = _listing(item_specifics=[
        ItemSpecific(name="UPC", value="012345678905", confidence="")])
    found = _scan({"type": "UPC", "value": UPC_A})
    assert barcodes.apply_to_listing(listing, found) == 0
    assert listing.item_specifics[0].value == "012345678905"


def test_the_listing_hands_its_code_to_the_comp_search():
    from backend.models import ItemSpecific
    listing = _listing(item_specifics=[
        ItemSpecific(name="UPC", value=UPC_A, confidence="high")])
    assert barcodes.listing_code(listing) == (UPC_A, UPC_A)
    # An ISBN prices off its digits as keywords; Browse's product search takes
    # a UPC only, so the second slot stays empty rather than sending eBay a
    # query it answers with nothing.
    book = _listing(item_specifics=[
        ItemSpecific(name="ISBN", value=ISBN_10, confidence="high")])
    assert book.item_specifics and barcodes.listing_code(book) == (ISBN_13, "")
    # Nothing at all is the ordinary case, and it must not look like a code.
    assert barcodes.listing_code(_listing()) == ("", "")


def test_a_code_that_stopped_verifying_is_not_used_to_price_the_item():
    """A record written before the check existed, or edited by hand into a
    typo: pricing an item off a UPC that is not one is pricing it off
    somebody else's product."""
    from backend.models import ItemSpecific
    listing = _listing(item_specifics=[
        ItemSpecific(name="UPC", value="036000291453", confidence="high")])
    assert barcodes.listing_code(listing) == ("", "")


# --- the last gate before eBay ---------------------------------------------

# The category eBay would publish this under, stubbed: sanitize_specifics
# fetches the aspect list, and these tests are about the check digit, not
# about the Taxonomy API.
CATEGORY_ASPECTS = {"aspects": [
    {"name": "UPC", "required": False, "mode": "FREE_TEXT", "values": [],
     "cardinality": "SINGLE", "data_type": "STRING", "format": "",
     "max_length": 0},
    {"name": "Color", "required": False, "mode": "FREE_TEXT", "values": [],
     "cardinality": "SINGLE", "data_type": "STRING", "format": "",
     "max_length": 0},
]}


@pytest.fixture
def sanitize(monkeypatch):
    """taxonomy.sanitize_specifics over a stubbed category."""
    from backend.services import taxonomy
    monkeypatch.setattr(taxonomy, "item_aspects", lambda cid: CATEGORY_ASPECTS)
    return taxonomy.sanitize_specifics


def test_a_bad_ai_code_is_dropped_on_the_way_out_whatever_wrote_it(sanitize):
    """sanitize_specifics is the final thing that touches a listing before
    eBay does, so the check runs there too — over values from any pass, not
    just the identify one."""
    from backend.models import ItemSpecific

    listing = _listing(category_id="11450", item_specifics=[
        ItemSpecific(name="UPC", value="036000291453", confidence="high"),
        ItemSpecific(name="Color", value="Blue", confidence="high"),
    ])
    sanitize(listing)
    names = [s.name for s in listing.item_specifics]
    assert "UPC" not in names
    assert "Color" in names


def test_a_good_code_survives_that_same_gate(sanitize):
    from backend.models import ItemSpecific

    listing = _listing(category_id="11450", item_specifics=[
        ItemSpecific(name="UPC", value=UPC_A, confidence="high")])
    sanitize(listing)
    assert [s.value for s in listing.item_specifics] == [UPC_A]


def test_the_sellers_own_number_stands_even_when_it_looks_wrong_to_us(sanitize):
    """`confidence` is "" for a value the seller entered or confirmed. They
    are holding the item; a check-digit rule that overrides them is a rule
    that deletes their work and tells them nothing."""
    from backend.models import ItemSpecific

    listing = _listing(category_id="11450", item_specifics=[
        ItemSpecific(name="UPC", value="036000291453", confidence="")])
    sanitize(listing)
    assert [s.value for s in listing.item_specifics] == ["036000291453"]


def test_does_not_apply_is_still_a_legitimate_answer_in_a_upc_box(sanitize):
    """eBay itself suggests it for vintage and handmade items, and it is not
    a code, so the check digit has no opinion about it."""
    from backend.models import ItemSpecific

    listing = _listing(category_id="11450", item_specifics=[
        ItemSpecific(name="UPC", value="Does Not Apply", confidence="high")])
    sanitize(listing)
    assert [s.value for s in listing.item_specifics] == ["Does Not Apply"]


# --- pricing off the barcode ------------------------------------------------

def test_a_upc_prices_the_product_instead_of_the_words_in_the_title(monkeypatch):
    """The point of reading the barcode. A keyword search matches listings
    that SOUND like this one; a UPC matches the same product."""
    from backend import config
    from backend.services import pricing

    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"itemSummaries": [
                {"price": {"value": "20.00"}, "title": "a", "itemWebUrl": ""},
                {"price": {"value": "30.00"}, "title": "b", "itemWebUrl": ""},
            ]}

    monkeypatch.setattr(config, "EBAY_CLIENT_ID", "id")
    monkeypatch.setattr(config, "EBAY_CLIENT_SECRET", "secret")
    # pricing imported the token helper by name, so the stub goes on pricing.
    monkeypatch.setattr(pricing, "_app_token", lambda: "token")
    monkeypatch.setattr(pricing.httpx, "get",
                        lambda *a, **kw: (seen.update(kw.get("params") or {}),
                                          _Resp())[1])

    out = pricing.active_comps("vintage teacup", gtin=UPC_A)
    assert seen.get("gtin") == UPC_A
    # `q` and `gtin` are alternatives on Browse, not companions.
    assert "q" not in seen
    assert out["label"] == ("Live asking prices for this exact product "
                            "(barcode match)")
    assert UPC_A in out["search_url"]

    seen.clear()
    out = pricing.active_comps("vintage teacup")
    assert seen.get("q") == "vintage teacup"
    assert "gtin" not in seen
    assert out["label"] == "Live asking prices on eBay"

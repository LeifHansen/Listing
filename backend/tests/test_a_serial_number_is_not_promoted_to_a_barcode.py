"""A serial number that happens to pass a check digit is still a serial number.

The scanner reads every identifier off the item and says what each one is:
UPC, EAN, ISBN -- or MPN, Model, Style, Serial, the codes on a plate that
carry no check digit at all. The barcode guard then asked only whether the
DIGITS check out, and never what the model said they were. Ten digits of a
drill's serial plate pass ISBN-10's mod-11 check about one time in eleven
(twelve or thirteen pass mod-10 one time in ten), and every one that did went
onto the listing as an ISBN at "high" confidence -- read, verified, not to be
reviewed -- and was then handed to the comp search as the product code.

A declared plate code is now kept as what the model said it was.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.models import Listing  # noqa: E402
from backend.services import barcodes  # noqa: E402

# Ten digits that pass ISBN-10's check digit, and twelve that pass UPC-A's.
LUCKY_TEN = "3000000003"
LUCKY_TWELVE = "036000291452"


def test_the_fixtures_really_do_pass_their_check_digits():
    assert barcodes.valid_isbn10(LUCKY_TEN)
    assert barcodes.valid_gtin(LUCKY_TWELVE)


@pytest.mark.parametrize("declared", ["Serial", "Model", "MPN", "Style",
                                      "Model Number", "Part"])
@pytest.mark.parametrize("value", [LUCKY_TEN, LUCKY_TWELVE])
def test_a_plate_code_is_never_verified_as_a_product_code(declared, value):
    found = barcodes.from_scan([{"type": declared, "value": value,
                                 "source": "plate under the base"}])
    assert [f["verified"] for f in found] == [False]
    assert not found[0].get("checksum_failed")

    listing = Listing(title="Cordless drill")
    barcodes.apply_to_listing(listing, found)
    names = {s.name for s in listing.item_specifics}
    assert not names & {"UPC", "EAN", "ISBN"}
    assert barcodes.listing_code(listing) == ("", "")
    # ...and a plate code is not a barcode to re-read, either.
    assert not any("barcode" in m.lower() for m in listing.missing_info)


def test_a_declared_barcode_is_still_verified():
    for declared in ("UPC", "upc", "other", ""):
        found = barcodes.from_scan([{"type": declared, "value": LUCKY_TWELVE}])
        assert found[0]["verified"] and found[0]["kind"] == "UPC", declared


def test_an_mpn_still_goes_on_flagged_for_review():
    listing = Listing(title="Cordless drill")
    barcodes.apply_to_listing(listing, barcodes.from_scan(
        [{"type": "MPN", "value": LUCKY_TEN}]))
    assert [(s.name, s.value, s.confidence) for s in listing.item_specifics] \
        == [("MPN", LUCKY_TEN, "medium")]

"""eBay's "required item specific is missing" is read by ONE parser.

Two parsers read that sentence: one to tell the seller which box to fill
(ebay_errors), one to decide whether a revise can go out without its specifics
(ebay_trading.missing_required_specific). They disagreed. The revise's missed
"Missing required item specific: X" and the quoted "'Type'" form, so on those
wordings the price change riding with it was refused on every save; the
seller-facing one missed a sentence that starts "Item specific ...".
"""
from __future__ import annotations

import pytest

from backend import ebay_errors
from backend.services import ebay_trading

WORDINGS = [
    ("The item specific Inseam is missing. Add Inseam to this listing.", "Inseam"),
    ("Missing required item specific: Country/Region of Manufacture",
     "Country/Region of Manufacture"),
    ("The required item specific 'Type' is missing.", "Type"),
    ("Item specific Brand is missing.", "Brand"),
    ("The item specific Unit Quantity is missing.", "Unit Quantity"),
]


@pytest.mark.parametrize("sentence,aspect", WORDINGS)
def test_both_sides_read_the_same_aspect(sentence, aspect):
    exc = ebay_trading.TradingError(sentence)
    assert ebay_trading.missing_required_specific(exc) == aspect
    assert ebay_errors._aspect_from_text(sentence) == aspect


def test_a_sentence_that_names_no_aspect_names_none():
    exc = ebay_trading.TradingError(
        "This listing is missing one or more required item specifics.")
    assert ebay_trading.missing_required_specific(exc) == ""


def test_a_specific_that_is_not_missing_is_not_a_missing_specific():
    """The revise only sets its specifics aside for a MISSING one."""
    exc = ebay_trading.TradingError(
        "The item specific Size Type has an invalid value.")
    assert ebay_trading.missing_required_specific(exc) == ""


def test_the_aspect_can_arrive_in_the_detail():
    exc = ebay_trading.TradingError("eBay rejected the request.",
                                    detail="Missing required item specific: Inseam")
    assert ebay_trading.missing_required_specific(exc) == "Inseam"

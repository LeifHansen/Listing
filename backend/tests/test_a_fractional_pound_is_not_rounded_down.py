"""A package weight with a fraction of a pound reaches eBay whole.

`_package_details` sent int(package_weight_lb), so 1.5 lb went out as 1 lb
0 oz -- eight ounces lighter than the box, on calculated postage the seller
pays the difference on -- while the checklist judged the same listing on the
full 24 oz. The weight is now converted to ounces once and split from there.
"""
from __future__ import annotations

import re

from backend.models import Listing
from backend.services import ebay_trading


def _sent(lb, oz):
    xml = ebay_trading._package_details(Listing(package_weight_lb=lb,
                                                package_weight_oz=oz))
    major = re.search(r"<WeightMajor[^>]*>([^<]*)<", xml).group(1)
    minor = re.search(r"<WeightMinor[^>]*>([^<]*)<", xml).group(1)
    return int(major), float(minor)


def test_half_a_pound_is_eight_ounces():
    assert _sent(1.5, 0) == (1, 8.0)


def test_a_fraction_carries_into_the_ounces_already_there():
    assert _sent(2.25, 10) == (2, 14.0)
    assert _sent(0.5, 12) == (1, 4.0)


def test_whole_pounds_and_ounces_go_as_they_always_did():
    assert _sent(3, 4.5) == (3, 4.5)
    assert _sent(0, 6) == (0, 6.0)
    assert ebay_trading._package_details(Listing()) == ""

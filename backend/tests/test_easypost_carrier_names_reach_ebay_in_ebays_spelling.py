"""eBay links a tracking number to the carrier's site by the exact string.

EasyPost names its carrier ACCOUNTS (UPSDAP, FedExDefault, DhlEcs); eBay's
createShippingFulfillment wants the carrier as eBay spells it (USPS, UPS,
FedEx, DHL). The bridge is one table, and FedEx keeps its capital E.
"""
from __future__ import annotations

import pytest

from backend.services import easypost


@pytest.mark.parametrize("theirs, ebays", [
    ("USPS", "USPS"),
    ("UPS", "UPS"), ("UPSDAP", "UPS"), ("UPSMailInnovations", "UPS"),
    ("FedEx", "FedEx"), ("FedExDefault", "FedEx"), ("FedExSmartPost", "FedEx"),
    ("DHLExpress", "DHL"), ("DhlEcs", "DHL"), ("DHLEcommerce", "DHL"),
    ("fedex", "FedEx"),      # case-insensitive
    ("OnTrac", "OnTrac"),    # unknown carriers pass through unchanged
    ("", ""),
    (None, ""),
])
def test_carrier_names_are_translated(theirs, ebays):
    assert easypost.ebay_carrier_code(theirs) == ebays

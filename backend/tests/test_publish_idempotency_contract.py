"""Publishing twice must not create two listings — using eBay's real contract.

Creating a listing is the one operation here that is not naturally
idempotent: a second call means a second live listing, which is a duplicate on
the seller's account and an eBay policy problem. The app guarded that with a
contract eBay does not implement:

  - `<InventoryTrackingNumber>` is not an element of eBay's ItemType.
    AddFixedPriceItem ignores it, so the "unique among the seller's active
    listings" second guard the code promised never existed.
  - `GetItem` was then queried BY that field, with neither ItemID nor SKU. That
    request cannot succeed, so the "eBay accepted it but we lost the response"
    recovery arm was dead code — and a duplicate rejection whose message
    happens not to name an item id left the seller on a dead end that tells
    them publishing again might duplicate.
  - `_DUPLICATE_CODES` did not contain 488, eBay's actual duplicate-UUID code,
    and did contain 21916884/21916885, which are eBay's item-CONDITION codes.
    So a fixable "condition" rejection was reported to the seller as "already
    published", and the message telling them what to fix was swallowed.

eBay's documented answer to exactly this problem is Item.SKU plus
Item.InventoryTrackingMethod=SKU at create time, then GetItem by SKU.

Contracts:
  https://developer.ebay.com/support/kb-article?KBid=1462  (recovering a lost
      AddFixedPriceItem response)
  https://developer.ebay.com/devzone/xml/docs/reference/ebay/getitem.html
  Error 488 "Duplicate UUID used." — LongMessage carries the prior item id:
      "The specified UUID has already been used; ListedByRequestAppId=1,
       item ID=110040602158"
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import ebay_trading

KEY = "0123456789abcdef0123456789abcdef"


def _fixed_price() -> Listing:
    return Listing(title="A lamp", price=25.0, quantity=1,
                   listing_format="FIXED_PRICE", condition="USED_EXCELLENT",
                   category_id="112581", package_weight_lb=1.0)


def _auction() -> Listing:
    return Listing(title="A lamp", price=25.0, quantity=1,
                   listing_format="AUCTION", auction_start_price=1.0,
                   condition="USED_EXCELLENT", category_id="112581",
                   package_weight_lb=1.0)


def _add_body(listing: Listing) -> str:
    return ebay_trading.build_add_item(
        listing, ["https://example.test/1.jpg"], {}, "97201",
        idempotency_key=KEY)[1]


# ------------------------------------------------- the create-side contract

def test_a_fixed_price_create_uses_sku_tracking_not_an_invented_field():
    """InventoryTrackingNumber is not an ItemType element; eBay ignores it.
    SKU + InventoryTrackingMethod=SKU is the documented pairing, and both
    must be set on the CREATE — a later revise cannot add the method."""
    body = _add_body(_fixed_price())
    assert "<InventoryTrackingNumber>" not in body
    assert f"<SKU>{KEY}</SKU>" in body
    assert "<InventoryTrackingMethod>SKU</InventoryTrackingMethod>" in body


def test_every_create_still_carries_the_uuid():
    """Item.UUID is the guard eBay really does honour, on both call types."""
    for listing in (_fixed_price(), _auction()):
        assert f"<UUID>{KEY}</UUID>" in _add_body(listing)


def test_an_auction_gets_no_sku_tracking():
    """InventoryTrackingMethod is a fixed-price concept; AddItem keeps UUID
    alone, which is what the existing branch already did."""
    body = _add_body(_auction())
    assert "<InventoryTrackingMethod>" not in body
    assert "<SKU>" not in body


def test_opting_out_of_idempotency_sends_neither():
    body = ebay_trading.build_add_item(
        _fixed_price(), ["https://example.test/1.jpg"], {}, "97201",
        idempotency_key="")[1]
    assert "<UUID>" not in body and "<SKU>" not in body


# ---------------------------------------------------- the recovery contract

def test_the_lost_response_lookup_asks_by_sku(monkeypatch):
    """GetItem resolves a SKU only for listings created with
    InventoryTrackingMethod=SKU — which is why the create above sets it. The
    old call sent InventoryTrackingNumber and could never succeed."""
    sent = {}

    def _fake_call(call, token, body):
        sent["call"], sent["body"] = call, body
        import xml.etree.ElementTree as ET
        return ET.fromstring(
            '<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            "<Item><ItemID>110000000001</ItemID></Item></GetItemResponse>")

    monkeypatch.setattr(ebay_trading, "_call", _fake_call)

    assert ebay_trading.item_id_for_sku("tok", KEY) == "110000000001"
    assert sent["call"] == "GetItem"
    assert f"<SKU>{KEY}</SKU>" in sent["body"]
    assert "InventoryTrackingNumber" not in sent["body"]


# -------------------------------------------------------- duplicate codes

def test_488_is_recognised_as_a_duplicate():
    """eBay's actual duplicate-UUID code, and it was missing."""
    assert ebay_trading._is_duplicate_rejection(
        ebay_trading.TradingError("Duplicate UUID used.", code="488"))


def test_488_gives_up_the_item_id_it_names():
    """488's LongMessage carries the listing the first attempt created, which
    is what lets a retry adopt it instead of making a twin."""
    message = ("The specified UUID has already been used; "
               "ListedByRequestAppId=1, item ID=110040602158.")
    assert ebay_trading._item_id_in_error(message) == "110040602158"


def test_a_condition_error_is_not_reported_as_already_published():
    """21916884/21916885 are eBay's item-CONDITION codes, not idempotency
    signals. Treating them as duplicates told the seller their listing was
    already live and swallowed the message saying what to fix."""
    for code in ("21916884", "21916885"):
        assert not ebay_trading._is_duplicate_rejection(
            ebay_trading.TradingError(
                "Condition is required for this category.", code=code)), code


def test_genuine_duplicate_wording_is_still_caught_whatever_the_code():
    """The text fallback is what keeps this robust to codes eBay adds later,
    and it is why dropping the condition codes loses no real coverage."""
    for message in ("UUID has already been used.",
                    "The specified UUID has already been used; item ID=1.",
                    "Duplicate UUID supplied."):
        assert ebay_trading._is_duplicate_rejection(
            ebay_trading.TradingError(message)), message


def test_an_ordinary_rejection_is_still_not_a_duplicate():
    for message in ("The title is too long.",
                    "Item specifics are missing for this category.",
                    "Your item's location was not filled in."):
        assert not ebay_trading._is_duplicate_rejection(
            ebay_trading.TradingError(message)), message


def test_a_stray_number_is_not_adopted_as_an_item_id():
    """Adopting the wrong id would point the record at somebody else's
    listing. Prefer the id eBay actually labels."""
    message = ("The specified UUID has already been used; "
               "ListedByRequestAppId=123456789012, item ID=110040602158.")
    assert ebay_trading._item_id_in_error(message) == "110040602158"


@pytest.mark.parametrize("code", ["21919188", "21919067"])
def test_selling_limit_and_policy_codes_stay_out(code):
    """Already-known traps: 21919188 is the monthly SELLING LIMIT and 21919067
    is the duplicate-LISTING policy. Neither says a listing was created."""
    assert not ebay_trading._is_duplicate_rejection(
        ebay_trading.TradingError("You have exceeded a limit.", code=code))

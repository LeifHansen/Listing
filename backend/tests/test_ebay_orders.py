"""Order flattening (pure functions, no network)."""
from __future__ import annotations

from backend.services import ebay_orders

RAW_ORDER = {
    "orderId": "12-34567-89012",
    "creationDate": "2026-08-16T18:00:00.000Z",
    "orderFulfillmentStatus": "NOT_STARTED",
    "buyer": {"username": "buyer99"},
    "pricingSummary": {"total": {"value": "91.25", "currency": "USD"}},
    "lineItems": [{
        "lineItemId": "li-1", "legacyItemId": "4242", "quantity": 1,
        "title": "Vintage Levi's 501 Jeans", "total": {"value": "85.00"},
    }],
    "fulfillmentStartInstructions": [{
        "shippingStep": {"shipTo": {
            "fullName": "Jordan Buyer",
            "email": "j@example.com",
            "primaryPhone": {"phoneNumber": "5551234567"},
            "contactAddress": {
                "addressLine1": "1 Main St", "addressLine2": "Apt 2",
                "city": "Columbus", "stateOrProvince": "OH",
                "postalCode": "43004", "countryCode": "US",
            },
        }},
    }],
}


def test_order_to_dict_flattens_ship_to_and_line_items():
    o = ebay_orders._order_to_dict(RAW_ORDER)
    assert o["order_id"] == "12-34567-89012"
    assert o["total"] == "91.25"
    assert o["line_items"][0]["legacy_item_id"] == "4242"
    assert o["ship_to"]["name"] == "Jordan Buyer"
    assert o["ship_to"]["address2"] == "Apt 2"
    assert o["ship_to"]["postal_code"] == "43004"

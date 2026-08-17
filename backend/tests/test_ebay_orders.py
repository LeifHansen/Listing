"""Order flattening + the Pirate Ship CSV export (pure functions, no network)."""
from __future__ import annotations

import csv
import io

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


def test_pirate_ship_csv_has_address_weight_and_dims():
    order = ebay_orders._order_to_dict(RAW_ORDER)
    text = ebay_orders.pirate_ship_csv(
        [order],
        {"12-34567-89012": {"weight_lb": 1, "weight_oz": 4.5,
                            "length_in": 12, "width_in": 9, "height_in": 3}})
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ebay_orders.PIRATE_SHIP_COLUMNS
    row = dict(zip(rows[0], rows[1]))
    assert row["Recipient Name"] == "Jordan Buyer"
    assert row["Address 1"] == "1 Main St"
    assert row["Zipcode"] == "43004"
    assert row["Weight (oz)"] == "20.5"  # 1 lb + 4.5 oz
    assert (row["Length (in)"], row["Width (in)"], row["Height (in)"]) == ("12", "9", "3")
    assert "Levi's" in row["Item Title"]


def test_pirate_ship_csv_skips_orders_without_an_address():
    order = ebay_orders._order_to_dict(RAW_ORDER)
    order["ship_to"] = {}
    text = ebay_orders.pirate_ship_csv([order])
    assert len(list(csv.reader(io.StringIO(text)))) == 1  # header only


def test_pirate_ship_csv_missing_package_leaves_cells_blank():
    order = ebay_orders._order_to_dict(RAW_ORDER)
    text = ebay_orders.pirate_ship_csv([order])
    rows = list(csv.reader(io.StringIO(text)))
    row = dict(zip(rows[0], rows[1]))
    assert row["Weight (oz)"] == "" and row["Length (in)"] == ""

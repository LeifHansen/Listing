"""The shipment EasyPost is asked to rate is the package the seller described.

Pure shaping, captured off the request body: the dialog collects pounds and
ounces and EasyPost wants ounces; dimensions are optional but only as a set;
the label is asked for as a 4x6 PDF; and the address fields are renamed the
way EasyPost spells them. The refusals here happen BEFORE any network call,
because EasyPost would refuse them less clearly.
"""
from __future__ import annotations

import pytest

from backend.services import easypost

ORDER = {"order_id": "12-345", "ship_to": {
    "name": "Jordan Buyer", "company": "", "email": "j@example.com",
    "phone": "5551234567", "address1": "1 Main St", "address2": "Apt 2",
    "city": "Columbus", "state": "OH", "postal_code": "43004", "country": "US"}}
SHIP_FROM = {"name": "Seller", "address1": "9 Elm St", "city": "Austin",
             "state": "TX", "postal_code": "78701", "country": "US",
             "phone": "5125550100"}
PACKAGE = {"weight_lb": 1, "weight_oz": 4.5, "length_in": 12, "width_in": 9,
           "height_in": 3}


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = ""
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture()
def sent(monkeypatch):
    """Capture the body EasyPost would receive; answer with `rates`."""
    box = {"rates": [], "messages": []}

    def _post(url, headers=None, json=None, timeout=None):
        box["url"] = url
        box["body"] = json
        box["headers"] = headers
        return _Resp({"id": "shp_1", "rates": box["rates"],
                      "messages": box["messages"]})
    monkeypatch.setattr(easypost.httpx, "post", _post)
    return box


def _shipment(sent):
    return sent["body"]["shipment"]


def test_weight_goes_out_in_ounces_with_dimensions_as_a_set(sent):
    easypost.create_shipment("EZTKkey", ORDER, PACKAGE, SHIP_FROM)
    parcel = _shipment(sent)["parcel"]
    assert parcel["weight"] == 20.5  # 1 lb + 4.5 oz
    assert (parcel["length"], parcel["width"], parcel["height"]) == (12, 9, 3)


def test_dimensions_are_left_out_unless_all_three_are_given(sent):
    easypost.create_shipment("EZTKkey", ORDER,
                             {"weight_oz": 8, "length_in": 12, "width_in": 9},
                             SHIP_FROM)
    assert _shipment(sent)["parcel"] == {"weight": 8.0}


def test_the_label_is_asked_for_as_a_printable_pdf_stamped_with_the_order(sent):
    easypost.create_shipment("EZTKkey", ORDER, PACKAGE, SHIP_FROM)
    ship = _shipment(sent)
    assert ship["options"]["label_format"] == "PDF"
    assert ship["options"]["label_size"] == "4x6"
    assert ship["options"]["invoice_number"] == "12-345"
    assert ship["reference"] == "12-345"


def test_addresses_are_renamed_the_way_easypost_spells_them(sent):
    easypost.create_shipment("EZTKkey", ORDER, PACKAGE, SHIP_FROM)
    to = _shipment(sent)["to_address"]
    assert to["street1"] == "1 Main St" and to["street2"] == "Apt 2"
    assert to["zip"] == "43004" and to["state"] == "OH"
    assert to["phone"] == "5551234567" and to["email"] == "j@example.com"
    assert "company" not in to  # blanks are omitted, not sent as ""
    frm = _shipment(sent)["from_address"]
    assert frm["street1"] == "9 Elm St" and frm["zip"] == "78701"
    assert frm["country"] == "US"


def test_the_key_rides_as_a_bearer_token(sent):
    easypost.create_shipment("EZTKkey", ORDER, PACKAGE, SHIP_FROM)
    assert sent["headers"]["Authorization"] == "Bearer EZTKkey"
    assert sent["url"].endswith("/v2/shipments")


def test_rates_come_back_cheapest_first(sent):
    sent["rates"] = [
        {"id": "r_up", "carrier": "UPS", "service": "Ground", "rate": "9.10",
         "currency": "USD", "delivery_days": 4},
        {"id": "r_us", "carrier": "USPS", "service": "GroundAdvantage",
         "rate": "6.07", "currency": "USD", "delivery_days": 3},
    ]
    got = easypost.create_shipment("EZTKkey", ORDER, PACKAGE, SHIP_FROM)
    assert got["shipment_id"] == "shp_1"
    assert [r["rate_id"] for r in got["rates"]] == ["r_us", "r_up"]
    assert got["rates"][0]["cost"] == "6.07"
    assert got["rates"][0]["delivery_days"] == 3


def test_carrier_messages_are_kept_when_nothing_could_be_rated(sent):
    sent["messages"] = [{"carrier": "UPS", "type": "rate_error",
                         "message": "phone number required"}]
    got = easypost.create_shipment("EZTKkey", ORDER, PACKAGE, SHIP_FROM)
    assert got["rates"] == []
    assert got["messages"] == [{"carrier": "UPS", "message": "phone number required"}]


# ------------------------------------------------ refused before any network

@pytest.fixture()
def no_network(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("a refusal reached the network")
    monkeypatch.setattr(easypost.httpx, "post", _boom)


def test_no_weight_is_refused_first(no_network):
    with pytest.raises(easypost.EasyPostError, match="weight"):
        easypost.create_shipment("EZTKkey", ORDER, {"weight_lb": 0}, SHIP_FROM)


def test_an_order_without_an_address_is_refused_first(no_network):
    with pytest.raises(easypost.EasyPostError, match="no ship-to address"):
        easypost.create_shipment("EZTKkey", {"order_id": "1", "ship_to": {}},
                                 PACKAGE, SHIP_FROM)


def test_a_blank_ship_from_is_refused_first(no_network):
    with pytest.raises(easypost.EasyPostError, match="ship-from"):
        easypost.create_shipment("EZTKkey", ORDER, PACKAGE, {"postal_code": "78701"})


def test_an_international_order_is_refused_first(no_network):
    """Customs forms are not filled in here; better to say so than to let a
    carrier refuse it with a code."""
    abroad = {**ORDER, "ship_to": {**ORDER["ship_to"], "country": "CA"}}
    with pytest.raises(easypost.EasyPostError, match="customs"):
        easypost.create_shipment("EZTKkey", abroad, PACKAGE, SHIP_FROM)


def test_the_key_shape_is_checked_locally():
    assert easypost.key_looks_valid("EZTK" + "a" * 30)
    assert easypost.key_looks_valid("EZAK" + "a" * 30)
    assert not easypost.key_looks_valid("hunter2")
    assert not easypost.key_looks_valid("EZTK")
    assert easypost.key_is_test("EZTK" + "a" * 30)
    assert not easypost.key_is_test("EZAK" + "a" * 30)

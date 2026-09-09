"""One order, one label -- however many times the button is pressed.

A double-click, a retry after a toast, "Ship it" pressed again from the bell:
every one of them reaches the purchase route for an order that already has a
label. The route answers with THAT label and never asks EasyPost for another.
A purchase whose answer was lost is settled against EasyPost first, and a
seller who asks only "did that go through?" is told no without an error.

The same record scopes a shipment id to its owner: another seller's shipment
id is a 404 here, never a lookup at EasyPost.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient

BOUGHT = {
    "shipment_id": "shp_1", "purchased": True, "tracking_number": "9400111",
    "label_url": "https://files/label.pdf", "carrier": "USPS",
    "service": "GroundAdvantage", "cost": "6.07", "currency": "USD",
    "tracker_url": "", "ebay_carrier": "USPS",
}


@pytest.fixture()
def shop(dbmod, monkeypatch):
    from backend import main

    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": "u1"})
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {
        "access_token": "tok", "_uid": "u1", "ebay_username": "seller_x",
        "ship_from_postal": ""})
    monkeypatch.setattr(main, "_easypost_key", lambda request: ("u1", "EZTKkey"))
    calls = {"buy": 0}

    def _buy(key, shipment_id, rate_id):
        calls["buy"] += 1
        return dict(BOUGHT)
    monkeypatch.setattr(main.easypost, "buy_label", _buy)
    monkeypatch.setattr(main.ebay_orders, "mark_shipped",
                        lambda *a, **k: {"ok": True})
    return main, TestClient(main.app), calls


def _buy(client, **over):
    body = {"order_id": "o1", "shipment_id": "shp_1", "rate_id": "rate_1"}
    body.update(over)
    return client.post("/api/easypost/label", json=body)


def test_a_second_press_returns_the_first_label(shop):
    main, client, calls = shop
    first = _buy(client).json()
    again = _buy(client, shipment_id="shp_2", rate_id="rate_9").json()
    assert calls["buy"] == 1
    assert again["shipment_id"] == first["shipment_id"] == "shp_1"
    assert again["tracking_number"] == "9400111"
    assert len(main.db.labels_for_order("u1", "o1")) == 1


def test_a_purchase_in_doubt_is_settled_before_anything_new_is_bought(shop, monkeypatch):
    main, client, calls = shop
    main.db.create_shipping_label("u1", order_id="o1", shipment_id="shp_old",
                                  rate_id="rate_old", status="buying")
    monkeypatch.setattr(main.easypost, "retrieve_shipment",
                        lambda key, sid: {**BOUGHT, "shipment_id": sid})
    body = _buy(client, shipment_id="shp_new", rate_id="rate_new").json()
    assert calls["buy"] == 0
    assert body["shipment_id"] == "shp_old"
    assert body["status"] == "bought"


def test_asking_only_whether_it_went_through_is_not_an_error(shop, monkeypatch):
    """The dialog reopens on a "buying" row and asks, with no rate chosen."""
    main, client, calls = shop
    main.db.create_shipping_label("u1", order_id="o1", shipment_id="shp_old",
                                  rate_id="rate_old", status="buying")
    monkeypatch.setattr(main.easypost, "retrieve_shipment",
                        lambda key, sid: {**BOUGHT, "purchased": False})
    res = client.post("/api/easypost/label", json={"order_id": "o1"})
    assert res.status_code == 200
    assert res.json()["status"] == "not_bought"
    assert calls["buy"] == 0
    # ...and the doubt is closed, so the next open goes straight to rates.
    assert main.db.labels_for_order("u1", "o1")[0]["status"] == "abandoned"


def test_another_sellers_shipment_is_a_404_here_not_a_lookup_at_easypost(shop, monkeypatch):
    main, client, calls = shop
    main.db.create_shipping_label("u2", order_id="o9", shipment_id="shp_theirs",
                                  rate_id="r", status="bought")

    def _never(*a, **k):
        raise AssertionError("EasyPost was asked about a label that isn't ours")
    monkeypatch.setattr(main.easypost, "refund_label", _never)
    assert client.post("/api/easypost/label/shp_theirs/refund").status_code == 404
    # An id of no shape at all is the same answer, with no network behind it.
    assert client.post(f"/api/easypost/label/{'A' * 200}/refund").status_code == 404


def test_voiding_our_own_label_records_it(shop, monkeypatch):
    main, client, calls = shop
    _buy(client)
    monkeypatch.setattr(main.easypost, "refund_label",
                        lambda key, sid: {"refund_status": "submitted"})
    res = client.post("/api/easypost/label/shp_1/refund")
    assert res.status_code == 200
    assert res.json()["refund_status"] == "submitted"
    assert main.db.labels_for_order("u1", "o1")[0]["status"] == "refund_requested"

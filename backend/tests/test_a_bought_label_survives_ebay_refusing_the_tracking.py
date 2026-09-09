"""A label the seller paid for is shown to them whatever eBay says next.

The purchase route does two things in a row: buy the label from EasyPost, then
tell eBay the tracking number. The second can fail after the first succeeded
-- eBay down, the scope missing, the order already marked from Seller Hub --
and raising then would hide a label the seller just paid for. So the answer is
always the label, with `ebay_marked` saying whether eBay took the tracking and
a reason when it did not, and the dialog offers the retry.

The other order of failure is the money one: a purchase whose ANSWER was lost.
The row written before the buy says "we may have paid for this", and the route
asks EasyPost before it says anything -- bought, the label is settled and
shown; not bought, the seller is told to reopen, never that nothing happened.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend.services import easypost, ebay_orders

BOUGHT = {
    "shipment_id": "shp_1", "purchased": True, "tracking_number": "9400111",
    "label_url": "https://files/label.pdf", "carrier": "FedExDefault",
    "service": "Ground", "cost": "8.10", "currency": "USD",
    "tracker_url": "https://track/x", "ebay_carrier": "FedEx",
}


@pytest.fixture()
def shop(dbmod, monkeypatch):
    """A connected seller with a real (SQLite) labels table, EasyPost and
    eBay both stubbed on their modules."""
    from backend import main

    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": "u1"})
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {
        "access_token": "tok", "_uid": "u1", "ebay_username": "seller_x",
        "ship_from_postal": ""})
    monkeypatch.setattr(main, "_easypost_key", lambda request: ("u1", "EZTKkey"))
    calls = {"buy": 0, "mark": []}

    def _buy(key, shipment_id, rate_id):
        calls["buy"] += 1
        return dict(BOUGHT)
    monkeypatch.setattr(main.easypost, "buy_label", _buy)

    def _mark(token, order_id, tracking, carrier, line_items=None):
        calls["mark"].append((order_id, tracking, carrier))
        return {"ok": True, "order_id": order_id}
    monkeypatch.setattr(main.ebay_orders, "mark_shipped", _mark)
    return main, TestClient(main.app), calls


def _buy(client, **over):
    body = {"order_id": "o1", "shipment_id": "shp_1", "rate_id": "rate_1"}
    body.update(over)
    return client.post("/api/easypost/label", json=body)


def test_the_happy_path_buys_then_tells_ebay_in_ebays_spelling(shop):
    main, client, calls = shop
    res = _buy(client)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "bought"
    assert body["tracking_number"] == "9400111"
    assert body["label_url"] == "https://files/label.pdf"
    assert body["ebay_marked"] is True and body["ebay_error"] == ""
    assert calls["mark"] == [("o1", "9400111", "FedEx")]
    # And it is on record.
    rows = main.db.labels_for_order("u1", "o1")
    assert [r["status"] for r in rows] == ["bought"]
    assert rows[0]["ebay_marked"] is True


def test_ebay_refusing_the_tracking_does_not_hide_the_label(shop, monkeypatch):
    main, client, calls = shop

    def _refuse(*a, **k):
        raise ebay_orders.OrdersError("eBay rejected the tracking number (400)")
    monkeypatch.setattr(main.ebay_orders, "mark_shipped", _refuse)
    res = _buy(client)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tracking_number"] == "9400111"
    assert body["ebay_marked"] is False
    assert "rejected the tracking" in body["ebay_error"]
    assert body["ebay_outcome_unknown"] is False
    row = main.db.labels_for_order("u1", "o1")[0]
    assert row["status"] == "bought" and row["ebay_marked"] is False
    assert "rejected" in row["ebay_error"]


def test_ebay_going_quiet_is_flagged_so_the_retry_is_careful(shop, monkeypatch):
    main, client, calls = shop

    def _lost(*a, **k):
        raise ebay_orders.UnknownOutcome("We lost contact with eBay while marking …")
    monkeypatch.setattr(main.ebay_orders, "mark_shipped", _lost)
    body = _buy(client).json()
    assert body["ebay_marked"] is False
    assert body["ebay_outcome_unknown"] is True


def test_a_lost_purchase_answer_is_settled_against_easypost(shop, monkeypatch):
    """EasyPost knows whether it took the money. Ask it, then say so."""
    main, client, calls = shop

    def _lost(key, shipment_id, rate_id):
        raise easypost.UnknownOutcome(easypost._BUY_UNKNOWN)
    monkeypatch.setattr(main.easypost, "buy_label", _lost)
    monkeypatch.setattr(main.easypost, "retrieve_shipment",
                        lambda key, sid: dict(BOUGHT))
    res = _buy(client)
    assert res.status_code == 200, res.text
    assert res.json()["tracking_number"] == "9400111"
    assert calls["mark"] == [("o1", "9400111", "FedEx")]
    assert main.db.labels_for_order("u1", "o1")[0]["status"] == "bought"


def test_a_lost_answer_that_cannot_be_settled_says_reopen_not_nothing(shop, monkeypatch):
    main, client, calls = shop

    def _lost(key, shipment_id, rate_id):
        raise easypost.UnknownOutcome(easypost._BUY_UNKNOWN)
    monkeypatch.setattr(main.easypost, "buy_label", _lost)

    def _down(key, sid):
        raise easypost.EasyPostError("Couldn't reach EasyPost: timed out")
    monkeypatch.setattr(main.easypost, "retrieve_shipment", _down)
    res = _buy(client)
    assert res.status_code == 502
    assert "before buying again" in res.json()["detail"]
    assert calls["mark"] == []
    # The row stays as the question it is, for the next open to answer.
    assert main.db.labels_for_order("u1", "o1")[0]["status"] == "buying"


def test_easyposts_refusal_buys_nothing_and_tells_ebay_nothing(shop, monkeypatch):
    main, client, calls = shop

    def _refuse(key, shipment_id, rate_id):
        raise easypost.EasyPostError("EasyPost couldn't buy the label (422): insufficient funds")
    monkeypatch.setattr(main.easypost, "buy_label", _refuse)
    res = _buy(client)
    assert res.status_code == 502
    assert "insufficient funds" in res.json()["detail"]
    assert calls["mark"] == []
    # Not left as a purchase in doubt.
    assert main.db.labels_for_order("u1", "o1")[0]["status"] == "refused"


def test_the_row_goes_in_before_the_money_and_stops_the_buy_when_it_cannot(shop, monkeypatch):
    main, client, calls = shop

    def _no_db(uid, **fields):
        raise main.db.StorageUnavailable("We couldn't record this label just now.")
    monkeypatch.setattr(main.db, "create_shipping_label", _no_db)
    res = _buy(client)
    assert res.status_code == 503
    assert calls["buy"] == 0


def test_the_retry_route_marks_the_label_as_taken_by_ebay(shop, monkeypatch):
    main, client, calls = shop

    def _refuse(*a, **k):
        raise ebay_orders.OrdersError("eBay rejected the tracking number (400)")
    monkeypatch.setattr(main.ebay_orders, "mark_shipped", _refuse)
    _buy(client)
    monkeypatch.setattr(main.ebay_orders, "mark_shipped",
                        lambda *a, **k: {"ok": True, "order_id": "o1"})
    res = client.post("/api/ebay/mark-shipped", json={
        "order_id": "o1", "tracking_number": "9400111", "carrier": "FedEx"})
    assert res.status_code == 200
    assert main.db.labels_for_order("u1", "o1")[0]["ebay_marked"] is True

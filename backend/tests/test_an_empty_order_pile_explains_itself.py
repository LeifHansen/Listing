""""No orders are waiting to ship" has to say which kind of empty it is.

The seller who reported it had sold something. Three different facts produce
the same empty list: every order in eBay's 90-day window is already shipped;
the eBay account connected here is not the one that sold; or the server is
on eBay's sandbox, which has no real orders. The dialog said the same
sentence for all three, so nothing could be diagnosed from the screen.

Now an empty pile costs one more read -- eBay's own count of EVERY order in
the window -- and the answer carries that count, the account it looked at and
the environment. A full pile costs nothing extra.
"""
from __future__ import annotations

import logging

import pytest

# The route half imports backend.main, which needs the full image (the
# AI client, Pillow); CI's minimal job skips this file and the smoke job
# runs it, like every other route test here.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main
from backend.services import ebay_orders


# ------------------------------------------------------- the service read

def test_orders_total_reads_ebays_count(monkeypatch):
    asked = {}

    def _get(token, path, params=None):
        asked["path"], asked["params"] = path, params
        return {"total": 7, "orders": [{}]}
    monkeypatch.setattr(ebay_orders, "_get", _get)
    assert ebay_orders.orders_total("tok") == 7
    assert asked["path"].endswith("/order")
    assert asked["params"] == {"limit": "1"}  # no status filter: every order


def test_orders_total_is_never_invented(monkeypatch):
    monkeypatch.setattr(ebay_orders, "_get", lambda *a, **k: {"orders": []})
    assert ebay_orders.orders_total("tok") is None


# ------------------------------------------------------------- the route

@pytest.fixture()
def seller(monkeypatch):
    monkeypatch.setattr(main.auth, "current_user", lambda request: {"id": "u1"})
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {
        "access_token": "tok", "_uid": "u1", "ebay_username": "seller_x",
        "ship_from_postal": ""})
    monkeypatch.setattr(main.config, "EBAY_ENV", "production")
    monkeypatch.setattr(main, "_attach_packages", lambda uid, orders: orders)
    monkeypatch.setattr(main.db, "labels_for_orders",
                        lambda uid, ids: {i: [] for i in ids})
    return TestClient(main.app)


def _pile(monkeypatch, count, total=None):
    orders = [{"order_id": f"o{i}", "line_items": [], "ship_to": {}}
              for i in range(count)]
    monkeypatch.setattr(ebay_orders, "awaiting_page", lambda token, limit=50: {
        "orders": orders, "total": total if total is not None else count,
        "partial": False})


def test_an_empty_pile_says_how_many_orders_there_were(seller, monkeypatch, caplog):
    _pile(monkeypatch, 0)
    monkeypatch.setattr(ebay_orders, "orders_total", lambda token: 12)
    caplog.set_level(logging.INFO, logger="thryft")
    body = seller.get("/api/ebay/orders").json()
    assert body["orders"] == []
    assert body["recent_total"] == 12
    assert body["ebay_username"] == "seller_x"
    assert body["env"] == "production"
    assert any("awaiting pile empty" in r.getMessage() and "12" in r.getMessage()
               for r in caplog.records)


def test_a_full_pile_does_not_pay_for_the_count(seller, monkeypatch):
    _pile(monkeypatch, 3)

    def _never(token):
        raise AssertionError("counted orders for a pile that was not empty")
    monkeypatch.setattr(ebay_orders, "orders_total", _never)
    body = seller.get("/api/ebay/orders").json()
    assert len(body["orders"]) == 3
    assert body["recent_total"] is None
    assert body["ebay_username"] == "seller_x"


def test_a_failed_count_does_not_cost_the_list(seller, monkeypatch):
    """The list is the answer; the count is a courtesy."""
    _pile(monkeypatch, 0)

    def _fail(token):
        raise ebay_orders.OrdersError("eBay returned 500 reading orders.")
    monkeypatch.setattr(ebay_orders, "orders_total", _fail)
    res = seller.get("/api/ebay/orders")
    assert res.status_code == 200
    assert res.json()["recent_total"] is None


def test_the_environment_rides_along_so_sandbox_can_be_named(seller, monkeypatch):
    _pile(monkeypatch, 0)
    monkeypatch.setattr(ebay_orders, "orders_total", lambda token: 0)
    monkeypatch.setattr(main.config, "EBAY_ENV", "sandbox")
    assert seller.get("/api/ebay/orders").json()["env"] == "sandbox"

"""Sales made in Seller Hub come in — and are only edited here if they fit.

The other direction of the sync. A seller who set up a sale in Seller Hub (or
the eBay app) sees it on the Sales screen and on their cards; one built with
eBay's richer options — different discounts for different listings, an
amount off, listings picked by rule — is shown read-only, with the reason,
rather than flattened by the next edit made here.

The pull is a mirror, so it also has to let go: a sale deleted in Seller Hub
leaves, a sale made here keeps its "made here" whatever Seller Hub says, a
failed pull leaves what was known on screen marked as unchecked (never an
empty list that reads as "no sales"), and a different eBay account's sales
are not shown to the one now connected.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.services import sale_events
from backend.tests._fake_markdowns import FakeMarkdowns


@pytest.fixture()
def shop(dbmod, monkeypatch):
    creds = {"access_token": "tok", "ebay_username": "seller1"}
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: dict(creds))
    ebay = FakeMarkdowns()
    monkeypatch.setattr(sale_events, "ebay_markdowns", ebay)
    sale_events._FRESH.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup", json={"email": "hub@example.com",
                                                 "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("hub@example.com")["id"]
    return client, dbmod, uid, ebay, creds


def _events(client, **params):
    r = client.get("/api/sale-events", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_simple_seller_hub_sale_comes_in_editable(shop):
    client, _db, _uid, ebay, _creds = shop
    ebay.seed("555", name="Weekend sale", percent="25", listing_ids=["111", "222"])
    body = _events(client)
    [event] = body["events"]
    assert event["name"] == "Weekend sale"
    assert event["percent_off"] == 25.0
    assert event["source"] == "ebay" and event["editable"] is True
    assert event["item_ids"] == ["111", "222"] and event["count"] == 2
    assert body["checked"] is True
    assert body["limits"]["max_percent"] >= body["limits"]["min_percent"]


def test_a_rule_based_sale_is_read_only_and_its_listings_come_from_ebay(shop):
    client, _db, _uid, ebay, _creds = shop
    ebay.seed("556", name="All tees", tiers=[{
        "discountBenefit": {"percentageOffItem": "10"},
        "inventoryCriterion": {"inventoryCriterionType": "INVENTORY_BY_RULE",
                               "ruleCriteria": {"selectionRules": []}}}])
    ebay.rule_sets["556"] = ["111", "333"]
    [event] = _events(client)["events"]
    assert event["editable"] is False
    assert "rule" in event["read_only_reason"]
    assert event["item_ids"] == ["111", "333"]
    assert event["seller_hub_url"].startswith("https://")


def test_an_amount_off_is_shown_with_no_percentage(shop):
    client, _db, _uid, ebay, _creds = shop
    ebay.seed("557", tiers=[{
        "discountBenefit": {"amountOffItem": {"value": "5.00", "currency": "USD"}},
        "inventoryCriterion": {"inventoryCriterionType": "INVENTORY_BY_VALUE",
                               "listingIds": ["111"]}}])
    [event] = _events(client)["events"]
    assert event["editable"] is False and event["percent_off"] is None
    assert "amount off" in event["read_only_reason"].lower()


def test_a_sale_deleted_in_seller_hub_leaves(shop):
    client, _db, _uid, ebay, _creds = shop
    ebay.seed("555")
    ebay.seed("556")
    assert len(_events(client)["events"]) == 2
    del ebay.promos["556"]
    body = client.post("/api/sale-events/refresh", json={"force": True}).json()
    assert [e["ebay_promotion_id"] for e in body["events"]] == ["555"]


def test_a_sale_made_here_stays_made_here(shop):
    client, dbmod, uid, ebay, _creds = shop
    assert dbmod.upsert_listing("a", {"title": "A", "price": 40.0,
                                      "ebay_listing_id": "111", "source": "ebay"},
                                status="published", user_id=uid)
    made = client.post("/api/sale-events", json={"percent": 20, "days": 7,
                                                 "listing_ids": ["a"]}).json()["event"]
    body = client.post("/api/sale-events/refresh", json={"force": True}).json()
    [event] = body["events"]
    assert event["id"] == made["id"] and event["source"] == "app"


def test_the_pull_is_not_repeated_inside_its_window(shop):
    client, _db, _uid, ebay, _creds = shop
    ebay.seed("555")
    _events(client)
    _events(client)
    assert [c for c in ebay.calls if c[0] == "list"] == [("list",)]
    client.post("/api/sale-events/refresh", json={"force": True})
    assert len([c for c in ebay.calls if c[0] == "list"]) == 2


def test_a_finished_sale_is_not_fetched_again(shop):
    client, _db, _uid, ebay, _creds = shop
    ebay.seed("555", status="ENDED", end="2026-11-20T00:00:00.000Z")
    _events(client)
    ebay.calls.clear()
    client.post("/api/sale-events/refresh", json={"force": True})
    assert ("get", "555") not in ebay.calls


def test_a_pull_that_fails_keeps_what_was_known_and_says_so(shop):
    client, _db, _uid, ebay, _creds = shop
    ebay.seed("555")
    assert len(_events(client)["events"]) == 1
    ebay.down = True
    body = client.post("/api/sale-events/refresh", json={"force": True}).json()
    assert body["checked"] is False
    assert len(body["events"]) == 1


def test_another_ebay_accounts_sales_are_not_shown(shop, monkeypatch):
    client, _db, _uid, ebay, creds = shop
    ebay.seed("555", name="Old store's sale")
    assert len(_events(client)["events"]) == 1
    creds["ebay_username"] = "seller2"           # a different store connected
    ebay.promos.clear()
    body = _events(client)
    assert body["events"] == []


def test_without_ebay_there_are_no_sales_to_show(shop, monkeypatch):
    client, _db, _uid, _ebay, _creds = shop
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: None)
    body = _events(client)
    assert body["connected"] is False and body["events"] == []


def test_it_needs_a_seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    assert TestClient(main.app).get("/api/sale-events").status_code == 401

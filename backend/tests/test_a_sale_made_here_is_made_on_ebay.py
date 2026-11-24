"""A sale made on the Manage tab is made on eBay — and kept honest there.

Through the real routes and a real schema, with eBay's markdown calls stood in
for by an in-memory eBay that applies eBay's rules (_fake_markdowns). What is
pinned is what the seller would see go wrong:

  * "Apply discount…" over the ticks makes ONE eBay sale of exactly those
    listings, named for what it is, and records it from eBay's answer;
  * a listing that cannot go in (an auction, not live, another seller's,
    already in a sale, refused by eBay) is reported in words, and the rest go;
  * adding to a sale re-reads it from eBay first, so listings added in Seller
    Hub since are never dropped by the PUT;
  * taking listings out does one PUT per sale, and a sale left empty is ended;
  * ending early cancels a sale that has not started and cuts a running one
    short;
  * a running sale's discount cannot be changed — eBay's rule, said up front;
  * Lower prices stays off listings in a sale (a price change takes a listing
    out of its sale on eBay);
  * the sales go with the account.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.services import sale_events
from backend.tests._fake_markdowns import FakeMarkdowns

CREDS = {"access_token": "tok", "ebay_username": "seller1"}


@pytest.fixture()
def shop(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: dict(CREDS))
    ebay = FakeMarkdowns()
    monkeypatch.setattr(sale_events, "ebay_markdowns", ebay)
    sale_events._FRESH.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup", json={"email": "sales@example.com",
                                                 "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("sales@example.com")["id"]
    return client, dbmod, uid, ebay


def _stock(dbmod, uid, rid, item, **listing):
    assert dbmod.upsert_listing(rid, {"title": f"Item {rid}", "price": 40.0,
                                      "ebay_listing_id": item, "source": "ebay",
                                      "image_urls": [f"https://i.ebayimg.com/{rid}.jpg"],
                                      **listing},
                                status="published", user_id=uid)


def _sale(client, ids, **body):
    return client.post("/api/sale-events", json={"percent": 20, "days": 7,
                                                 "listing_ids": ids, **body})


def test_apply_discount_makes_one_ebay_sale_of_the_ticks(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "a", "111")
    _stock(dbmod, uid, "b", "222")
    _stock(dbmod, uid, "c", "333")

    r = _sale(client, ["a", "b"])
    assert r.status_code == 200, r.text
    body = r.json()
    [create] = [c for c in ebay.calls if c[0] == "create"]
    assert create[1] == ["111", "222"]
    event = body["event"]
    assert event["source"] == "app" and event["editable"] is True
    assert event["percent_off"] == 20.0
    assert event["name"].startswith("20% off · 7 days")
    assert event["count"] == 2
    assert body["report"]["changed"] == 2
    assert [e["id"] for e in body["events"]] == [event["id"]]
    pid = event["ebay_promotion_id"]
    assert ebay.promos[pid]["promotionImageUrl"].startswith("https://i.ebayimg.com/")


def test_what_cannot_go_in_is_reported_and_the_rest_go(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "ok", "111")
    _stock(dbmod, uid, "auction", "222", listing_format="AUCTION")
    _stock(dbmod, uid, "refused", "333")
    assert dbmod.upsert_listing("draft", {"title": "d"}, status="draft", user_id=uid)
    other = dbmod.create_user("other-sale", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", {"title": "t", "ebay_listing_id": "999"},
                                status="published", user_id=other["id"])
    ebay.refuse["333"] = "This listing's price changed in the last 14 days."

    body = _sale(client, ["ok", "auction", "refused", "draft", "theirs"]).json()
    report = body["report"]
    assert report["changed"] == 1
    reasons = {r["listing_id"]: r["message"] for r in report["results"]["skipped"]}
    assert "Auctions can't go in a sale" in reasons["auction"]
    assert reasons["refused"] == "This listing's price changed in the last 14 days."
    assert reasons["draft"] == "No longer live on eBay."
    assert reasons["theirs"] == "Listing not found."
    assert ebay.ids_in(body["event"]["ebay_promotion_id"]) == ["111"]


def test_a_listing_in_another_sale_is_skipped_unless_it_is_moved(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "a", "111")
    _stock(dbmod, uid, "b", "222")
    first = _sale(client, ["a"], name="Spring").json()["event"]

    skipped = _sale(client, ["a", "b"], name="Summer").json()
    reason = skipped["report"]["results"]["skipped"][0]["message"]
    assert "already in “Spring”" in reason
    assert ebay.ids_in(skipped["event"]["ebay_promotion_id"]) == ["222"]

    sale_events._FRESH.clear()
    moved = _sale(client, ["a"], name="Autumn", move=True).json()
    assert moved["report"]["changed"] == 1
    assert moved["report"]["results"]["changed"][0]["moved_from"] == "Spring"
    assert ebay.ids_in(moved["event"]["ebay_promotion_id"]) == ["111"]
    # Spring had nothing else in it, so it was ended rather than left empty.
    spring = ebay.promos.get(first["ebay_promotion_id"])
    assert spring is None or "111" not in ebay.ids_in(first["ebay_promotion_id"])


def test_adding_to_a_sale_keeps_what_seller_hub_added(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "a", "111")
    _stock(dbmod, uid, "b", "222")
    event = _sale(client, ["a"]).json()["event"]
    pid = event["ebay_promotion_id"]
    # The seller adds a listing in Seller Hub; our row has not heard.
    ebay.promos[pid]["selectedInventoryDiscounts"][0]["inventoryCriterion"]["listingIds"].append("777")

    r = client.post(f"/api/sale-events/{event['id']}/listings", json={"listing_ids": ["b"]})
    assert r.status_code == 200, r.text
    assert ebay.ids_in(pid) == ["111", "777", "222"]
    assert r.json()["event"]["count"] == 3


def test_taking_listings_out_is_one_put_per_sale_and_an_empty_sale_ends(shop):
    client, dbmod, uid, ebay = shop
    for rid, item in (("a", "111"), ("b", "222"), ("c", "333")):
        _stock(dbmod, uid, rid, item)
    one = _sale(client, ["a", "b"], name="One").json()["event"]
    two = _sale(client, ["c"], name="Two").json()["event"]
    ebay.calls.clear()

    r = client.post("/api/sale-events/remove-listings", json={"listing_ids": ["a", "c"]})
    assert r.status_code == 200, r.text
    assert r.json()["report"]["changed"] == 2
    assert ebay.ids_in(one["ebay_promotion_id"]) == ["222"]
    # "Two" held only c: a scheduled sale left with nothing is cancelled.
    assert two["ebay_promotion_id"] not in ebay.promos
    updates = [c for c in ebay.calls if c[0] == "update"]
    assert len(updates) == 1


def test_a_listing_not_in_a_sale_is_said_to_be_so(shop):
    client, dbmod, uid, _ebay = shop
    _stock(dbmod, uid, "a", "111")
    body = client.post("/api/sale-events/remove-listings", json={"listing_ids": ["a"]}).json()
    assert body["report"]["results"]["skipped"][0]["message"] == "It isn't in a sale."


def test_ending_a_sale_that_has_not_started_cancels_it(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "a", "111")
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    event = _sale(client, ["a"], start_at=future).json()["event"]
    assert event["state"] == "scheduled"

    r = client.post(f"/api/sale-events/{event['id']}/end")
    assert r.status_code == 200, r.text
    assert event["ebay_promotion_id"] not in ebay.promos
    assert r.json()["events"] == []


def test_ending_a_running_sale_cuts_it_short(shop):
    client, dbmod, uid, ebay = shop
    pid = ebay.seed("555", name="Hub sale", listing_ids=["111"])
    _stock(dbmod, uid, "a", "111")
    event = client.get("/api/sale-events").json()["events"][0]
    assert event["state"] == "running" and event["editable"]

    client.post(f"/api/sale-events/{event['id']}/end")
    end = datetime.fromisoformat(ebay.promos[pid]["endDate"].replace("Z", "+00:00"))
    assert end - datetime.now(timezone.utc) < timedelta(minutes=10)
    assert ebay.promos[pid]["selectedInventoryDiscounts"][0]["inventoryCriterion"]["listingIds"] == ["111"]


def test_a_running_sales_discount_cannot_change(shop):
    client, _dbmod, _uid, ebay = shop
    ebay.seed("555", listing_ids=["111"], percent="20")
    event = client.get("/api/sale-events").json()["events"][0]
    r = client.patch(f"/api/sale-events/{event['id']}", json={"percent": 30})
    assert r.status_code == 400
    assert "once it has started" in r.json()["detail"]
    # The end date still can.
    later = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    assert client.patch(f"/api/sale-events/{event['id']}",
                        json={"end_at": later}).status_code == 200


def test_a_seller_hub_sale_with_tiers_is_never_written(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "a", "111")
    tier = lambda ids: {"discountBenefit": {"percentageOffItem": "10"},  # noqa: E731
                        "inventoryCriterion": {"inventoryCriterionType": "INVENTORY_BY_VALUE",
                                               "listingIds": ids}}
    ebay.seed("555", tiers=[tier(["111"]), tier(["222"])])
    event = client.get("/api/sale-events").json()["events"][0]
    assert event["editable"] is False
    ebay.calls.clear()
    assert client.post(f"/api/sale-events/{event['id']}/end").status_code == 400
    assert client.post(f"/api/sale-events/{event['id']}/listings",
                       json={"listing_ids": ["a"]}).status_code == 400
    body = client.post("/api/sale-events/remove-listings", json={"listing_ids": ["a"]}).json()
    assert "managed in Seller Hub" in body["report"]["results"]["skipped"][0]["message"]
    assert ebay.writes() == []


def test_a_sale_names_exactly_one_set_of_listings(shop):
    client, _dbmod, _uid, _ebay = shop
    r = client.post("/api/sale-events", json={"percent": 20, "days": 7})
    assert r.status_code == 400
    r = client.post("/api/sale-events", json={"percent": 20, "days": 7,
                                              "listing_ids": ["a"], "all_active": True})
    assert r.status_code == 400


def test_a_store_category_sale_reaches_the_shelf(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "tee", "111", store_category_id="11")
    _stock(dbmod, uid, "hat", "222", store_category_id="12")
    body = client.post("/api/sale-events", json={"percent": 15, "days": 3,
                                                 "store_category_id": "11"}).json()
    assert ebay.ids_in(body["event"]["ebay_promotion_id"]) == ["111"]


def test_a_percentage_ebay_would_refuse_is_refused_before_asking(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "a", "111")
    assert _sale(client, ["a"], percent=90).status_code == 400
    assert _sale(client, ["a"], days=99).status_code == 400
    assert ebay.writes() == []


def test_an_old_connection_is_told_to_reconnect(shop):
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "a", "111")
    ebay.scope_error = True
    body = client.get("/api/sale-events").json()
    assert body["needs_reconnect"] is True and body["checked"] is False


def test_lower_prices_leaves_a_listing_in_a_sale_alone(shop, monkeypatch):
    import time
    client, dbmod, uid, ebay = shop
    _stock(dbmod, uid, "a", "111")
    _stock(dbmod, uid, "b", "222")
    _sale(client, ["a"], name="Holiday")
    main._REPRICE_JOBS.clear()

    revised = []

    class _Ebay:
        def publish(self, ctx, creds):
            from backend.marketplaces.base import PublishOutcome
            revised.append(ctx.session_id)
            return PublishOutcome(ok=True, message="ok", status="published")
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _Ebay())

    job = client.post("/api/ebay/lower-prices",
                      json={"percent": 10, "listing_ids": ["a", "b"]}).json()["job_id"]
    for _ in range(500):
        status = client.get(f"/api/bulk/status/{job}").json()
        if status.get("done"):
            break
        time.sleep(0.02)
    result = status["result"]
    assert revised == ["b"]
    [skip] = result["results"]["skipped"]
    assert skip["listing_id"] == "a" and "“Holiday”" in skip["message"]


def test_the_sales_go_with_the_account(shop):
    client, dbmod, uid, ebay = shop
    ebay.seed("555", listing_ids=["111"])
    assert client.get("/api/sale-events").json()["events"]
    assert client.post("/api/ebay/disconnect").status_code == 200
    assert dbmod.list_sale_events(uid) == []

    ebay.seed("556", listing_ids=["112"])
    sale_events._FRESH.clear()
    client.get("/api/sale-events")
    assert dbmod.list_sale_events(uid)
    assert dbmod.delete_user(uid) is not None
    assert dbmod.list_sale_events(uid) == []

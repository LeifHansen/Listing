"""A listing moved into a sale that cannot take it stays in the sale it was in.

"Add to sale…" with "move" takes each listing out of its old sale and then
puts it in the new one. It used to take them out FIRST and only then read the
new sale and check it could take them -- managed in Seller Hub, or already
full -- so a refusal left the listings in no sale at all, and an old sale
emptied by the move had already been ended. Nor was anything put back when
eBay then refused the PUT, which "New sale" has always done.

Now the new sale is checked before anything leaves the old one, and a listing
that went out but did not go in is put back where it was.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main, ratelimit  # noqa: E402
from backend.services import ebay_markdowns as real  # noqa: E402
from backend.services import sale_events  # noqa: E402
from backend.tests._fake_markdowns import FakeMarkdowns  # noqa: E402

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
    assert client.post("/api/auth/signup", json={"email": "moves@example.com",
                                                 "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("moves@example.com")["id"]
    for rid, item in (("a", "111"), ("b", "222"), ("c", "333"), ("e", "555")):
        assert dbmod.upsert_listing(
            rid, {"title": f"Item {rid}", "price": 40.0, "ebay_listing_id": item,
                  "source": "ebay", "image_urls": [f"https://i.ebayimg.com/{rid}.jpg"]},
            status="published", user_id=uid)
    return client, ebay


def _sale(client, ids, name):
    r = client.post("/api/sale-events", json={"percent": 20, "days": 7,
                                              "listing_ids": ids, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["event"]


def test_a_full_sale_is_refused_before_anything_leaves_the_old_one(shop):
    client, ebay = shop
    spring = _sale(client, ["a", "c"], "Spring")
    summer = _sale(client, ["b"], "Summer")
    ebay.MAX_LISTINGS = 1  # Summer is full
    sale_events._FRESH.clear()
    before = len(ebay.writes())

    r = client.post(f"/api/sale-events/{summer['id']}/listings",
                    json={"listing_ids": ["a"], "move": True})
    assert r.status_code == 400 and "at most 1" in r.json()["detail"]
    assert ebay.writes()[before:] == []  # nothing was taken out of Spring
    assert "111" in ebay.ids_in(spring["ebay_promotion_id"])


def test_a_listing_ebay_refuses_in_the_new_sale_goes_back_to_the_old_one(shop):
    client, ebay = shop
    spring = _sale(client, ["a", "c", "e"], "Spring")
    summer = _sale(client, ["b"], "Summer")
    sale_events._FRESH.clear()
    summer_pid = summer["ebay_promotion_id"]
    put = ebay.update

    def _refuse_111_in_summer(creds, pid, body, client=None):
        if pid == summer_pid and "111" in real._ids_in(body):
            raise real.MarkdownRefused("eBay won't put that listing on sale.",
                                       38204, ["111"])
        return put(creds, pid, body, client=client)

    ebay.update = _refuse_111_in_summer

    r = client.post(f"/api/sale-events/{summer['id']}/listings",
                    json={"listing_ids": ["a", "e"], "move": True})
    assert r.status_code == 200, r.text
    assert ebay.ids_in(summer_pid) == ["222", "555"]
    # "a" went out of Spring and could not go in: it is back, not nowhere.
    assert sorted(ebay.ids_in(spring["ebay_promotion_id"])) == ["111", "333"]

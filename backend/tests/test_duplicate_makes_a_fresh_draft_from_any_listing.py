"""Duplicate: a second of the same thing, as a brand-new draft.

"Sell similar" for the seller holding another one. The copy carries what
describes the ITEM -- title, price, condition, specifics, description,
shipping, photos -- and nothing that belongs to the first listing's life on
a marketplace: no eBay item id, no SKU, no source, no per-marketplace state
(a relist deliberately keeps a live Etsy copy; a duplicate is a second item
and must not claim the first one's Etsy listing), no video, no sync
bookkeeping, no AI verdict, no sale. The original is left exactly as it was.

Photos come from disk when they are there and from R2 when housekeeping has
freed the local copy -- relist reads only the disk, which is right for a
sale (selling purges the photos everywhere) and wrong here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, objstore, ratelimit, storage


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "dup@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("dup@example.com")["id"]
    # No bucket in tests: uploads are a no-op and a restore finds nothing.
    monkeypatch.setattr(objstore, "upload_optimized", lambda *a, **k: None)
    monkeypatch.setattr(objstore, "restore", lambda key, path: False)
    return client, dbmod, uid


LIVE = {
    "title": "Levi's 501 jeans 34x32", "description": "Dark wash.", "price": 45.0,
    "purchase_price": 8.0, "quantity": 1, "images": ["a.jpg", "b.jpg"],
    "condition": "USED_GOOD", "condition_description": "Light wear.",
    "category_id": "11483", "category_suggestion": "Clothing > Men > Jeans",
    "item_specifics": [{"name": "Size", "value": "34x32", "confidence": "high"}],
    "package_weight_lb": 1, "package_weight_oz": 4, "fulfillment_policy_id": "fp-1",
    # Everything that belongs to THIS listing's life on eBay and elsewhere.
    "source": "ebay", "sku": "SKU-1", "ebay_listing_id": "111",
    "ebay_start_time": "2026-09-01T00:00:00Z", "view_url": "https://www.ebay.com/itm/111",
    "watch_count": 12, "sold_quantity": 1, "sold_price": 40.0, "sold_at": "2026-09-20",
    "ended_at": "2026-09-21", "price_lowered_at": "2026-09-10", "offer_sent_at": "2026-09-11",
    "marketplaces": {"etsy": {"listing_id": "e-9", "status": "live"}},
    "videos": [{"file": "clip.mp4", "on_ebay": True, "status": "LIVE"}],
    "dirty_fields": ["price"], "remote_shadow": {"price": 50.0},
    "conflicts": {"title": {"ours": "a", "theirs": "b"}},
    "ai_confidence": "low",
}


def _put(dbmod, uid, rid, listing, status="published", photos=("a.jpg", "b.jpg")):
    d = storage.optimized_dir(rid)
    d.mkdir(parents=True, exist_ok=True)
    for name in photos:
        (d / name).write_bytes(b"jpeg-" + name.encode())
    assert dbmod.upsert_listing(rid, listing, status=status, user_id=uid)
    return rid


def test_a_live_listing_becomes_a_fresh_draft_with_its_identity_stripped(seller):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "own-1", LIVE)

    resp = client.post(f"/api/listings/{rid}/duplicate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    new_id = body["id"]
    assert new_id != rid and body["from"] == rid and body["photos"] == 2

    row = dbmod.get_listing(new_id)
    assert row["status"] == "draft" and row["user_id"] == uid
    copy = row["listing"]
    # What describes the item rides along.
    for key in ("title", "description", "price", "purchase_price", "condition",
                "condition_description", "category_id", "category_suggestion",
                "item_specifics", "package_weight_lb", "package_weight_oz",
                "fulfillment_policy_id"):
        assert copy[key] == LIVE[key], key
    # What belongs to the first listing's life does not.
    assert copy["ebay_listing_id"] == "" and copy["sku"] == "" and copy["source"] == ""
    assert copy["view_url"] == "" and copy["ebay_start_time"] == ""
    assert copy["watch_count"] == 0 and copy["sold_quantity"] == 0
    assert copy["sold_price"] is None and copy["sold_at"] == "" and copy["ended_at"] == ""
    assert copy["price_lowered_at"] == "" and copy["offer_sent_at"] == ""
    assert copy["marketplaces"] == {}          # NOT carried, unlike a relist
    assert copy["videos"] == []
    assert copy["dirty_fields"] == [] and copy["remote_shadow"] == {} and copy["conflicts"] == {}
    assert copy["ai_confidence"] == ""
    # The photos are copies, in the listing's order, and the originals stay.
    assert copy["images"] == ["a.jpg", "b.jpg"]
    assert (storage.optimized_dir(new_id) / "b.jpg").read_bytes() == b"jpeg-b.jpg"
    assert (storage.optimized_dir(rid) / "a.jpg").is_file()
    original = dbmod.get_listing(rid)
    assert original["status"] == "published"
    assert original["listing"]["ebay_listing_id"] == "111"
    assert original["listing"]["marketplaces"]["etsy"]["listing_id"] == "e-9"


def test_a_photo_only_on_r2_is_fetched_for_the_copy(seller, monkeypatch):
    client, dbmod, uid = seller
    # Housekeeping freed b.jpg locally; the bucket still has it.
    rid = _put(dbmod, uid, "own-2", {**LIVE, "image_urls": ["https://i.ebayimg.com/x.jpg"]},
               photos=("a.jpg",))
    asked = []

    def restore(key, path: Path):
        asked.append(key)
        if key.endswith("/b.jpg"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"from-r2")
            return True
        return False
    monkeypatch.setattr(objstore, "restore", restore)

    body = client.post(f"/api/listings/{rid}/duplicate").json()
    assert body["photos"] == 2
    copy = dbmod.get_listing(body["id"])["listing"]
    assert copy["images"] == ["a.jpg", "b.jpg"]
    assert (storage.optimized_dir(body["id"]) / "b.jpg").read_bytes() == b"from-r2"
    # Asked the bucket only for the photo the disk did not have, under the
    # ORIGINAL listing's key.
    assert asked == [objstore.key_for(rid, "b.jpg")]
    # The files are ours now: the eBay-hosted fallback strip is dropped.
    assert copy["image_urls"] == []


def test_a_photo_found_nowhere_is_skipped_rather_than_fatal(seller):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "own-3", LIVE, photos=("a.jpg",))
    body = client.post(f"/api/listings/{rid}/duplicate").json()
    assert body["photos"] == 1
    assert dbmod.get_listing(body["id"])["listing"]["images"] == ["a.jpg"]


def test_a_draft_duplicates_too_and_keeps_ebay_hosted_photos_when_none_copied(seller):
    client, dbmod, uid = seller
    rid = _put(dbmod, uid, "draft-1", {
        "title": "Imported hat", "price": 12.0, "images": [],
        "image_urls": ["https://i.ebayimg.com/hat.jpg"], "source": "ebay",
    }, status="draft", photos=())
    body = client.post(f"/api/listings/{rid}/duplicate").json()
    copy = dbmod.get_listing(body["id"])["listing"]
    assert copy["images"] == []
    # Nothing was copied, so the eBay strip is all the draft has: keep it.
    assert copy["image_urls"] == ["https://i.ebayimg.com/hat.jpg"]
    assert body["photos"] == 1
    assert copy["source"] == ""


def test_someone_elses_listing_and_a_missing_one_are_both_not_found(seller, dbmod):
    client, _, uid = seller
    assert dbmod.upsert_listing("theirs", {"title": "x"}, status="draft",
                                user_id="someone-else")
    assert client.post("/api/listings/theirs/duplicate").status_code == 404
    assert client.post("/api/listings/nope/duplicate").status_code == 404
    assert client.post("/api/listings/theirs/duplicate").status_code == 404
    assert len(dbmod.list_listings(limit=50, user_id=uid)) == 0

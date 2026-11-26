"""Two writes that took a whole listing and kept the wrong half of it.

  * Relist (and End & relist) copied the old item's sync bookkeeping into the
    new draft: its conflicts, the remote shadow the next sync diffs against,
    the offer stamp that silences offer nudges, and video entries naming
    files the copy never made. The duplicate path already cleared them.
  * Shop Mode's "Buy" saved the client's copy wholesale as "unlisted". A
    second tap on an item since published blanked its eBay id and moved it
    back -- and the next publish made a duplicate live listing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main, ratelimit  # noqa: E402

UID = "u-copy"


def test_a_relist_starts_without_the_old_items_bookkeeping(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main, "_save_new_draft", lambda *a, **k: None)
    rec = {"id": "old1", "status": "sold", "listing": {
        "title": "Lamp", "price": 20.0,
        "conflicts": {"title": {"local": "a", "remote": "b"}},
        "remote_shadow": {"title": "Lamp"}, "dirty_fields": ["price"],
        "offer_sent_at": "2026-11-01T00:00:00+00:00",
        "publish_error": "eBay said no",
        "videos": [{"file": "v.mp4", "ebay_video_id": "V1"}]}}

    _new_id, listing, _copied = main._clone_listing("old1", rec, UID)

    assert listing.title == "Lamp" and listing.price == 20.0
    assert listing.conflicts == {} and listing.remote_shadow == {}
    assert listing.dirty_fields == [] and listing.offer_sent_at == ""
    assert listing.publish_error == "" and listing.videos == []


def test_a_second_buy_tap_does_not_unpublish_the_item(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup", json={
        "email": "shop@example.com", "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("shop@example.com")["id"]
    assert dbmod.upsert_listing("scan1", {
        "title": "Lamp", "ebay_listing_id": "1234567890", "source": "",
        "marketplaces": {"ebay": {"status": "published",
                                  "listing_id": "1234567890"}}},
        status="published", user_id=uid)

    # The Shop Mode screen still holds the copy from before it was published.
    res = client.post("/api/inventory/add", json={
        "session_id": "scan1", "listing": {"title": "Lamp"}})

    assert res.status_code == 200, res.text[:200]
    rec = dbmod.get_listing("scan1")
    assert rec["status"] == "published"
    assert rec["listing"]["ebay_listing_id"] == "1234567890"

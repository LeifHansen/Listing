"""A sold listing is an archive record, not a draft.

Two rules keep it that way, and both matter for the same reason: the sold
record is the only place the app remembers what one finished sale was.

- POST /api/publish refuses it. Republishing in place would overwrite that
  history with a second listing's life, and for an imported item it asks eBay
  to revise an item that has already ended.
- POST /api/listings/{id}/relist is the way to sell another one: a NEW draft
  carrying the copy, the specifics and whatever photos survived the sale --
  with every field describing the finished sale cleared, and the sold record
  left untouched.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import db, main  # noqa: E402

SOLD = {
    "title": "1990s basketball card lot",
    "brand": "Topps",
    "price": 29.99,
    "purchase_price": 4.0,
    "description": "A big pile of cardboard.",
    "item_specifics": [{"name": "Sport", "value": "Basketball", "confidence": "high"}],
    "images": ["img_000.jpg"],
    # Everything below describes THIS sale / THIS eBay item.
    "ebay_listing_id": "123456789012",
    "sku": "thryft-s1",
    "source": "ebay",
    "ebay_start_time": "2026-06-01T00:00:00Z",
    "view_url": "https://www.ebay.com/itm/123456789012",
    "watch_count": 7,
    "sold_quantity": 1,
    "sold_price": 24.0,
    "sold_at": "2026-08-24T00:00:00Z",
    "marketplaces": {"ebay": {"listing_id": "123456789012", "status": "published"}},
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """A stand-in listing store plus a real (temporary) photo directory --
    these tests are about the endpoints' rules, and about the photos actually
    being copied rather than moved."""
    rows = {"s1": {"id": "s1", "status": "sold", "user_id": "u1",
                   "listing": dict(SOLD)}}
    written: dict[str, dict] = {}

    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main, "_uid", lambda request: "u1")
    monkeypatch.setattr(db, "get_listing", lambda sid: rows.get(sid))
    monkeypatch.setattr(main.db, "get_listing", lambda sid: rows.get(sid))

    def _upsert(sid, listing, status="draft", user_id=None, **kw):
        written[sid] = {"listing": listing, "status": status, "user_id": user_id}
        return True

    monkeypatch.setattr(main.db, "upsert_listing", _upsert)
    monkeypatch.setattr(main.storage, "save_listing", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "new_session_id", lambda: "s2")
    monkeypatch.setattr(main.objstore, "upload_optimized", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "optimized_dir",
                        lambda sid: tmp_path / sid / "optimized")

    src = tmp_path / "s1" / "optimized"
    src.mkdir(parents=True)
    (src / "img_000.jpg").write_bytes(b"jpeg-bytes")

    c = TestClient(main.app)
    c.rows, c.written, c.photos = rows, written, tmp_path
    return c


def _publish(client, session_id="s1"):
    return client.post("/api/publish", json={
        "session_id": session_id, "mode": "live", "listing": dict(SOLD)})


def test_publishing_a_sold_listing_is_refused(client):
    res = _publish(client)
    assert res.status_code == 409
    assert "sold" in res.json()["detail"].lower()
    # And the refusal is total: nothing was written back to the record.
    assert client.written == {}


def test_the_refusal_names_the_way_forward(client):
    """A dead end would just send the seller back to Publish Live. The
    message has to say where the action moved to."""
    assert "relist" in _publish(client).json()["detail"].lower()


def test_a_draft_is_still_publishable(client):
    """The guard is about SOLD, not about every settled record: an ended
    listing still relists in place from the Inactive tab."""
    client.rows["s1"]["status"] = "ended"
    assert _publish(client).status_code != 409


def test_relist_makes_a_new_draft(client):
    res = client.post("/api/listings/s1/relist")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "s2" and body["from"] == "s1"
    assert client.written["s2"]["status"] == "draft"
    assert client.written["s2"]["user_id"] == "u1"


def test_the_copy_keeps_what_describes_the_ITEM(client):
    copy = client.post("/api/listings/s1/relist").json()["listing"]
    assert copy["title"] == SOLD["title"]
    assert copy["brand"] == "Topps"
    assert copy["price"] == 29.99
    assert copy["purchase_price"] == 4.0
    assert copy["description"] == SOLD["description"]
    assert [s["name"] for s in copy["item_specifics"]] == ["Sport"]


def test_the_copy_drops_everything_that_describes_the_SALE(client):
    """The bug this whole endpoint exists to avoid: a copy that still carries
    the sold item's eBay id would have the next publish try to revise a
    finished listing, and would show last sale's numbers on a fresh draft."""
    copy = client.post("/api/listings/s1/relist").json()["listing"]
    assert copy["ebay_listing_id"] == ""
    assert copy["sku"] == ""
    assert copy["source"] == ""
    assert copy["view_url"] == ""
    assert copy["ebay_start_time"] == ""
    assert copy["watch_count"] == 0
    assert copy["sold_quantity"] == 0
    assert copy["sold_price"] is None
    assert copy["sold_at"] == ""
    assert copy["marketplaces"] == {}


def test_photos_are_copied_not_moved(client):
    """The sold record is an archive: a relist must not strip it of the
    photos it still has."""
    body = client.post("/api/listings/s1/relist").json()
    assert body["listing"]["images"] == ["img_000.jpg"]
    assert body["photos"] == 1
    assert (client.photos / "s2" / "optimized" / "img_000.jpg").is_file()
    assert (client.photos / "s1" / "optimized" / "img_000.jpg").is_file()


def test_a_sale_that_purged_its_photos_still_relists(client):
    """Selling PURGES the session's images to reclaim storage, so this is the
    normal case for an app-created listing -- it has to produce a usable
    draft (with `photos: 0` saying what the seller has to add) rather than a
    draft claiming photos that aren't there."""
    (client.photos / "s1" / "optimized" / "img_000.jpg").unlink()
    body = client.post("/api/listings/s1/relist").json()
    assert body["listing"]["images"] == []
    assert body["photos"] == 0


def test_ebay_hosted_photos_survive_the_copy(client):
    """An imported listing's photos live on eBay, not on our disk -- those
    the copy can carry as they are."""
    client.rows["s1"]["listing"]["image_urls"] = ["https://i.ebayimg.com/a.jpg"]
    (client.photos / "s1" / "optimized" / "img_000.jpg").unlink()
    body = client.post("/api/listings/s1/relist").json()
    assert body["listing"]["image_urls"] == ["https://i.ebayimg.com/a.jpg"]
    assert body["photos"] == 1


def test_the_sold_record_is_left_alone(client):
    client.post("/api/listings/s1/relist")
    assert "s1" not in client.written
    assert client.rows["s1"]["status"] == "sold"
    assert client.rows["s1"]["listing"]["sold_price"] == 24.0


def test_relisting_someone_elses_listing_is_a_404(client, monkeypatch):
    client.rows["s1"]["user_id"] = "u2"
    assert client.post("/api/listings/s1/relist").status_code == 404


def test_relisting_a_missing_listing_is_a_404(client):
    assert client.post("/api/listings/nope/relist").status_code == 404

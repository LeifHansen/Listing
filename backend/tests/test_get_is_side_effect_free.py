"""Reading a listing must not spend the seller's time, storage or quota.

Opening an imported eBay listing performed, inline in a GET: up to 24 outbound
HTTPS downloads from ebayimg, writes of up to 48 image files plus a manifest, a
fire-and-forget R2 upload thread, a database row write and a listing.json
write.

A GET that mutates is not a style complaint. It means a prefetch, a retry, a
crawler, a link preview or a double-click each start the same expensive work;
that two opens race each other over the same directory; and that a read can
fail, or bill storage, for reasons the seller never asked for. `backend/
storage.py` already states the rule this violated.

The photos still have to arrive before the editor can work on them — eBay's
own URLs are not editable here. So the work is unchanged; what changes is that
the seller asks for it. Reads show the eBay-hosted photos, and preparing a
listing for editing is a command.
"""
from __future__ import annotations

import pytest

# Importing backend.main pulls the whole app in. The `checks` job installs
# neither of these, so it skips this file; the smoke job's "API tests" step is
# where it runs, and that step fails on a skip so this can never quietly stop
# running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

LISTING_ID = "ebay-110000000001"


@pytest.fixture()
def client(monkeypatch):
    from backend import db, main, objstore, storage
    from backend.services import image_import

    rec = {
        "id": LISTING_ID, "user_id": "u1", "status": "published",
        "listing": {"title": "Blue lamp", "source": "ebay", "images": [],
                    "image_urls": ["https://i.ebayimg.com/1.jpg",
                                   "https://i.ebayimg.com/2.jpg"]},
    }
    effects = {"downloads": 0, "uploads": 0, "writes": 0}

    def _download(lid, urls):
        effects["downloads"] += 1
        return ["img_000.jpg", "img_001.jpg"]

    monkeypatch.setattr(image_import, "import_listing_images", _download)
    monkeypatch.setattr(storage, "list_optimized", lambda _lid: [])
    monkeypatch.setattr(storage, "save_listing", lambda *_a, **_k: None)
    monkeypatch.setattr(objstore, "upload_optimized",
                        lambda *_a, **_k: effects.__setitem__(
                            "uploads", effects["uploads"] + 1))
    monkeypatch.setattr(db, "get_listing", lambda _lid: rec)
    monkeypatch.setattr(db, "upsert_listing",
                        lambda *_a, **_k: effects.__setitem__(
                            "writes", effects["writes"] + 1))
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_in_background",
                        lambda fn, *a, what="", **k: fn(*a, **k))

    return TestClient(main.app), effects


def test_reading_a_listing_downloads_nothing(client):
    """The finding itself: a plain GET pulled every remote photo."""
    api, effects = client
    resp = api.get(f"/api/listings/{LISTING_ID}")

    assert resp.status_code == 200
    assert effects["downloads"] == 0, "a read downloaded the listing's photos"


def test_reading_a_listing_writes_nothing(client):
    api, effects = client
    api.get(f"/api/listings/{LISTING_ID}")

    assert effects["writes"] == 0, "a read wrote the database row"
    assert effects["uploads"] == 0, "a read started an R2 upload"


def test_reading_still_returns_the_ebay_hosted_photos(client):
    """Nothing is hidden by making the read passive — the listing's photos are
    shown from eBay's URLs, read-only, exactly as the record holds them."""
    api, _ = client
    body = api.get(f"/api/listings/{LISTING_ID}").json()

    assert body["listing"]["image_urls"] == ["https://i.ebayimg.com/1.jpg",
                                             "https://i.ebayimg.com/2.jpg"]


def test_repeated_reads_stay_free(client):
    """Prefetches, retries, crawlers and double-clicks all land here."""
    api, effects = client
    for _ in range(5):
        api.get(f"/api/listings/{LISTING_ID}")

    assert effects == {"downloads": 0, "uploads": 0, "writes": 0}


# ------------------------------------------------- the explicit command

def test_preparing_for_editing_does_the_work(client):
    """The same work as before, asked for. Without this the photos never
    become editable and the fix would just be a removed feature."""
    api, effects = client
    resp = api.post(f"/api/listings/{LISTING_ID}/prepare-for-editing")

    assert resp.status_code == 200, resp.text
    assert effects["downloads"] == 1
    assert effects["writes"] == 1
    assert effects["uploads"] == 1
    assert resp.json()["images"] == ["img_000.jpg", "img_001.jpg"]


def test_preparing_twice_does_not_redownload(client, monkeypatch):
    """Idempotent: a listing already prepared is reported as ready rather than
    fetched again."""
    from backend import storage

    api, effects = client
    monkeypatch.setattr(storage, "list_optimized",
                        lambda _lid: ["img_000.jpg", "img_001.jpg"])

    api.post(f"/api/listings/{LISTING_ID}/prepare-for-editing")
    assert effects["downloads"] == 0


def test_preparing_someone_elses_listing_is_refused(client, monkeypatch):
    """It spends storage and makes outbound requests, so it needs the same
    ownership check as any other write."""
    from backend import main

    api, effects = client
    monkeypatch.setattr(main, "_uid", lambda _r: "somebody-else")

    resp = api.post(f"/api/listings/{LISTING_ID}/prepare-for-editing")

    assert resp.status_code == 404
    assert effects["downloads"] == 0

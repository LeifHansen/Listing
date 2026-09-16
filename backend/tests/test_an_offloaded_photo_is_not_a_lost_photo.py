"""A photo the volume let go is still the seller's photo.

The app does not keep every photo on its own disk for ever. Once R2 is
configured the reclaim pass verifies a file is in the bucket and then unlinks
the local copy (main._offload_to_r2), because the volume only needs a photo
while someone is working on the listing. /media has always known this and
redirects to the bucket, so VIEWING an offloaded photo never broke.

The AI fills did not know it. All three — the dashboard's "Finish all"
(_enrich_one), /api/enrich and /api/autofill-specifics — built their file list
with a bare `is_file()` filter and, finding nothing, answered:

    This listing's photos aren't on the server anymore.

Which was a sentence about our infrastructure, not the seller's listing. It
told them where a file wasn't, proposed nothing they could do, and was not
true: the photos were in the bucket the whole time. A seller pressing "Finish
all" on a store of any age got it back nineteen listings at a time, under a
heading that said those listings "still need you" — for work that needed
nothing from them at all, on a button whose entire promise is that the details
get filled in and pushed to eBay without further action.

So: ask all three places a photo can be before anyone is allowed to say it is
gone. The volume, then the bucket, then — for an imported listing, whose push
to the bucket is best-effort and may never have landed — eBay itself, which is
still serving those photos on the live page.

These tests are about the fills. The edits (crop, rotate, straighten) already
rehydrated via main._ensure_local; that is the behaviour being extended here,
not invented.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.models import ItemSpecific


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {"access_token": "t"})
    monkeypatch.setattr(main, "_resolve_category", lambda listing: None)
    main._ENRICH_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "offload@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("offload@example.com")["id"]
    return client, dbmod, uid


class _AcceptingEbay:
    def __init__(self):
        self.sent = []

    def publish(self, ctx, creds):
        from backend.marketplaces.base import PublishOutcome
        self.sent.append(ctx)
        return PublishOutcome(ok=True, message="Revised.", status="published")


def _record(rid: str, **over) -> dict:
    return {"title": f"Item {rid}", "category_id": "11450",
            "images": ["img_000.jpg"], "missing_info": ["size"], **over}


def _photo(rid: str, name: str = "img_000.jpg"):
    from PIL import Image
    path = main.storage.optimized_dir(rid) / name
    Image.new("RGB", (8, 8), "white").save(path, "JPEG")
    return path


def _offload(rid: str, name: str = "img_000.jpg") -> None:
    """What the reclaim pass does: the file is in the bucket, so the local
    copy goes. The record keeps the name — it is still a photo of the item."""
    (main.storage.optimized_dir(rid) / name).unlink()


def _fills_one(seen: list):
    def _fill(listing, paths):
        seen.append([p.name for p in paths])
        listing.item_specifics.append(ItemSpecific(name="Size", value="M",
                                                   confidence="high"))
        return 1
    return _fill


def _finish(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished")


def _bucket_holding(monkeypatch, rid: str, name: str = "img_000.jpg"):
    """R2, standing in: it holds this one photo and will hand it back."""
    monkeypatch.setattr(main.objstore, "enabled", lambda: True)
    key = f"{rid}/{name}"
    monkeypatch.setattr(main.objstore, "key_for",
                        lambda s, n, kind="optimized": f"{s}/{n}")
    monkeypatch.setattr(main.objstore, "exists", lambda k: k == key)
    monkeypatch.setattr(main.objstore, "upload_optimized", lambda *a, **k: None)

    def _restore(k, path):
        if k != key:
            return False
        _photo(rid, path.name)
        return True
    monkeypatch.setattr(main.objstore, "restore", _restore)


def _empty_bucket(monkeypatch):
    monkeypatch.setattr(main.objstore, "enabled", lambda: True)
    monkeypatch.setattr(main.objstore, "key_for",
                        lambda s, n, kind="optimized": f"{s}/{n}")
    monkeypatch.setattr(main.objstore, "exists", lambda k: False)
    monkeypatch.setattr(main.objstore, "restore", lambda k, p: False)
    monkeypatch.setattr(main.objstore, "upload_optimized", lambda *a, **k: None)


# ------------------------------------------------------- the bulk finisher

def test_the_bulk_fill_pulls_an_offloaded_photo_back_instead_of_skipping(
        seller, monkeypatch):
    """The nineteen. Each of these listings was filled in and pushed to eBay
    the moment the fill stopped confusing "not on this disk" with "gone"."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("offload-aged", _record("offload-aged"),
                                status="published", user_id=uid)
    _photo("offload-aged")
    _offload("offload-aged")
    _bucket_holding(monkeypatch, "offload-aged")
    seen: list = []
    ebay = _AcceptingEbay()
    monkeypatch.setattr(main, "_enrich_listing", _fills_one(seen))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["offload-aged"]})
    result = _finish(client, started.json()["job_id"])

    assert result["changed"] == 1
    assert result["skipped"] == 0
    # It read the real file, and eBay got the revise.
    assert seen == [["img_000.jpg"]]
    assert len(ebay.sent) == 1


def test_a_listing_with_no_photo_anywhere_is_still_skipped(seller, monkeypatch):
    """The honest case has to survive the fix. A listing whose photos are gone
    from the volume, the bucket and eBay cannot be filled in by a pass that
    reads photos — and saying so is the point. What changes is the sentence:
    it names what the seller can do instead of where a file isn't."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("offload-bare", _record("offload-bare"),
                                status="published", user_id=uid)
    _empty_bucket(monkeypatch)
    monkeypatch.setattr(main, "_enrich_listing",
                        lambda listing, paths: pytest.fail("read nothing"))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["offload-bare"]})
    result = _finish(client, started.json()["job_id"])

    assert result["skipped"] == 1
    assert result["changed"] == 0
    message = result["results"]["skipped"][0]["message"]
    assert message == main._NO_PHOTOS
    assert "server" not in message.lower()


def test_an_offloaded_listing_is_not_billed_for_a_pass_that_never_ran(
        seller, monkeypatch):
    """The skip is taken before the charge, and stays there. A listing with no
    photo to read earns nothing, and a seller who pressed one button must not
    find AI credits spent on the listings it could not look at."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("offload-unbilled", _record("offload-unbilled"),
                                status="published", user_id=uid)
    _empty_bucket(monkeypatch)
    charged: list = []
    monkeypatch.setattr(main, "_charge_uid",
                        lambda *a, **k: charged.append(a) or None)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["offload-unbilled"]})
    assert _finish(client, started.json()["job_id"])["skipped"] == 1
    assert charged == []


# --------------------------------------------------- the last copy, on eBay

def test_an_imported_listing_falls_back_to_the_photos_ebay_is_still_serving(
        seller, monkeypatch):
    """An imported listing's push to the bucket rides on a background thread
    and is best-effort, so "offloaded" and "never uploaded" look identical
    afterwards: names on the record, no file behind them, nothing in R2. eBay
    is still showing those photos on the live page. Download them again."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing(
        "offload-imported",
        _record("offload-imported", source="ebay",
                image_urls=["https://i.ebayimg.com/images/g/abc/s-l1600.jpg"]),
        status="published", user_id=uid)
    _empty_bucket(monkeypatch)
    pulled: list = []

    def _reimport(rid, urls):
        pulled.append((rid, list(urls)))
        _photo(rid, "img_000.jpg")
        return ["img_000.jpg"]
    monkeypatch.setattr(main.image_import, "import_listing_images", _reimport)
    seen: list = []
    ebay = _AcceptingEbay()
    monkeypatch.setattr(main, "_enrich_listing", _fills_one(seen))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["offload-imported"]})
    result = _finish(client, started.json()["job_id"])

    assert pulled == [("offload-imported",
                       ["https://i.ebayimg.com/images/g/abc/s-l1600.jpg"])]
    assert result["changed"] == 1
    assert seen == [["img_000.jpg"]]
    # And the re-downloaded names are what gets saved — not the stale ones the
    # fill started from, which is how the next press would re-download again.
    assert ebay.sent[0].listing.images == ["img_000.jpg"]


def test_the_bucket_is_tried_before_ebay(seller, monkeypatch):
    """R2 is one GET; eBay is up to 24 downloads and a re-encode of each. A
    photo in the bucket must never cost the seller the second one."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing(
        "offload-buckets-first",
        _record("offload-buckets-first", source="ebay",
                image_urls=["https://i.ebayimg.com/images/g/abc/s-l1600.jpg"]),
        status="published", user_id=uid)
    _photo("offload-buckets-first")
    _offload("offload-buckets-first")
    _bucket_holding(monkeypatch, "offload-buckets-first")
    monkeypatch.setattr(main.image_import, "import_listing_images",
                        lambda rid, urls: pytest.fail("went to eBay"))
    monkeypatch.setattr(main, "_enrich_listing", _fills_one([]))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["offload-buckets-first"]})
    assert _finish(client, started.json()["job_id"])["changed"] == 1


# --------------------------------------------------------- the single fills

def test_the_single_listing_fill_rehydrates_too(seller, monkeypatch):
    """/api/enrich is the same edit on one draft, and refused the same
    listings for the same wrong reason."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("offload-aged", _record("offload-aged"),
                                status="draft", user_id=uid)
    _photo("offload-aged")
    _offload("offload-aged")
    _bucket_holding(monkeypatch, "offload-aged")
    seen: list = []
    monkeypatch.setattr(main, "_enrich_listing", _fills_one(seen))

    res = client.post("/api/enrich/offload-aged",
                      json={"session_id": "offload-aged",
                            "listing": _record("offload-aged")})
    assert res.status_code == 200, res.text
    result = _finish(client, res.json()["job_id"])

    assert seen == [["img_000.jpg"]]
    assert result.get("added") == 1


def test_autofill_specifics_rehydrates_too(seller, monkeypatch):
    """The third door onto the same work, opened automatically when the editor
    loads a listing — so this one refused without anybody pressing anything."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("offload-aged", _record("offload-aged"),
                                status="draft", user_id=uid)
    _photo("offload-aged")
    _offload("offload-aged")
    _bucket_holding(monkeypatch, "offload-aged")
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.taxonomy, "item_aspects",
                        lambda cid: {"aspects": [{"localizedAspectName": "Size"}]})
    seen: list = []

    def _fill_aspects(paths, listing, aspects, tag_text=""):
        seen.append([p.name for p in paths])
        return [ItemSpecific(name="Size", value="M", confidence="high")]
    monkeypatch.setattr(main.claude_ai, "fill_aspects", _fill_aspects)
    monkeypatch.setattr(main, "_cover_remaining_specifics",
                        lambda listing, paths, aspects: 0)

    res = client.post("/api/autofill-specifics/offload-aged",
                      json={"session_id": "offload-aged",
                            "listing": _record("offload-aged")})
    assert res.status_code == 200, res.text
    assert seen == [["img_000.jpg"]]


def test_the_single_fill_says_what_to_do_when_there_is_truly_nothing(
        seller, monkeypatch):
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("offload-bare", _record("offload-bare"),
                                status="draft", user_id=uid)
    _empty_bucket(monkeypatch)

    res = client.post("/api/enrich/offload-bare",
                      json={"session_id": "offload-bare",
                            "listing": _record("offload-bare")})
    assert res.status_code == 400
    assert res.json()["detail"] == main._NO_PHOTOS


# ------------------------------------------------ what adoption may claim

def test_adoption_does_not_call_a_listing_ready_on_names_alone(monkeypatch,
                                                              dbmod):
    """`images` on the record and files on the disk are two different facts.
    Adoption returned the names the moment it saw the first, which is a claim
    about a listing whose photos the reclaim pass had taken away."""
    monkeypatch.setattr(main, "db", dbmod)
    _empty_bucket(monkeypatch)
    pulled: list = []

    def _reimport(rid, urls):
        pulled.append(rid)
        _photo(rid, "img_000.jpg")
        return ["img_000.jpg"]
    monkeypatch.setattr(main.image_import, "import_listing_images", _reimport)
    monkeypatch.setattr(main, "_in_background", lambda *a, **k: None)

    rec = {"listing": _record("offload-gone", source="ebay",
                              image_urls=["https://i.ebayimg.com/x.jpg"]),
           "status": "published", "user_id": "u"}
    assert main._adopt_imported_images("offload-gone", rec) == ["img_000.jpg"]
    assert pulled == ["offload-gone"]


def test_adoption_fetches_nothing_when_the_bucket_still_has_them(monkeypatch,
                                                                 dbmod):
    """The common path stays one HEAD. A listing whose photos are safely
    offloaded must not be re-downloaded from eBay every time it is opened."""
    monkeypatch.setattr(main, "db", dbmod)
    _bucket_holding(monkeypatch, "offload-safe")
    monkeypatch.setattr(main.image_import, "import_listing_images",
                        lambda rid, urls: pytest.fail("went to eBay"))

    rec = {"listing": _record("offload-safe", source="ebay",
                              image_urls=["https://i.ebayimg.com/x.jpg"]),
           "status": "published", "user_id": "u"}
    assert main._adopt_imported_images("offload-safe", rec) == ["img_000.jpg"]

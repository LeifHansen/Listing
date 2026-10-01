"""End and Relist: the old listing comes down, and the SAME item goes back up
as a new one with fresh copy — in that order, and never half way.

The Manage tab's bulk verb. A listing that has sat for months has lost the
search placement a new listing gets, and eBay's search has already shown
its title to everyone who did not buy. Relisting mints a new item id and a
new "listed" date; the AI-written title and description make it read as new.

What the run must get right, per listing:

  * The copy is made and the new words are written BEFORE anything is ended.
    The AI call is the step that fails, and it has to fail while the seller
    still has exactly what they had: the original live, and no draft left
    behind.
  * The original is ended and settled exactly as the End button settles it
    (kept under Inactive for the grace period), and only THEN is the copy
    published — as a create, from a draft carrying no item id, so eBay makes
    a new listing rather than reviving the one that just ended.
  * A refusal to end leaves the copy unpublished and removed: the seller
    must never end up with their own item live twice.
  * The photos ride along, copied not moved, and the facts do: price,
    specifics, condition. Only the title and the description are new.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit, storage
from backend.marketplaces.base import PublishOutcome
from backend.services import jobstore


class FakeEbay:
    """The eBay side of the run — EndItem and AddItem — scriptable per test,
    recording the order things happened in."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.end_error: Exception | None = None
        self.refuse_publish = False

    def end(self, token, listing):
        self.calls.append(("end", listing.ebay_listing_id))
        if self.end_error:
            raise self.end_error
        return {"ended": True, "listing_id": listing.ebay_listing_id}

    # The eBay provider, as End and Relist sees it.
    def publish(self, ctx, creds):
        listing = ctx.listing
        self.calls.append(("publish", ctx.session_id, ctx.mode,
                           listing.ebay_listing_id, listing.source,
                           listing.title, listing.description, listing.price,
                           list(listing.images), ctx.prev_record.get("status")))
        if self.refuse_publish:
            main.db.upsert_listing(ctx.session_id, listing.model_dump(),
                                   status="draft", user_id=ctx.uid)
            return PublishOutcome(ok=False, message="eBay refused it.",
                                  issues=[{"target": "category", "level": "error",
                                           "title": "Pick a category"}])
        listing.ebay_listing_id = "777"
        listing.source = "ebay"
        main.db.upsert_listing(ctx.session_id, listing.model_dump(),
                               status="published", user_id=ctx.uid)
        return PublishOutcome(ok=True, listing_id="777", status="published",
                              url="https://www.ebay.com/itm/777")


@pytest.fixture()
def seller(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "relist@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("relist@example.com")["id"]

    ebay = FakeEbay()
    monkeypatch.setattr(main.deps, "ebay_creds_for",
                        lambda request: {"access_token": "t", "ebay_username": "leif",
                                         "_uid": uid})
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main.listing_sync, "end", ebay.end)
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda key: ebay if key == "ebay" else None)
    monkeypatch.setattr(main.inventory_mirror, "on_ebay_finished",
                        lambda *a, **k: [])
    monkeypatch.setattr(main, "_adopt_imported_images", lambda *a, **k: [])
    fresh = {"title": "Levi's 501 Original Fit Jeans Men's 34x32 Dark Wash Denim",
             "description": "Levi's 501 in a dark wash. Key Details: ..."}
    monkeypatch.setattr(main.claude_ai, "fresh_copy", lambda listing: dict(fresh))
    jobstore._JOBS.clear()
    main._END_RELIST_JOBS.clear()
    return client, dbmod, uid, ebay


def _live(dbmod, uid, rid="own-1", title="Levis 501 jeans 34x32"):
    """One of the seller's own listings, live on eBay, with a photo on disk."""
    photos = storage.optimized_dir(rid)
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "a.jpg").write_bytes(b"jpeg")
    assert dbmod.upsert_listing(rid, {
        "title": title, "description": "Old words.", "price": 45.0,
        "quantity": 1, "images": ["a.jpg"], "source": "ebay",
        "ebay_listing_id": "111", "condition": "Pre-owned",
        "item_specifics": [{"name": "Size", "value": "34x32"}],
        "watch_count": 12, "view_url": "https://www.ebay.com/itm/111",
    }, status="published", user_id=uid)
    return rid


def _run(client, dbmod, uid, ids):
    resp = client.post("/api/ebay/end-and-relist", json={"listing_ids": ids})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]
    for _ in range(600):
        snap = jobstore.snapshot(job_id, uid) or {}
        if snap.get("done"):
            return snap
        time.sleep(0.01)
    raise AssertionError("the job never finished")


def _rows(dbmod, uid):
    return {r["id"]: r for r in dbmod.list_listings(limit=50, user_id=uid)}


def test_the_old_listing_ends_and_the_same_item_goes_up_new(seller):
    client, dbmod, uid, ebay = seller
    rid = _live(dbmod, uid)

    snap = _run(client, dbmod, uid, [rid])
    result = snap["result"]
    assert result["changed"] == 1 and result["failed"] == 0, result
    new_id = result["results"]["changed"][0]["new_id"]
    assert new_id != rid

    rows = _rows(dbmod, uid)
    # The original: ended and kept, as the End button keeps it.
    assert rows[rid]["status"] == "ended"
    assert rows[rid]["listing"]["title"] == "Levis 501 jeans 34x32"
    # The copy: live, with the new words and the old facts.
    new = rows[new_id]
    assert new["status"] == "published"
    assert new["listing"]["title"].startswith("Levi's 501 Original Fit")
    assert new["listing"]["description"].startswith("Levi's 501 in a dark wash")
    assert new["listing"]["price"] == 45.0
    assert [(s["name"], s["value"]) for s in new["listing"]["item_specifics"]] \
        == [("Size", "34x32")]
    assert new["listing"]["ebay_listing_id"] == "777"
    # Copied, not moved: both sessions hold the photo.
    assert (storage.optimized_dir(rid) / "a.jpg").is_file()
    assert (storage.optimized_dir(new_id) / "a.jpg").is_file()

    # End first, then a CREATE: the draft the provider was handed carries no
    # item id and no eBay source, so it cannot revise the item just ended.
    kinds = [c[0] for c in ebay.calls]
    assert kinds == ["end", "publish"]
    assert ebay.calls[0][1] == "111"
    _, sid, mode, item_id, source, title, _desc, price, images, prev = ebay.calls[1]
    assert (sid, mode, item_id, source, price, images, prev) == (
        new_id, "live", "", "", 45.0, ["a.jpg"], "draft")
    assert title.startswith("Levi's 501 Original Fit")


def test_an_ai_failure_costs_the_seller_nothing(seller, monkeypatch):
    """The words are written before anything is ended, so a failure there
    leaves the listing live and no draft behind."""
    client, dbmod, uid, ebay = seller
    rid = _live(dbmod, uid)

    def _fail(listing):
        raise RuntimeError("the AI response was too long and got cut off")

    monkeypatch.setattr(main.claude_ai, "fresh_copy", _fail)
    snap = _run(client, dbmod, uid, [rid])
    result = snap["result"]
    assert result["failed"] == 1 and result["changed"] == 0
    assert "new copy" in result["results"]["failed"][0]["message"]

    rows = _rows(dbmod, uid)
    assert set(rows) == {rid}, "a draft was left behind"
    assert rows[rid]["status"] == "published"
    assert ebay.calls == [], "eBay was touched over a failed rewrite"


def test_a_refused_ending_never_lists_the_item_twice(seller):
    client, dbmod, uid, ebay = seller
    rid = _live(dbmod, uid)
    ebay.end_error = ValueError("Auction has bids and cannot be ended.")

    snap = _run(client, dbmod, uid, [rid])
    result = snap["result"]
    assert result["failed"] == 1
    assert "Couldn't end it on eBay" in result["results"]["failed"][0]["message"]

    rows = _rows(dbmod, uid)
    assert set(rows) == {rid}
    assert rows[rid]["status"] == "published"
    assert [c[0] for c in ebay.calls] == ["end"], "it was published anyway"


def test_a_refused_publish_says_the_old_one_is_already_ended(seller):
    """The one failure that cannot be undone: eBay took the old listing down
    and would not take the new one. The draft with the fresh copy is kept,
    and the report says exactly that rather than 'failed'."""
    client, dbmod, uid, ebay = seller
    rid = _live(dbmod, uid)
    ebay.refuse_publish = True

    snap = _run(client, dbmod, uid, [rid])
    result = snap["result"]
    assert result["failed"] == 1
    message = result["results"]["failed"][0]["message"]
    assert "Ended on eBay" in message and "Pick a category" in message
    assert "saved as a draft" in message

    rows = _rows(dbmod, uid)
    assert rows[rid]["status"] == "ended"
    drafts = [r for r in rows.values() if r["id"] != rid]
    assert len(drafts) == 1 and drafts[0]["status"] == "draft"
    assert drafts[0]["listing"]["title"].startswith("Levi's 501 Original Fit")


def test_only_the_live_ones_it_was_given_are_touched(seller):
    client, dbmod, uid, ebay = seller
    rid = _live(dbmod, uid)
    assert dbmod.upsert_listing("draft-1", {"title": "Not live", "images": []},
                                status="draft", user_id=uid)
    other = dbmod.create_user("other-id", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", {"title": "Theirs", "ebay_listing_id": "9"},
                                status="published", user_id=other["id"])

    snap = _run(client, dbmod, uid, [rid, "draft-1", "theirs", "gone"])
    result = snap["result"]
    assert result["changed"] == 1 and result["skipped"] == 3 and result["failed"] == 0
    skipped = {r["listing_id"]: r["message"] for r in result["results"]["skipped"]}
    assert skipped["draft-1"] == "No longer live on eBay."
    assert skipped["theirs"] == "This listing is gone."
    assert skipped["gone"] == "This listing is gone."
    assert [c[:2] for c in ebay.calls if c[0] == "end"] == [("end", "111")]
    # Another seller's listing is never read, let alone ended.
    assert _rows(dbmod, other["id"])["theirs"]["status"] == "published"


def test_the_second_press_is_handed_the_first_run(seller, monkeypatch):
    client, dbmod, uid, _ebay = seller
    rid = _live(dbmod, uid)
    held = {"go": False}
    real = main.claude_ai.fresh_copy

    def _slow(listing):
        while not held["go"]:
            time.sleep(0.005)
        return real(listing)

    monkeypatch.setattr(main.claude_ai, "fresh_copy", _slow)
    first = client.post("/api/ebay/end-and-relist", json={"listing_ids": [rid]}).json()
    second = client.post("/api/ebay/end-and-relist", json={"listing_ids": [rid]}).json()
    assert second["job_id"] == first["job_id"]
    assert second["joined"] is True
    held["go"] = True
    for _ in range(600):
        if (jobstore.snapshot(first["job_id"], uid) or {}).get("done"):
            break
        time.sleep(0.01)
    rows = _rows(dbmod, uid)
    assert sum(1 for r in rows.values() if r["status"] == "published") == 1


def test_it_needs_ebay_and_the_ai(seller, monkeypatch):
    client, dbmod, uid, _ebay = seller
    rid = _live(dbmod, uid)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: False)
    resp = client.post("/api/ebay/end-and-relist", json={"listing_ids": [rid]})
    assert resp.status_code == 400 and "AI" in resp.json()["detail"]
    monkeypatch.setattr(main.deps, "ebay_creds_for", lambda request: None)
    resp = client.post("/api/ebay/end-and-relist", json={"listing_ids": [rid]})
    assert resp.status_code == 400 and "Connect eBay" in resp.json()["detail"]
    assert client.post("/api/ebay/end-and-relist",
                       json={"listing_ids": []}).status_code == 400

"""One tap fills in a whole group of listings — and only that group.

"Fill in details" used to be a prompt: open each listing, wait for the AI to
read its photos, let it fill eBay's recommended item specifics, save, push,
repeat. `POST /api/listings/enrich` is that same edit applied across the
group in one pass, as a background job the dashboard polls.

What has to hold for a button that spends AI credits per listing and revises
live eBay listings:

  * it touches the listings it was NAMED, and nothing else. A bulk action that
    can widen to a seller's whole store is one mis-sent id away from an
    expensive surprise, and this one bills per item;

  * another seller's id is simply not theirs to enrich — the ownership rule
    lives in the read (db.get_listings), so this route cannot forget it;

  * a listing it cannot fill in is SKIPPED with a reason, not failed. The
    group is whatever the recommendation engine grouped a while ago, so some
    of it has sold, lost its photos, or has nothing the photos can answer;

  * one listing's failure never strands the rest of the run; and

  * the "still to check" notes it actually answered stop being asked. That is
    what makes the button's own suggestion go away afterwards instead of
    sitting there reading like a no-op.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.models import ItemSpecific, Listing


# ------------------------------------------------------------ the notes rule

def test_a_note_the_fill_answered_stops_being_asked():
    listing = Listing(title="Nike hoodie", missing_info=["size", "exact colour"],
                      item_specifics=[ItemSpecific(name="Size", value="M"),
                                      ItemSpecific(name="Colour", value="Black")])
    assert main._drop_answered_missing_info(listing) == 2
    assert listing.missing_info == []


def test_a_note_nothing_filled_is_kept():
    """A blank the AI could not settle is still a real one. Silencing it would
    be the more expensive lie — the seller would believe the listing complete."""
    listing = Listing(title="Camera", missing_info=["exact model number"],
                      item_specifics=[ItemSpecific(name="Brand", value="Canon")])
    assert main._drop_answered_missing_info(listing) == 0
    assert listing.missing_info == ["exact model number"]


def test_a_note_is_answered_by_a_whole_word_only():
    """"Type" must not answer "typewriter model" — a substring match would
    quietly retire notes about something else entirely."""
    listing = Listing(title="Typewriter", missing_info=["typewriter model"],
                      item_specifics=[ItemSpecific(name="Type", value="Manual")])
    assert main._drop_answered_missing_info(listing) == 0
    assert listing.missing_info == ["typewriter model"]


def test_a_blank_specific_answers_nothing():
    listing = Listing(title="Boots", missing_info=["size"],
                      item_specifics=[ItemSpecific(name="Size", value="  ")])
    assert main._drop_answered_missing_info(listing) == 0


def test_the_brand_answers_its_own_note():
    """identify and the maker double-check write the maker to listing.brand,
    not to a specifics row — so the note about it has to read that field."""
    listing = Listing(title="Jacket", brand="Patagonia",
                      missing_info=["the brand, if you can find a label"])
    assert main._drop_answered_missing_info(listing) == 1
    assert listing.missing_info == []


# --------------------------------------------------------------- the run

@pytest.fixture()
def seller(dbmod, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: {"access_token": "t"})
    # Nothing here reaches eBay's taxonomy or Claude: the fill itself is the
    # unit under test everywhere else (main._enrich_listing), and what this
    # file is about is which listings it is handed and what is reported back.
    monkeypatch.setattr(main, "_resolve_category", lambda listing: None)
    monkeypatch.setattr(main, "_adopt_imported_images", lambda rid, rec: [])
    main._ENRICH_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "enrich@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("enrich@example.com")["id"]
    return client, dbmod, uid


def _listing(rid: str, **over) -> dict:
    return {"title": f"Item {rid}", "category_id": "11450",
            "images": ["img_00.jpg"], "missing_info": ["size"], **over}


def _with_photo(rid: str) -> None:
    """A real file where the enrichment looks for one — the route refuses a
    listing whose photos are no longer on the server."""
    from PIL import Image
    path = main.storage.optimized_dir(rid) / "img_00.jpg"
    Image.new("RGB", (8, 8), "white").save(path, "JPEG")


def _finish(client, job_id: str, timeout: float = 10.0) -> dict:
    """The job's result once its worker thread is done."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError(f"enrich job {job_id} never finished")


def _enriched(seen: list):
    """A stand-in for the AI pass: records what it was handed and fills one
    specific, exactly as the real one does when it finds something."""
    def _fill(listing, paths):
        seen.append(listing.title)
        listing.item_specifics.append(ItemSpecific(name="Size", value="M",
                                                   confidence="high"))
        return 1
    return _fill


def test_it_fills_in_the_listings_it_was_named(seller, monkeypatch):
    client, dbmod, uid = seller
    for i in range(4):
        assert dbmod.upsert_listing(f"mine-{i}", _listing(f"mine-{i}"),
                                    status="published", user_id=uid)
        _with_photo(f"mine-{i}")
    seen: list = []
    monkeypatch.setattr(main, "_enrich_listing", _enriched(seen))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["mine-1", "mine-3"]})
    assert started.status_code == 200, started.text
    assert started.json()["total"] == 2
    result = _finish(client, started.json()["job_id"])

    assert result["changed"] == 2
    assert result["filled"] == 2
    assert sorted(seen) == ["Item mine-1", "Item mine-3"]


def test_it_will_not_reach_another_sellers_listing(seller, monkeypatch):
    client, dbmod, uid = seller
    other = dbmod.create_user("other-id", "them@example.com", "x" * 60)
    assert dbmod.upsert_listing("theirs", _listing("theirs"),
                                status="published", user_id=other["id"])
    _with_photo("theirs")
    assert dbmod.upsert_listing("mine", _listing("mine"),
                                status="published", user_id=uid)
    _with_photo("mine")
    seen: list = []
    monkeypatch.setattr(main, "_enrich_listing", _enriched(seen))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["mine", "theirs"]})
    result = _finish(client, started.json()["job_id"])

    assert seen == ["Item mine"]
    assert result["changed"] == 1


def test_a_listing_that_cannot_be_filled_is_skipped_not_failed(seller, monkeypatch):
    """A sold listing, one with no photos left, and one whose photos answered
    nothing are three different sentences — and none of them is a failure."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("sold-one", _listing("sold-one"),
                                status="sold", user_id=uid)
    assert dbmod.upsert_listing("no-photos", _listing("no-photos"),
                                status="published", user_id=uid)
    assert dbmod.upsert_listing("nothing-to-add", _listing("nothing-to-add"),
                                status="published", user_id=uid)
    _with_photo("nothing-to-add")
    monkeypatch.setattr(main, "_enrich_listing", lambda listing, paths: 0)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    started = client.post(
        "/api/listings/enrich",
        json={"listing_ids": ["sold-one", "no-photos", "nothing-to-add"]})
    result = _finish(client, started.json()["job_id"])

    assert result["skipped"] == 3
    assert result["failed"] == 0
    assert result["changed"] == 0


def test_one_rejected_listing_does_not_strand_the_rest(seller, monkeypatch):
    client, dbmod, uid = seller
    for rid in ("first", "boom", "last"):
        assert dbmod.upsert_listing(rid, _listing(rid), status="published",
                                    user_id=uid)
        _with_photo(rid)

    def _fill(listing, paths):
        if listing.title == "Item boom":
            raise RuntimeError("the model fell over")
        listing.item_specifics.append(ItemSpecific(name="Size", value="M",
                                                   confidence="high"))
        return 1
    monkeypatch.setattr(main, "_enrich_listing", _fill)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["first", "boom", "last"]})
    result = _finish(client, started.json()["job_id"])

    assert result["changed"] == 2
    assert result["failed"] == 1
    assert [f["listing_id"] for f in result["results"]["failed"]] == ["boom"]


def test_what_was_filled_in_reaches_ebay(seller, monkeypatch):
    """A revise carries only the fields marked changed. Unmarked, eBay gets an
    empty revise: the record shows the new specifics, the seller is told it
    worked, and the live listing is still blank."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("live-one", _listing("live-one"),
                                status="published", user_id=uid)
    _with_photo("live-one")
    ebay = _AcceptingEbay()
    monkeypatch.setattr(main, "_enrich_listing", _enriched([]))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    started = client.post("/api/listings/enrich",
                          json={"listing_ids": ["live-one"]})
    result = _finish(client, started.json()["job_id"])

    assert result["changed"] == 1
    assert len(ebay.sent) == 1
    sent = ebay.sent[0]
    assert "item_specifics" in sent.listing.dirty_fields
    assert [(s.name, s.value) for s in sent.listing.item_specifics] == [("Size", "M")]


def test_adopted_photos_are_not_wiped_by_the_fill(seller, monkeypatch):
    """An imported listing's photos live on eBay until something downloads
    them, and adoption writes the local filenames onto the record. A listing
    model built a moment BEFORE that carries an empty `images` — and the save
    at the end of the fill puts it straight back over the photos that were
    just downloaded, so the next open re-downloads all of them."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing(
        "imported", _listing("imported", images=[], source="ebay",
                             image_urls=["https://i.ebayimg.com/x.jpg"]),
        status="published", user_id=uid)
    _with_photo("imported")

    def _adopt(rid, rec):
        rec["listing"] = {**rec["listing"], "images": ["img_00.jpg"]}
        return ["img_00.jpg"]
    monkeypatch.setattr(main, "_adopt_imported_images", _adopt)
    ebay = _AcceptingEbay()
    monkeypatch.setattr(main, "_enrich_listing", _enriched([]))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    started = client.post("/api/listings/enrich", json={"listing_ids": ["imported"]})
    assert _finish(client, started.json()["job_id"])["changed"] == 1
    assert ebay.sent[0].listing.images == ["img_00.jpg"]


def test_a_live_listing_we_cannot_revise_is_not_billed_for(seller, monkeypatch):
    """Filling in a live listing we then cannot push leaves the page buyers
    see exactly as blank as it was. Charging for that is charging for nothing
    the seller can point at."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("live-one", _listing("live-one"),
                                status="published", user_id=uid)
    _with_photo("live-one")
    monkeypatch.setattr(main, "_ebay_creds_for", lambda request: None)
    charged: list = []
    monkeypatch.setattr(main, "_charge_uid",
                        lambda *a, **k: charged.append(a) or None)
    monkeypatch.setattr(main, "_enrich_listing", _enriched([]))

    started = client.post("/api/listings/enrich", json={"listing_ids": ["live-one"]})
    result = _finish(client, started.json()["job_id"])

    assert result["skipped"] == 1
    assert charged == []


def test_a_draft_is_filled_in_without_touching_ebay(seller, monkeypatch):
    client, dbmod, uid = seller
    assert dbmod.upsert_listing("a-draft", _listing("a-draft"),
                                status="draft", user_id=uid)
    _with_photo("a-draft")
    ebay = _AcceptingEbay()
    monkeypatch.setattr(main, "_enrich_listing", _enriched([]))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    started = client.post("/api/listings/enrich", json={"listing_ids": ["a-draft"]})
    result = _finish(client, started.json()["job_id"])

    assert result["changed"] == 1
    assert ebay.sent == []
    saved = dbmod.get_listing("a-draft")
    assert saved["status"] == "draft"          # never demoted, never promoted
    assert [s["name"] for s in saved["listing"]["item_specifics"]] == ["Size"]
    assert saved["listing"]["missing_info"] == []   # the note it answered


def test_a_second_tap_joins_the_run_it_already_paid_for(seller, monkeypatch):
    """Two tabs, or a double tap. Starting a second pass over the same group
    would fill the same blanks twice and bill for both."""
    client, dbmod, uid = seller
    for rid in ("one", "two"):
        assert dbmod.upsert_listing(rid, _listing(rid), status="published",
                                    user_id=uid)
        _with_photo(rid)
    held = _Gate()
    monkeypatch.setattr(main, "_enrich_listing", held.fill)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    first = client.post("/api/listings/enrich", json={"listing_ids": ["one", "two"]})
    second = client.post("/api/listings/enrich", json={"listing_ids": ["one", "two"]})
    assert second.json()["job_id"] == first.json()["job_id"]
    held.open()
    _finish(client, first.json()["job_id"])
    assert held.calls == 2      # each listing filled once, not twice


def test_the_charge_is_written_down_while_it_is_outstanding(seller, monkeypatch):
    """A machine killed rather than stopped takes the spend receipt with it:
    no finally runs, and the seller has paid for a fill that never happened.
    The job records the charge while it is in flight so a later boot can
    settle up (main._settle_interrupted_jobs), and clears it the moment this
    process has settled it either way — a receipt left behind would be
    refunded a second time on the next restart."""
    _client, dbmod, uid = seller
    assert dbmod.upsert_listing("one", _listing("one"), status="draft", user_id=uid)
    _with_photo("one")
    monkeypatch.setattr(main, "_charge_uid",
                        lambda u, feature, units=1: {"ok": True, "entry_id": "e1",
                                                     "user_id": u})
    monkeypatch.setattr(main, "_enrich_listing", _enriched([]))

    noted: list = []
    outcome = main._enrich_one(dbmod.get_listing("one"), uid, None,
                               "http://x", note_charge=noted.append)

    assert outcome["ok"]
    assert noted == [[{"ok": True, "entry_id": "e1", "user_id": uid}], None]


def test_a_charge_for_a_fill_that_never_ran_goes_back(seller, monkeypatch):
    """_enrich_listing swallows its own failures and answers None for "didn't
    run", so the refund has to key off that rather than an exception."""
    _client, dbmod, uid = seller
    assert dbmod.upsert_listing("one", _listing("one"), status="draft", user_id=uid)
    _with_photo("one")
    monkeypatch.setattr(main, "_charge_uid",
                        lambda u, feature, units=1: {"ok": True, "entry_id": "e1",
                                                     "user_id": u})
    monkeypatch.setattr(main, "_enrich_listing", lambda listing, paths: None)
    given_back: list = []
    monkeypatch.setattr(main.tokens, "refund", given_back.append)

    outcome = main._enrich_one(dbmod.get_listing("one"), uid, None, "http://x")

    assert outcome["skip"]
    assert [r["entry_id"] for r in given_back] == ["e1"]


def test_an_empty_ask_is_refused(seller):
    client, _dbmod, _uid = seller
    assert client.post("/api/listings/enrich", json={"listing_ids": []}).status_code == 400


def test_more_ids_than_one_run_can_hold_are_deferred(seller, monkeypatch):
    """The remainder is reported for a second pass rather than silently
    dropped — the same contract the bulk price drop keeps."""
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "BULK_ENRICH_CAP", 2)
    ids = []
    for i in range(5):
        rid = f"many-{i}"
        ids.append(rid)
        assert dbmod.upsert_listing(rid, _listing(rid), status="published",
                                    user_id=uid)
        _with_photo(rid)
    monkeypatch.setattr(main, "_enrich_listing", _enriched([]))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    started = client.post("/api/listings/enrich", json={"listing_ids": ids})
    body = started.json()
    assert (body["total"], body["deferred"]) == (2, 3)
    assert _finish(client, body["job_id"])["deferred"] == 3


def test_the_dashboard_is_told_what_one_run_holds(seller, monkeypatch):
    """The cap is enforced here and rendered there, so it has to travel.

    Without it the dashboard promised the whole group: a 46-listing "Fill in
    details" asked the seller to confirm 46 and quoted the AI cost of 46, then
    filled BULK_ENRICH_CAP of them and reported "1 of 25" under a badge still
    reading 46. What /api/insights publishes is the number this route will
    actually run — asserted against the run itself, not against the constant.
    """
    client, dbmod, uid = seller
    monkeypatch.setattr(main, "BULK_ENRICH_CAP", 2)
    # The suggestion engine's own eBay lookups are not what this is about.
    monkeypatch.setattr(main, "_metrics_by_record_id", lambda creds, items: {})
    monkeypatch.setattr(main, "_rates_by_record_id", lambda creds, items: {})
    monkeypatch.setattr(main, "_promoted_record_ids", lambda creds, items: (set(), True))
    ids = []
    for i in range(5):
        rid = f"capped-{i}"
        ids.append(rid)
        assert dbmod.upsert_listing(rid, _listing(rid), status="published",
                                    user_id=uid)
        _with_photo(rid)
    monkeypatch.setattr(main, "_enrich_listing", _enriched([]))
    monkeypatch.setattr(main.marketplaces, "get", lambda name: _AcceptingEbay())

    caps = client.get("/api/insights").json()["bulk_caps"]
    assert caps["lower_price"] == main.BULK_PRICE_CAP

    body = client.post("/api/listings/enrich", json={"listing_ids": ids}).json()
    assert body["total"] == caps["specifics"] == 2
    assert body["deferred"] == 3
    _finish(client, body["job_id"])


def test_a_logged_out_caller_gets_nothing(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/listings/enrich",
                       json={"listing_ids": ["x"]}).status_code == 401


# ----------------------------------------------------------------- stand-ins

class _AcceptingEbay:
    """eBay, saying yes. Records the context each revise was sent with."""

    def __init__(self):
        self.sent = []

    def publish(self, ctx, creds):
        from backend.marketplaces.base import PublishOutcome
        self.sent.append(ctx)
        return PublishOutcome(ok=True, message="Revised.", status="published")


class _Gate:
    """A fill that blocks until the test lets it through, so a second request
    lands while the first job is genuinely still running."""

    def __init__(self):
        import threading
        self._go = threading.Event()
        self.calls = 0

    def fill(self, listing, paths):
        self._go.wait(5)
        self.calls += 1
        listing.item_specifics.append(ItemSpecific(name="Size", value="M",
                                                   confidence="high"))
        return 1

    def open(self):
        self._go.set()

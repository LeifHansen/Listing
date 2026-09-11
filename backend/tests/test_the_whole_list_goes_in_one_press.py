"""One press finishes the list — both halves of it, all of it, to eBay.

The seller's report, looking at "Fill in details · 131" above "Check details ·
177": "I want all of this to be done and submitted to eBay with one click, not
individually, and not broken out into multiple steps."

Every word of that named something real.

ONE CLICK. There was no button that spanned both groups, so clearing the list
meant two different motions.

NOT INDIVIDUALLY. "Check details" had no bulk verb at all — 177 rows, each
with a link that opens one listing. It could only shrink one hand-checked
listing at a time, which on that scale is not a to-do list.

NOT BROKEN OUT INTO MULTIPLE STEPS. "Enrich all" ran BULK_ENRICH_CAP listings
and deferred the rest, so 131 of them was six presses.

So: one route, no cap, and the notes the AI declined to invent become
something the seller can answer in bulk — by saying they are fine, which is a
real answer and the only honest one available. Nothing is deleted and nothing
false reaches eBay: `missing_info` is a note to the seller, never listing
content.

What this must NOT do is charge twice. A listing the AI has already read
(`enriched_at`) gains nothing from reading it again — that is the loop
test_fill_in_details_stops_asking exists for — so the fill runs only on the
listings that have never had it, and the rest are accepted for free.
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
from backend.services import recommender


@pytest.fixture()
def seller(dbmod, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "t"})
    monkeypatch.setattr(main, "_metrics_by_record_id", lambda creds, items: {})
    monkeypatch.setattr(main, "_resolve_category", lambda listing: None)
    monkeypatch.setattr(main, "_adopt_imported_images", lambda rid, rec: [])
    main._ENRICH_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "finish@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("finish@example.com")["id"]
    return client, dbmod, uid


def _listing(rid: str, **over) -> dict:
    """A live imported listing with blank specifics and enough photos that no
    other suggestion outranks the two under test."""
    return {"title": f"Item {rid}", "category_id": "11450", "source": "ebay",
            "ebay_listing_id": f"11{rid}", "images": ["a.jpg", "b.jpg", "c.jpg"],
            "missing_info": ["exact measurements"], **over}


def _already_read(rid: str, **over) -> dict:
    """A listing the fill has run on, still carrying a note it could not
    answer — the "Check details" half."""
    return _listing(rid, enriched_at="2026-09-04T12:00:00+00:00",
                    item_specifics=[
                        {"name": "Brand", "value": "Nike"},
                        {"name": "Colour", "value": "Black"},
                        {"name": "Size", "value": "XL"}],
                    **over)


def _with_photo(rid: str) -> None:
    from PIL import Image
    Image.new("RGB", (8, 8), "white").save(
        main.storage.optimized_dir(rid) / "a.jpg", "JPEG")


def _stock(dbmod, uid: str, ids, factory=_listing) -> None:
    for rid in ids:
        assert dbmod.upsert_listing(rid, factory(rid), status="published",
                                    user_id=uid)
        _with_photo(rid)


class _AcceptingEbay:
    """eBay saying yes, persisting the record the way the real provider does."""

    def __init__(self, dbmod, uid):
        self.db, self.uid, self.revised = dbmod, uid, []

    def publish(self, ctx, creds):
        from backend.marketplaces.base import PublishOutcome
        self.revised.append(ctx.session_id)
        self.db.upsert_listing(ctx.session_id, ctx.listing.model_dump(),
                               status="published", user_id=self.uid)
        return PublishOutcome(ok=True, message="Revised.", status="published")


def _finish(client, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError(f"finish job {job_id} never finished")


def _groups(client) -> dict:
    return client.get("/api/insights").json()["group_totals"]


# --------------------------------------------------------------- the press

def test_one_press_clears_both_groups(seller, monkeypatch):
    """The screenshot, in miniature: a "Fill in details" pile and a "Check
    details" pile, and one press that empties both."""
    client, dbmod, uid = seller
    fresh = [f"NEW{i:03d}" for i in range(6)]
    read = [f"OLD{i:03d}" for i in range(9)]
    _stock(dbmod, uid, fresh)
    _stock(dbmod, uid, read, factory=_already_read)

    calls = []

    def _fill(listing, paths, tags, progress=None):
        calls.append(listing.title)
        listing.item_specifics.append(
            ItemSpecific(name="Size", value="M", confidence="high"))
        return 1

    monkeypatch.setattr(main, "_enrich_listing_v2", _fill)
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    before = _groups(client)
    assert before == {"specifics": 6, "verify": 9}

    started = client.post("/api/listings/finish-all")
    body = started.json()
    # No cap: one press reaches every listing on the list, not a slice of it.
    assert body["total"] == 15
    assert body["deferred"] == 0
    result = _finish(client, body["job_id"])

    assert result["changed"] == 6
    assert result["accepted"] == 15
    assert _groups(client) == {}, "the list the seller was looking at is gone"


def test_the_listings_it_filled_reach_ebay(seller, monkeypatch):
    """"...and submitted to eBay". A fill kept only in our own record leaves
    the live listing exactly as blank as it was."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["A", "B"])

    monkeypatch.setattr(
        main, "_enrich_listing_v2",
        lambda listing, paths, tags, progress=None: (
            listing.item_specifics.append(
                ItemSpecific(name="Size", value="M", confidence="high")) or 1))
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    _finish(client, client.post("/api/listings/finish-all").json()["job_id"])

    assert sorted(ebay.revised) == ["A", "B"]


def test_a_listing_the_ai_has_read_is_not_charged_again(seller, monkeypatch):
    """The whole reason "Check details" never had a button: re-reading a
    listing the AI has already read buys nothing and bills for it."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["fresh"])
    _stock(dbmod, uid, ["read"], factory=_already_read)

    seen = []
    monkeypatch.setattr(
        main, "_enrich_listing_v2",
        lambda listing, paths, tags, progress=None: (seen.append(listing.title) or 0))
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda name: _AcceptingEbay(dbmod, uid))

    _finish(client, client.post("/api/listings/finish-all").json()["job_id"])

    assert seen == ["Item fresh"], "the AI was run again on a listing it had read"
    # ...and the one it skipped is still off the list afterwards.
    assert _groups(client) == {}


def test_the_notes_are_kept_not_deleted(seller, monkeypatch):
    """Accepting a note is the seller answering, not the app forgetting. The
    editor still shows what the AI flagged; only the nagging stops."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, ["keep"], factory=_already_read)
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda name: _AcceptingEbay(dbmod, uid))

    _finish(client, client.post("/api/listings/finish-all").json()["job_id"])

    stored = dbmod.get_listing("keep")["listing"]
    assert stored["missing_info"] == ["exact measurements"]
    assert stored["notes_accepted_at"], "the seller's answer was not written down"


def test_nothing_is_pushed_to_ebay_just_to_accept_a_note(seller, monkeypatch):
    """`missing_info` is a note to the seller, never listing content. Accepting
    one changes nothing a buyer can see, so it must not spend an eBay revise
    on 177 listings to say so."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, [f"R{i}" for i in range(4)], factory=_already_read)
    ebay = _AcceptingEbay(dbmod, uid)
    monkeypatch.setattr(main.marketplaces, "get", lambda name: ebay)

    _finish(client, client.post("/api/listings/finish-all").json()["job_id"])

    assert ebay.revised == []


def test_a_listing_the_fill_could_not_run_on_says_so_and_stays(seller,
                                                               monkeypatch):
    """The one thing this button does NOT clear, and should not.

    A listing whose photos are no longer on the server has not been read by
    the AI, and the fill is still genuinely ahead of it — re-adopt the photos
    (opening it in the editor does that) and it can run. Stamping it done
    would hide a listing that can still be improved, so it stays on the list
    and the run says why in the seller's own words rather than silently
    leaving it there.

    Its NOTES are still accepted: that answer holds whether or not the fill
    ever runs, and it is what stops the listing moving to "Check details" the
    moment it does.
    """
    client, dbmod, uid = seller
    # No photo file on disk: _enrich_one skips it before it charges anything.
    assert dbmod.upsert_listing("nophoto", _listing("nophoto"),
                                status="published", user_id=uid)
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda name: _AcceptingEbay(dbmod, uid))

    result = _finish(client,
                     client.post("/api/listings/finish-all").json()["job_id"])

    assert result["skipped"] == 1
    assert "photos aren't on the server" in result["results"]["skipped"][0]["message"]
    assert result["accepted"] == 1
    assert dbmod.get_listing("nophoto")["listing"]["notes_accepted_at"]
    # Still asking for the fill, because the fill has still never run on it.
    assert _groups(client) == {"specifics": 1}


def test_it_does_not_touch_the_groups_that_need_a_decision(seller, monkeypatch):
    """A price cut needs a percentage and photos need someone holding the
    item. Neither is this button's to make."""
    client, dbmod, uid = seller
    assert dbmod.upsert_listing(
        "thin", _listing("thin", images=["only.jpg"]),
        status="published", user_id=uid)
    _with_photo("thin")
    monkeypatch.setattr(
        main, "_enrich_listing_v2",
        lambda listing, paths, tags, progress=None: 0)
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda name: _AcceptingEbay(dbmod, uid))

    # One photo outranks the fill, so this listing is in "Add more photos".
    assert _groups(client) == {"photos": 1}
    assert client.post("/api/listings/finish-all").status_code == 400
    assert _groups(client) == {"photos": 1}


# ------------------------------------------------------- what it says first

def test_the_plan_prices_only_what_it_charges_for(seller):
    """308 listings do not cost 308 fills. Quoting the total would price the
    press at four times what it spends."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, [f"NEW{i}" for i in range(3)])
    _stock(dbmod, uid, [f"OLD{i}" for i in range(7)], factory=_already_read)

    plan = client.get("/api/insights").json()["finish_all"]
    assert plan == {"total": 10, "enrich": 3, "accept": 7}


def test_a_logged_out_caller_gets_nothing(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/listings/finish-all").status_code == 401


# -------------------------------------------------------------- the units

def test_an_accepted_note_stops_the_group_asking():
    item = {"id": "L1", "status": "published",
            "created_at": "2020-01-01T00:00:00+00:00",
            "listing": {"title": "Bowling trophy",
                        "images": ["1.jpg", "2.jpg", "3.jpg"],
                        "missing_info": ["exact measurements"],
                        "enriched_at": "2026-09-04T12:00:00+00:00",
                        "notes_accepted_at": "2026-09-11T09:00:00+00:00"}}
    assert "verify" not in [r["type"] for r in recommender.recommend_for(item)]


def test_an_unanswered_note_still_asks():
    item = {"id": "L1", "status": "published",
            "created_at": "2020-01-01T00:00:00+00:00",
            "listing": {"title": "Bowling trophy",
                        "images": ["1.jpg", "2.jpg", "3.jpg"],
                        "missing_info": ["exact measurements"],
                        "enriched_at": "2026-09-04T12:00:00+00:00"}}
    assert "verify" in [r["type"] for r in recommender.recommend_for(item)]

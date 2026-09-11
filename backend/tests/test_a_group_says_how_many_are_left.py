"""A suggestion group's badge is a COUNT, and a count has to survive the cap.

The seller's report: "the Enrich all counter doesn't decrease as we enrich and
update items." Every part of the run worked — the AI read the photos, the
specifics were filled, the revises reached eBay, `enriched_at` was stamped so
those listings stopped asking. And the badge read the same number afterwards.

Because the badge was never the count. /api/insights ranked the whole store,
cut the list to fit a payload, and the dashboard counted the rows that
arrived. On a store with more suggestions than the cap, that number IS the
cap: fill 25 listings and 25 that had been below the line move up to take
their place. The screen has no way to show work that happened, which reads
exactly like a button that does nothing — and this group has now been
reported for that twice (see test_fill_in_details_stops_asking for the other
half, which was real).

So the count is taken before the cut and sent as `group_totals`, and the cut
is applied PER TYPE — a flat one made each group's membership depend on how
busy the other groups were, and past a storeful of stale prices it dropped
"Fill in details" off the payload altogether, taking the only button that
clears it with it.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import main, ratelimit
from backend.models import ItemSpecific
from backend.services import recommender


# ------------------------------------------------------------------- units

def _rec(type_: str, lid: str, priority: int) -> dict:
    return {"listing_id": lid, "listing_title": lid, "type": type_,
            "label": type_, "reason": "", "action": "open",
            "priority": priority}


def test_the_total_counts_every_rec_of_its_type():
    recs = [_rec("lower_price", f"p{i}", 68) for i in range(9)]
    recs += [_rec("specifics", f"s{i}", 45) for i in range(4)]
    assert recommender.totals_by_type(recs) == {"lower_price": 9,
                                                "specifics": 4}


def test_the_cap_is_per_type_so_no_group_is_squeezed_out():
    """A flat cap of 3 over this list returns three prices and nothing else —
    the specifics group, and the only button that clears it, never reaches the
    screen."""
    recs = [_rec("lower_price", f"p{i}", 68) for i in range(9)]
    recs += [_rec("specifics", f"s{i}", 45) for i in range(4)]
    kept = recommender.capped_by_type(recs, 3)
    assert [r["type"] for r in kept] == ["lower_price"] * 3 + ["specifics"] * 3
    # Order within a type is the ranking's own, so the strongest still lead.
    assert [r["listing_id"] for r in kept][:3] == ["p0", "p1", "p2"]


def test_a_cap_of_zero_keeps_everything():
    recs = [_rec("specifics", f"s{i}", 45) for i in range(4)]
    assert recommender.capped_by_type(recs, 0) == recs


# ------------------------------------------------------- the group on screen

@pytest.fixture()
def seller(dbmod, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "db", dbmod)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "_ebay_creds_for",
                        lambda request: {"access_token": "t"})
    # eBay's traffic report is a network call and says nothing about any of
    # this: every rec here comes from the record itself.
    monkeypatch.setattr(main, "_metrics_by_record_id", lambda creds, items: {})
    monkeypatch.setattr(main, "_resolve_category", lambda listing: None)
    monkeypatch.setattr(main, "_adopt_imported_images", lambda rid, rec: [])
    main._ENRICH_JOBS.clear()
    ratelimit.reset()
    client = TestClient(main.app)
    assert client.post("/api/auth/signup",
                       json={"email": "groups@example.com",
                             "password": "password123"}).status_code < 400
    uid = dbmod.get_user_by_email("groups@example.com")["id"]
    return client, dbmod, uid


def _listing(rid: str, **over) -> dict:
    """A live imported listing with blank specifics and photos enough that no
    other suggestion outranks the fill."""
    return {"title": f"Item {rid}", "category_id": "11450", "source": "ebay",
            "ebay_listing_id": f"11{rid}", "images": ["a.jpg", "b.jpg", "c.jpg"],
            "missing_info": ["size"], **over}


def _with_photo(rid: str) -> None:
    from PIL import Image
    Image.new("RGB", (8, 8), "white").save(
        main.storage.optimized_dir(rid) / "a.jpg", "JPEG")


def _stock(dbmod, uid: str, ids, when=None, **over) -> None:
    """`when` dates the row on creation (db.upsert_listing takes it as the
    record's created_at), which is the clock the age heuristics read."""
    for rid in ids:
        assert dbmod.upsert_listing(rid, _listing(rid, **over),
                                    status="published", user_id=uid, when=when)
        _with_photo(rid)


class _AcceptingEbay:
    """eBay saying yes, and recording the revise the way every branch of the
    real provider does (ebay_provider.publish always writes the dump)."""

    def __init__(self, dbmod, uid):
        self.db, self.uid = dbmod, uid

    def publish(self, ctx, creds):
        from backend.marketplaces.base import PublishOutcome
        self.db.upsert_listing(ctx.session_id, ctx.listing.model_dump(),
                               status="published", user_id=self.uid)
        return PublishOutcome(ok=True, message="Revised.", status="published")


def _fills_one(listing, paths, tags, progress=None):
    listing.item_specifics.append(
        ItemSpecific(name="Size", value="M", confidence="high"))
    return 1


def _insights(client) -> dict:
    return client.get("/api/insights").json()


def _finish(client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            assert not body.get("error"), body["error"]
            return body["result"]
        time.sleep(0.02)
    raise AssertionError(f"enrich job {job_id} never finished")


def test_the_badge_counts_the_store_not_the_payload(seller):
    """80 listings need the fill. The payload carries a capped slice of them;
    the number the group shows is 80."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, [f"L{i:03d}" for i in range(80)])

    body = _insights(client)
    rows = [r for r in body["recommendations"] if r["type"] == "specifics"]
    assert len(rows) == main.INSIGHTS_GROUP_CAP
    assert body["group_totals"]["specifics"] == 80


def test_the_badge_goes_down_by_what_the_run_filled(seller, monkeypatch):
    """The seller's report, end to end: press the button, watch the number."""
    client, dbmod, uid = seller
    _stock(dbmod, uid, [f"L{i:03d}" for i in range(80)])
    monkeypatch.setattr(main, "_enrich_listing_v2", _fills_one)
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda name: _AcceptingEbay(dbmod, uid))

    before = _insights(client)
    assert before["group_totals"]["specifics"] == 80
    ids = [r["listing_id"] for r in before["recommendations"]
           if r["type"] == "specifics"]

    started = client.post("/api/listings/enrich", json={"listing_ids": ids})
    assert started.json()["total"] == main.BULK_ENRICH_CAP
    result = _finish(client, started.json()["job_id"])
    assert result["changed"] == main.BULK_ENRICH_CAP

    after = _insights(client)["group_totals"]["specifics"]
    assert after == 80 - main.BULK_ENRICH_CAP, (
        "the run filled a capped number of listings and the badge did not "
        "move — which is what a seller reads as the button not working")


def test_a_busy_group_does_not_push_another_off_the_screen(seller):
    """60 stale prices outrank 10 blank specifics. Under one flat cap of 50
    the "Fill in details" group never arrived at all, so the only button that
    clears it was unreachable — on exactly the store that needs it most."""
    client, dbmod, uid = seller
    stale = (datetime.now(timezone.utc)
             - timedelta(days=recommender.STALE_DAYS + 5))
    # An old listing earns the stronger "Lower the price" rec, and `ranked`
    # keeps one rec per listing — so these are wholly the other group.
    _stock(dbmod, uid, [f"OLD{i:03d}" for i in range(60)], when=stale)
    _stock(dbmod, uid, [f"NEW{i:03d}" for i in range(10)])

    body = _insights(client)
    kinds = {r["type"] for r in body["recommendations"]}
    assert kinds == {"lower_price", "specifics"}, \
        "the group with the button on it fell off the payload"
    assert body["group_totals"] == {"lower_price": 60, "specifics": 10}
    # ...and the payload itself stays bounded: per type, not per store.
    assert len([r for r in body["recommendations"]
                if r["type"] == "lower_price"]) == main.INSIGHTS_GROUP_CAP

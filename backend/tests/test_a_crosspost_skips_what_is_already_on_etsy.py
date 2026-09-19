"""A crosspost never sends what Etsy already has, or cannot take.

The browser says the same thing before the wizard opens, but this is the
rule rather than the courtesy: a listing already on Etsy would be
duplicated in the seller's shop, an auction is a format Etsy does not have,
and a listing with variations is one this app cannot represent as a single
price and stock count.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.marketplaces.base import PublishOutcome
from backend.services import jobstore


class _Etsy:
    key, label = "etsy", "Etsy"
    oauth_ready = staticmethod(lambda: True)

    def creds_for(self, _uid):
        return {"access_token": "t", "shop_id": "1", "settings": {}}


def _rec(rid, status="published", **listing):
    base = {"title": f"Item {rid}", "description": "Nice.", "price": 20.0,
            "quantity": 1, "images": ["a.jpg"],
            "marketplaces": {"ebay": {"status": "published"}}}
    base.update(listing)
    return {"id": rid, "user_id": "u1", "status": status, "listing": base}


STORE = {
    "fine": _rec("fine"),
    "onetsy": _rec("onetsy", marketplaces={"ebay": {"status": "published"},
                                           "etsy": {"status": "published", "listing_id": "9"}}),
    "auction": _rec("auction", listing_format="AUCTION"),
    "variations": _rec("variations", has_variations=True),
    "draft": _rec("draft", status="draft"),
}


@pytest.fixture
def api(monkeypatch, every_marketplace):
    from backend import main

    sent: list[str] = []
    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda key: _Etsy() if key == "etsy" else None)
    monkeypatch.setattr(main.db, "get_listings",
                        lambda ids, uid: [STORE[i] for i in ids if i in STORE])
    monkeypatch.setattr(main.db, "get_listing", lambda rid: STORE.get(rid))
    monkeypatch.setattr(main.db, "mutate_listing_data",
                        lambda rid, fn, **kw: fn(dict(STORE[rid]["listing"])))
    monkeypatch.setattr(main, "_adopt_imported_images", lambda *a, **k: None)
    monkeypatch.setattr(main, "CROSSPOST_PACE_SECONDS", 0)
    monkeypatch.setattr(main, "_publish_targets",
                        lambda sid, *a, **k: (sent.append(sid), {
                            "etsy": PublishOutcome(ok=True, listing_id="e1",
                                                   status="draft", message="ok")})[1])
    monkeypatch.setattr(main.etsy_service, "suggest_taxonomy",
                        lambda listing: {"taxonomy_id": 7, "path": "A > B",
                                         "source": "ebay_path"})
    jobstore._JOBS.clear()
    main._CROSSPOST_JOBS.clear()
    return TestClient(main.app), sent


ALL = ["fine", "onetsy", "auction", "variations", "draft"]


def test_the_review_says_which_are_left_out_and_why(api):
    client, _sent = api
    body = client.post("/api/crosspost/etsy/review",
                       json={"listing_ids": ALL, "defaults": {}}).json()
    assert [r["id"] for r in body["rows"]] == ["fine"]
    why = {s["id"]: s["why"] for s in body["skipped"]}
    assert "Already on Etsy" in why["onetsy"]
    assert "auction" in why["auction"].lower()
    assert "variations" in why["variations"].lower()
    assert "Not live" in why["draft"]


def test_the_run_refuses_them_too_even_if_the_browser_asked(api):
    """The wizard's list is a convenience; a request naming a listing Etsy
    already has must not publish it a second time."""
    client, sent = api
    job_id = client.post("/api/crosspost/etsy/start", json={
        "items": [{"id": i, "etsy": {}} for i in ALL], "mode": "draft"}).json()["job_id"]
    for _ in range(600):
        snap = jobstore.snapshot(job_id, "u1")
        if snap.get("done"):
            break
        time.sleep(0.01)
    assert sent == ["fine"]
    rows = {r["id"]: r for r in snap["items"]}
    assert rows["onetsy"]["status"] == "skipped"
    assert "Already on Etsy" in rows["onetsy"]["message"]
    assert rows["fine"]["status"] == "draft"

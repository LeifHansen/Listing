"""Crossposting a store to Etsy is a job, not a request.

Twenty listings is twenty publishes, each a dozen calls to Etsy, so it runs
behind the same job machinery every other long run here uses: progress the
seller can watch, a Stop they can press, and a record of what happened to
each listing. Three rules this pins:

- it publishes through _publish_targets, the same door /api/publish uses,
  so the duplicate guard and the state fold cannot drift into a second copy;
- Stop is honoured BETWEEN listings, never inside one, where standing down
  would leave a listing created on Etsy with no photos on it;
- one listing's refusal is one row, not the end of the run.
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


def _record(rid, **over):
    listing = {"title": f"Item {rid}", "description": "Nice.", "price": 20.0,
               "quantity": 1, "images": ["a.jpg"],
               "marketplaces": {"ebay": {"status": "published"}}}
    listing.update(over.pop("listing", {}))
    return {"id": rid, "user_id": "u1", "status": "published", "listing": listing, **over}


@pytest.fixture
def api(monkeypatch, every_marketplace):
    from backend import main

    store = {rid: _record(rid) for rid in ("a", "b", "c")}
    published: list[str] = []

    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda key: _Etsy() if key == "etsy" else None)
    monkeypatch.setattr(main.db, "get_listings",
                        lambda ids, uid: [store[i] for i in ids if i in store])
    monkeypatch.setattr(main.db, "get_listing", lambda rid: store.get(rid))
    monkeypatch.setattr(main.db, "mutate_listing_data",
                        lambda rid, fn, **kw: fn(dict(store[rid]["listing"])))
    monkeypatch.setattr(main, "_adopt_imported_images", lambda *a, **k: None)
    monkeypatch.setattr(main, "CROSSPOST_PACE_SECONDS", 0)

    def _publish(session_id, listing, mode, targets, uid, base_url, prev_rec):
        published.append(session_id)
        assert targets == ["etsy"], "a crosspost publishes to Etsy and nothing else"
        if session_id == "b":
            return {"etsy": PublishOutcome(ok=False, message="Etsy said no.",
                                           issues=[{"target": "title"}])}
        return {"etsy": PublishOutcome(ok=True, listing_id=f"e-{session_id}",
                                       url=f"https://etsy/{session_id}",
                                       status="draft", message="Draft created.")}

    monkeypatch.setattr(main, "_publish_targets", _publish)
    jobstore._JOBS.clear()
    main._CROSSPOST_JOBS.clear()
    client = TestClient(main.app)
    return client, store, published


def _start(client, ids, mode="draft"):
    return client.post("/api/crosspost/etsy/start", json={
        "items": [{"id": i, "etsy": {"taxonomy_id": 1, "who_made": "someone_else",
                                     "when_made": "1990s"}} for i in ids],
        "mode": mode})


def _wait(client, job_id, tries=600):
    for _ in range(tries):
        body = client.get(f"/api/bulk/status/{job_id}").json()
        if body.get("done"):
            return body
        time.sleep(0.01)
    raise AssertionError("the job never finished")


def test_every_listing_is_published_through_the_one_publish_path(api):
    client, _store, published = api
    job_id = _start(client, ["a", "b", "c"]).json()["job_id"]
    body = _wait(client, job_id)
    assert published == ["a", "b", "c"]
    rows = {r["id"]: r for r in body["items"]}
    assert rows["a"]["status"] == "draft"
    assert rows["a"]["url"] == "https://etsy/a"
    assert rows["c"]["status"] == "draft"
    assert body["current"] == 3
    assert body["phase"] == "done"


def test_one_refusal_is_one_row_not_the_end_of_the_run(api):
    client, _store, published = api
    job_id = _start(client, ["a", "b", "c"]).json()["job_id"]
    body = _wait(client, job_id)
    rows = {r["id"]: r for r in body["items"]}
    assert rows["b"]["status"] == "refused"
    assert rows["b"]["message"] == "Etsy said no."
    assert [r["status"] for r in body["items"] if r["id"] != "b"] == ["draft", "draft"]


def test_stop_is_honoured_between_listings(api, monkeypatch):
    from backend import main

    client, _store, published = api
    started: list[str] = []

    real = main._crosspost_one

    def _slow(job_id, uid, item, mode, base_url):
        started.append(str(item["id"]))
        if len(started) == 1:
            # The seller presses Stop while the first is in flight.
            jobstore.request_cancel(job_id, "u1")
        return real(job_id, uid, item, mode, base_url)

    monkeypatch.setattr(main, "_crosspost_one", _slow)
    job_id = _start(client, ["a", "b", "c"]).json()["job_id"]
    for _ in range(600):
        if jobstore.snapshot(job_id, "u1").get("done"):
            break
        time.sleep(0.01)
    # The one in flight was finished; nothing after it was begun.
    assert started == ["a"]
    assert published == ["a"]
    snap = jobstore.snapshot(job_id, "u1")
    assert snap["cancelled"] is True
    assert [r["status"] for r in snap["items"]] == ["draft", "queued", "queued"]

"""Two tabs, or a double tap, must not crosspost the same store twice.

The check and the reservation are one critical section, like the enrich
route's: a second press that passed the check before the first registered
would put every listing on Etsy twice, which is the one mistake this
feature cannot make. The second press is handed the job already running.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.marketplaces.base import PublishOutcome
from backend.services import jobstore

# backend.main pulls in the AI and photo stacks, which the minimal CI install
# does not have. Skipped there rather than erroring, like every other test
# that drives the app.
pytest.importorskip("anthropic")
pytest.importorskip("PIL")


class _Etsy:
    key, label = "etsy", "Etsy"
    oauth_ready = staticmethod(lambda: True)

    def creds_for(self, _uid):
        return {"access_token": "t", "shop_id": "1", "settings": {}}


class _Pending(_Etsy):
    access_pending_note = "Etsy hasn't seated your shop yet."

    def access_pending(self, _uid):
        return True


def _rec(rid):
    return {"id": rid, "user_id": "u1", "status": "published", "listing": {
        "title": f"Item {rid}", "description": "Nice.", "price": 20.0, "quantity": 1,
        "images": ["a.jpg"], "marketplaces": {"ebay": {"status": "published"}}}}


@pytest.fixture
def api(monkeypatch, every_marketplace):
    from backend import main

    store = {rid: _rec(rid) for rid in ("a", "b")}
    gate = {"provider": _Etsy()}
    held = {"go": False}

    monkeypatch.setattr(main, "_uid", lambda _r: "u1")
    monkeypatch.setattr(main.marketplaces, "get",
                        lambda key: gate["provider"] if key == "etsy" else None)
    monkeypatch.setattr(main.db, "get_listings",
                        lambda ids, uid: [store[i] for i in ids if i in store])
    monkeypatch.setattr(main.db, "get_listing", lambda rid: store.get(rid))
    monkeypatch.setattr(main.db, "mutate_listing_data",
                        lambda rid, fn, **kw: fn(dict(store[rid]["listing"])))
    monkeypatch.setattr(main, "_adopt_imported_images", lambda *a, **k: None)
    monkeypatch.setattr(main, "CROSSPOST_PACE_SECONDS", 0)
    sent: list[str] = []

    def _publish(sid, *a, **k):
        while not held["go"]:
            time.sleep(0.005)
        sent.append(sid)
        return {"etsy": PublishOutcome(ok=True, listing_id="e1", status="draft",
                                       message="ok")}

    monkeypatch.setattr(main, "_publish_targets", _publish)
    jobstore._JOBS.clear()
    main._CROSSPOST_JOBS.clear()
    return TestClient(main.app), gate, held, sent


def _press(client):
    return client.post("/api/crosspost/etsy/start", json={
        "items": [{"id": "a", "etsy": {}}, {"id": "b", "etsy": {}}], "mode": "draft"})


def test_the_second_press_is_handed_the_first_run(api):
    client, _gate, held, sent = api
    first = _press(client).json()
    second = _press(client).json()
    assert second["job_id"] == first["job_id"]
    assert second["joined"] is True
    held["go"] = True
    for _ in range(600):
        if jobstore.snapshot(first["job_id"], "u1").get("done"):
            break
        time.sleep(0.01)
    assert sorted(sent) == ["a", "b"], "the same listings went twice"


def test_a_seller_etsy_has_not_seated_is_told_before_anything_runs(api):
    client, gate, _held, sent = api
    gate["provider"] = _Pending()
    resp = _press(client)
    assert resp.status_code == 400
    assert "seated" in resp.json()["detail"]
    assert sent == []


def test_a_crosspost_with_nothing_in_it_is_refused(api):
    client, *_ = api
    assert client.post("/api/crosspost/etsy/start",
                       json={"items": [], "mode": "draft"}).status_code == 400
    assert client.post("/api/crosspost/etsy/review",
                       json={"listing_ids": []}).status_code == 400

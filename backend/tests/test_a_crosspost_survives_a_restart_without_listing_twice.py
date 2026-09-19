"""A restart mid-crosspost must not put one item on Etsy twice.

The run is a loop over the seller's listings, so a deploy lands in the
middle of one. What matters is the listing that was IN FLIGHT: Etsy may
have created it before the process went away, and the app cannot tell —
so it is reported as unknown, the same answer every lost reply gets here,
and the seller is pointed at their Etsy drafts before anything is sent
again. The ones already finished are not re-sent, and the rest carry on.
"""
from __future__ import annotations

import time

import pytest

from backend.marketplaces.base import PublishOutcome
from backend.services import jobstore


def _rec(rid):
    return {"id": rid, "user_id": "u1", "status": "published", "listing": {
        "title": f"Item {rid}", "description": "Nice.", "price": 20.0, "quantity": 1,
        "images": ["a.jpg"], "marketplaces": {"ebay": {"status": "published"}}}}


@pytest.fixture
def resume(monkeypatch, every_marketplace):
    from backend import main

    store = {rid: _rec(rid) for rid in ("a", "b", "c", "d")}
    sent: list[str] = []
    monkeypatch.setattr(main.db, "get_listings",
                        lambda ids, uid: [store[i] for i in ids if i in store])
    monkeypatch.setattr(main.db, "get_listing", lambda rid: store.get(rid))
    monkeypatch.setattr(main.db, "mutate_listing_data",
                        lambda rid, fn, **kw: fn(dict(store[rid]["listing"])))
    monkeypatch.setattr(main, "_adopt_imported_images", lambda *a, **k: None)
    monkeypatch.setattr(main, "CROSSPOST_PACE_SECONDS", 0)
    monkeypatch.setattr(main, "_publish_targets",
                        lambda sid, *a, **k: (sent.append(sid), {
                            "etsy": PublishOutcome(ok=True, listing_id="e1",
                                                   status="draft", message="ok")})[1])
    jobstore._JOBS.clear()
    main._CROSSPOST_JOBS.clear()
    return main, sent


# What the mirror on disk held when the process died: "a" was finished,
# "b" was being sent, "c" and "d" had not started.
MIRROR = {"id": "job-1", "kind": "crosspost-etsy", "_uid": "u1", "_mode": "draft",
          "_ids": ["a", "b", "c", "d"], "_done": ["a"], "_inflight": ["b"],
          "done": False, "phase": "publishing", "current": 1, "total_items": 4}


def _finish(main, job_id="job-1"):
    for _ in range(600):
        snap = jobstore.snapshot(job_id, "u1")
        if snap and snap.get("done"):
            return snap
        time.sleep(0.01)
    raise AssertionError("the resumed job never finished")


def test_the_one_in_flight_is_never_sent_again(resume):
    main, sent = resume
    assert main._resume_interrupted_crossposts([dict(MIRROR)]) == {"job-1"}
    _finish(main)
    assert "b" not in sent, "the listing in flight was sent a second time"
    assert "a" not in sent, "a finished listing was sent again"
    assert sent == ["c", "d"]


def test_it_says_to_check_etsy_before_trying_that_one_again(resume):
    main, _sent = resume
    main._resume_interrupted_crossposts([dict(MIRROR)])
    rows = {r["id"]: r for r in _finish(main)["items"]}
    assert rows["b"]["status"] == "unknown"
    assert "drafts" in rows["b"]["message"].lower()
    assert rows["a"]["status"] == "done"
    assert rows["c"]["status"] == "draft"


def test_a_run_the_seller_stopped_is_not_a_second_chance(resume):
    main, sent = resume
    assert main._resume_interrupted_crossposts(
        [{**MIRROR, "_cancel": True}]) == set()
    assert main._resume_interrupted_crossposts(
        [{**MIRROR, "done": True}]) == set()
    assert sent == []


def test_the_settle_pass_picks_it_up_with_the_photo_batches(resume, monkeypatch):
    """One boot hook settles every interrupted job; a crosspost must be in
    it, or a restart silently drops the rest of the seller's run."""
    main, sent = resume
    monkeypatch.setattr(main.tokens, "refund_all", lambda *a, **k: 0)
    main._settle_interrupted_jobs([dict(MIRROR)])
    _finish(main)
    assert sent == ["c", "d"]

"""A partial sweep must say it is partial.

Re-checking a listing's status is one eBay call, so a big store is
deliberately only SAMPLED — 100 at a time, chosen randomly so every listing
gets its turn across successive syncs rather than the same first hundred
being re-checked forever. That trade is right; the quota is finite.

What was wrong is that the answer did not admit it. The response reported
`checked` and nothing else, so 100 out of a 400-listing store and 100 out of
a 100-listing store were indistinguishable — and this is the pass that runs
behind a button called "Sync with eBay". A caller reading `checked` as "the
store" is reading partial coverage as complete, which is how a seller ends up
trusting a status that was never looked at.

So the sweep now reports what it COULD have covered alongside what it did,
and a flag saying whether those differ.
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


def _live(n: int) -> list[dict]:
    return [{"id": f"ebay-{i}", "status": "published",
             "listing": {"ebay_listing_id": str(i), "source": "ebay"}}
            for i in range(n)]


@pytest.fixture()
def sweep(monkeypatch):
    """Run the sweep against a store of N live listings, without eBay."""
    from backend import auth, db, main
    from backend.services import listing_sync, sync_guard

    def _run(store_size: int) -> dict:
        records = _live(store_size)
        monkeypatch.setattr(auth, "current_user",
                            lambda _r: {"id": "u1", "email": "a@b.c"})
        monkeypatch.setattr(db, "list_listings", lambda **_k: records)
        monkeypatch.setattr(main, "_ebay_creds_for",
                            lambda _r: {"access_token": "tok", "_uid": "u1",
                                        "ebay_username": ""})
        monkeypatch.setattr(sync_guard, "sweep_due", lambda *_a, **_k: True)
        monkeypatch.setattr(listing_sync, "reconcile_recent",
                            lambda *_a, **_k: (0, set()))
        # The sweep itself does nothing; this is about what it REPORTS.
        monkeypatch.setattr(listing_sync, "refresh_statuses",
                            lambda *_a, **_k: 0)

        from fastapi.testclient import TestClient
        resp = TestClient(main.app).post("/api/ebay/sync-listings",
                                         json={"force": True})
        assert resp.status_code == 200, resp.text
        return resp.json()

    return _run


def test_a_small_store_is_covered_completely_and_says_so(sweep):
    body = sweep(12)

    assert body["eligible"] == 12
    assert body["checked"] == 12
    assert body["partial"] is False


def test_a_big_store_is_sampled_and_admits_it(sweep):
    """The finding: 100 of 400 answered identically to 100 of 100."""
    from backend import main

    body = sweep(400)

    assert body["eligible"] == 400
    assert body["checked"] == main.SWEEP_SAMPLE
    assert body["partial"] is True, \
        "a sampled sweep reported itself as complete coverage"


def test_the_sample_size_is_reported_so_a_caller_can_explain_it(sweep):
    """"Checked 100 of 400" needs all three numbers to be sayable."""
    from backend import main

    body = sweep(400)
    assert body["sample_size"] == main.SWEEP_SAMPLE
    assert body["checked"] <= body["eligible"]


def test_a_store_exactly_at_the_limit_is_not_partial(sweep):
    from backend import main

    body = sweep(main.SWEEP_SAMPLE)
    assert body["partial"] is False
    assert body["checked"] == body["eligible"]


def test_an_empty_store_is_not_partial(sweep):
    body = sweep(0)
    assert body["eligible"] == 0
    assert body["partial"] is False

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

    def _run(store_size: int, drafts_first: int = 0) -> dict:
        """`drafts_first` puts that many DRAFT records ahead of the live ones,
        which is how a real store fills the read cap: list_listings returns
        most-recent-first, so on a big enough store the older live listings
        fall off the end entirely."""
        records = ([{"id": f"d{i}", "status": "draft", "listing": {}}
                    for i in range(drafts_first)] + _live(store_size))
        monkeypatch.setattr(auth, "current_user",
                            lambda _r: {"id": "u1", "email": "a@b.c"})
        def _list(limit=50, user_id=None, statuses=None):
            # Honours `statuses`, because the real one does: the route asks
            # for live listings, and a double that ignored the filter would
            # keep testing the world where a page of drafts pushes a seller's
            # live listings out of the sweep. That world is the bug.
            rows = records if statuses is None else [
                r for r in records if r["status"] in statuses]
            return rows[:limit]

        monkeypatch.setattr(db, "list_listings", _list)
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


# --------------------------------------------- and the read cap counts too
#
# `partial` was computed from the SAMPLE alone. The list it samples is itself
# a capped read (LISTING_LIST_CAP), so a store bigger than the cap has live
# listings that never reach the sweep at all -- and nothing said so.
#
# The cap is now measured in LIVE listings rather than records of any kind,
# because the read asks for live ones. Both halves of that are asserted below:
# a genuinely capped sweep still says `partial`, and drafts no longer displace
# anybody's live listings, which was the state a real store sits in.

def test_a_store_bigger_than_the_read_cap_is_partial(sweep):
    from backend import main

    body = sweep(main.LIST_CAP + 5)

    # Past the cap of LIVE listings, so some never reached the sweep at all.
    # Reporting a complete sync here says every listing's status was confirmed
    # when hundreds were never looked at.
    assert body["partial"] is True, \
        "a sweep that could not even READ the whole store called itself complete"


def test_drafts_no_longer_push_live_listings_out_of_the_sweep(sweep):
    """The fix, stated as a test. `list_listings` is most-recent-first, so a
    store whose newest LIST_CAP records are drafts used to hide EVERY live
    listing from every sweep, for as long as it stayed that shape: a sale or
    an ending on eBay was never noticed here. The read asks for live listings
    now, so the drafts are irrelevant to it."""
    from backend import main

    body = sweep(5, drafts_first=main.LIST_CAP)
    assert body["eligible"] == 5, "the drafts ahead of them hid the live ones"
    assert body["partial"] is False


def test_the_cap_and_the_sample_are_both_honoured(sweep):
    """Past the cap AND sampled. Still one flag, still true."""
    from backend import main

    body = sweep(main.LIST_CAP + 400)
    assert body["partial"] is True


def test_a_store_that_fits_is_still_reported_complete(sweep):
    """The flag has to stay meaningful: a store the read covered entirely,
    swept entirely, is not partial."""
    body = sweep(12, drafts_first=5)

    assert body["eligible"] == 12
    assert body["partial"] is False

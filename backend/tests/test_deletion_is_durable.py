""""We deleted your photos" has to survive the process saying it.

Deleting an account drops the database rows and then hands the photos to a
background thread: one pass over every listing, deleting the local directory
and the R2 prefix. Nothing records that the pass is owed and nothing checks
that it finished. A deploy, a restart, an OOM or a crash part-way through
leaves the rest of the seller's photos in the bucket — indefinitely, with the
rows that named them already gone, so nothing will ever look for them again.

By then the app has told the seller their account is deleted and the privacy
policy has promised the photos went with it. A purge that only usually
happens is not the promise that was made, and object storage is exactly where
that matters: the objects are still fetchable by anyone holding a URL.

So what is owed is written down, in the SAME transaction that deletes the
rows — either both happen or neither, with no window where the rows are gone
and the debt is not recorded — and a resume pass retries whatever is still
outstanding.

The same pass picks up eBay's account-deletion notices. `pending_deletion_notices`
was written for that and never called, so a notice interrupted between "we
acknowledged it" and "we erased it" stayed pending forever. eBay stops
resending once acknowledged, so nothing else was ever going to.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def store(dbmod):
    """A real SQLite database (conftest's `dbmod`), so the transaction
    boundary these tests turn on is a real one and not a mock."""
    return dbmod


def _a_user(db, uid="u1"):
    db.create_user(uid, f"{uid}@example.com", "hash")
    db.upsert_listing(f"{uid}-l1", {"title": "Lamp"}, status="draft", user_id=uid)
    db.upsert_listing(f"{uid}-l2", {"title": "Chair"}, status="draft", user_id=uid)


# ------------------------------------------------- the debt is recorded

def test_deleting_a_user_records_the_photos_still_owed(store):
    _a_user(store)

    ids = store.delete_user("u1")

    assert sorted(ids) == ["u1-l1", "u1-l2"]
    owed = {p["listing_id"] for p in store.pending_media_purges()}
    assert owed == {"u1-l1", "u1-l2"}, \
        "the rows were deleted with no record that their photos still exist"


def test_the_debt_and_the_deletion_are_one_transaction(store, monkeypatch):
    """If recording what is owed fails, the deletion must not happen either.
    A user whose rows are gone with no purge record is the exact state this
    exists to prevent, and it cannot be detected afterwards."""
    _a_user(store)

    def _boom(*_a, **_k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(store, "_queue_media_purges", _boom)

    assert store.delete_user("u1") is None
    assert store.get_user_by_id("u1") is not None, \
        "the user was deleted even though the purge record failed"


def test_a_finished_purge_stops_being_owed(store):
    _a_user(store)
    store.delete_user("u1")

    store.finish_media_purge("u1-l1")

    assert {p["listing_id"] for p in store.pending_media_purges()} == {"u1-l2"}


def test_a_failed_purge_stays_owed_and_records_why(store):
    _a_user(store)
    store.delete_user("u1")

    store.note_media_purge_failure("u1-l1", "R2 timed out")

    rows = {p["listing_id"]: p for p in store.pending_media_purges()}
    assert "u1-l1" in rows, "a failed purge was written off as done"
    assert rows["u1-l1"]["attempts"] == 1
    assert "R2 timed out" in rows["u1-l1"]["last_error"]


def test_a_purge_that_never_succeeds_is_still_visible(store):
    """It must not disappear after N tries. Nothing else remembers these
    objects exist — the listing row that named them is gone."""
    _a_user(store)
    store.delete_user("u1")

    for _ in range(25):
        store.note_media_purge_failure("u1-l1", "still failing")

    rows = {p["listing_id"]: p for p in store.pending_media_purges()}
    assert "u1-l1" in rows
    assert rows["u1-l1"]["attempts"] == 25


# ------------------------------------------------------- the resume pass

def test_the_resume_pass_retries_what_is_outstanding(store, monkeypatch):
    from backend.services import deletion_queue

    _a_user(store)
    store.delete_user("u1")

    purged = []
    result = deletion_queue.run_pending(purge_media=purged.append)

    assert sorted(purged) == ["u1-l1", "u1-l2"]
    assert result["media"] == 2
    assert store.pending_media_purges() == []


def test_one_object_that_will_not_delete_does_not_block_the_others(store):
    from backend.services import deletion_queue

    _a_user(store)
    store.delete_user("u1")

    def _purge(lid):
        if lid == "u1-l1":
            raise RuntimeError("R2 refused")

    deletion_queue.run_pending(purge_media=_purge)

    still = {p["listing_id"] for p in store.pending_media_purges()}
    assert still == {"u1-l1"}, "a failure took the whole pass down with it"


def test_a_second_pass_finishes_what_the_first_could_not(store):
    from backend.services import deletion_queue

    _a_user(store)
    store.delete_user("u1")
    deletion_queue.run_pending(purge_media=_fails_once())

    purged = []
    deletion_queue.run_pending(purge_media=purged.append)
    assert store.pending_media_purges() == []


def _fails_once():
    def _purge(_lid):
        raise RuntimeError("transient")
    return _purge


def test_the_pass_is_safe_to_run_with_nothing_owed(store):
    from backend.services import deletion_queue

    assert deletion_queue.run_pending(purge_media=lambda _l: None)["media"] == 0


# --------------------------------------- the eBay notice half, now wired up

def test_an_interrupted_ebay_notice_is_picked_back_up(store, monkeypatch):
    """`pending_deletion_notices` existed and nothing called it. eBay stops
    resending once acknowledged, so a notice stuck between "recorded" and
    "erased" was never going to be finished by anyone else."""
    from backend.services import deletion_queue

    store.record_deletion_notice("n-1", "EBAYUSER-9", "digest")

    seen = []
    monkeypatch.setattr(deletion_queue.ebay_deletion, "purge",
                        lambda uid, purge_media=None: seen.append(uid)
                        or {"users": 0, "listings": 0, "state": "no_match"})

    result = deletion_queue.run_pending(purge_media=lambda _l: None)

    assert seen == ["EBAYUSER-9"]
    assert result["notices"] == 1
    assert store.pending_deletion_notices() == []


def test_a_notice_that_cannot_be_completed_stays_pending(store, monkeypatch):
    from backend.services import deletion_queue

    store.record_deletion_notice("n-2", "EBAYUSER-9", "digest")
    monkeypatch.setattr(deletion_queue.ebay_deletion, "purge",
                        lambda uid, purge_media=None: {
                            "users": 0, "listings": 0, "state": "failed",
                            "error": "db unavailable"})

    deletion_queue.run_pending(purge_media=lambda _l: None)

    assert [n["notification_id"] for n in store.pending_deletion_notices()] == ["n-2"]


# ------------------------------------------- the notice path keeps it accurate

def test_the_notice_path_clears_the_queue_it_filled(store, monkeypatch):
    """delete_user queues every listing's photos, whichever route called it.
    The eBay notice path purges them inline, so it has to clear the rows —
    otherwise the resume pass re-purges prefixes that are already gone on
    every tick, forever."""
    from backend.services import ebay_deletion

    _a_user(store)
    store.save_ebay_account("u1", ebay_user_id="EBAYUSER-9",
                            refresh_token="tok")

    result = ebay_deletion.purge("EBAYUSER-9", purge_media=lambda _l: None)

    assert result["state"] == "done"
    assert store.pending_media_purges() == []


def test_a_purge_the_notice_path_could_not_do_stays_queued(store):
    from backend.services import ebay_deletion

    _a_user(store)
    store.save_ebay_account("u1", ebay_user_id="EBAYUSER-9",
                            refresh_token="tok")

    def _refuse(_lid):
        raise RuntimeError("R2 down")

    ebay_deletion.purge("EBAYUSER-9", purge_media=_refuse)

    owed = {p["listing_id"] for p in store.pending_media_purges()}
    assert owed == {"u1-l1", "u1-l2"}, \
        "photos the notice path failed to erase were forgotten"


def test_the_backlog_is_countable_without_naming_anyone(store):
    """It goes in the operator diagnostics, and the ids belong to people who
    asked to be forgotten."""
    from backend.services import deletion_queue

    _a_user(store)
    store.delete_user("u1")

    backlog = deletion_queue.backlog()
    assert backlog == {"media_purges": 2, "deletion_notices": 0}
    assert "u1" not in str(backlog)


# ------------------------------------------- the debt must be cheap to record

def test_recording_the_debt_does_not_cost_a_round_trip_per_listing(store):
    """Queued row-by-row this was one SELECT and one INSERT per listing,
    inside the open delete transaction. On SQLite that is merely slow; against
    a cross-region Postgres a 2,000-listing account is thousands of serial
    round trips, which is long enough to hit a statement timeout — and then
    the whole deletion rolls back and the seller is told it failed.

    The bound is on statement COUNT rather than wall time: a timing assertion
    would pass on a laptop and say nothing about the database this actually
    runs against.
    """
    from sqlalchemy import event

    _a_user(store)
    for i in range(200):
        store.upsert_listing(f"u1-x{i}", {"title": "x"}, status="draft",
                             user_id="u1")

    eng = store._get_engine()
    statements: list[int] = []

    def _count(*_a, **_k):
        statements.append(1)

    event.listen(eng, "before_cursor_execute", _count)
    try:
        assert len(store.delete_user("u1")) == 202
    finally:
        event.remove(eng, "before_cursor_execute", _count)

    # Generous, and still an order of magnitude below per-row: the fixed cost
    # is the eight table deletes plus a couple of statements per batch.
    assert len(statements) < 40, (
        f"{len(statements)} statements to delete 202 listings — the purge "
        "queue is being written a row at a time")

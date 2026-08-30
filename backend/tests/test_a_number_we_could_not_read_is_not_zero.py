"""Two more reads that answered a failure with a believable number.

Both are the same shape as the prefs bug next door, and both sit on a surface
where the number is the whole point of looking.

**The token ledger.** `GET /api/tokens/history` answered a broken database
with `{"entries": []}` and a 200, and the dialog renders that as *"Nothing yet
— your AI activity will show up here."* That is the screen a seller opens to
find out whether they were charged for the identify that just failed, and it
told them they were not. The dialog already has the other branch — it sets
`history.error` and shows "Couldn't load your activity" — so, exactly as with
the prefs panel, the guard existed and the server never let it fire.

**The deletion backlog.** `/api/admin/diagnostics` reports `deletion_backlog`,
and this repo's own release notes tell the operator to watch it: *"a number
that does not come back down is a promise already made to somebody."* Both
counts came from reads that returned `[]` on failure, so during the outage
where an operator is most likely to be looking, the answer was **zero owed** —
a clean bill on an erasure obligation, produced by not being able to ask.

The backlog is deliberately NOT made to raise. Taking the whole diagnostics
page down because one table is unreadable is worse than the problem: that page
is what an operator reads during an outage. It reports `null` for a count it
could not take, which is not a number and cannot be mistaken for one.

The drain loop keeps the tolerant read on purpose too — for a worker, "nothing
to do this pass" and "could not look" lead to the same action, and the next
pass tries again.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit
from backend.services import deletion_queue


@pytest.fixture()
def seller(dbmod):
    assert dbmod.enabled()
    ratelimit.reset()
    client = TestClient(main.app)
    r = client.post("/api/auth/signup",
                    json={"email": "ledger@example.com", "password": "password123"})
    assert r.status_code < 400, r.text
    return client


def _break(monkeypatch, module, name: str) -> None:
    """Make one db call answer the way it should when storage is down.

    The typed failure, not a bare exception: these are route-level guards
    asking whether the route adds a swallow of its own. That the failure is
    typed at ALL, rather than returned as `[]`, is the db half, and it is
    asserted separately against a broken Session below.
    """
    def boom(*_a, **_k):
        raise errors.StorageUnavailable(
            "We couldn’t load your activity just now. Try again in a moment.")
    monkeypatch.setattr(module, name, boom)


# --- the ledger ------------------------------------------------------------

def test_an_unreadable_ledger_is_not_an_empty_one(seller, monkeypatch):
    _break(monkeypatch, main.db, "token_history")
    r = seller.get("/api/tokens/history")
    assert r.status_code == 503, (
        f"answered {r.status_code} {r.text[:120]} — the dialog renders that as "
        "'Nothing yet', on the screen where someone checks whether they paid")


def test_the_ledger_failure_reads_as_english(seller, monkeypatch):
    _break(monkeypatch, main.db, "token_history")
    detail = seller.get("/api/tokens/history").json().get("detail", "")
    assert "connection reset" not in detail
    assert "try again" in detail.lower()


def test_a_seller_with_no_activity_still_gets_an_empty_ledger(seller):
    """The other half: a genuinely empty ledger is a real answer, and the
    "Nothing yet" copy is right for it."""
    r = seller.get("/api/tokens/history")
    assert r.status_code == 200
    assert r.json()["entries"] == []


def test_the_reader_itself_stops_swallowing(dbmod, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("connection reset by peer")
    monkeypatch.setattr(dbmod, "Session", boom)
    with pytest.raises(errors.StorageUnavailable):
        dbmod.token_history("someone")


# --- the deletion backlog --------------------------------------------------

def test_a_backlog_we_could_not_count_is_not_zero(monkeypatch):
    def boom(*_a, **_k):
        raise errors.StorageUnavailable("down")
    monkeypatch.setattr(deletion_queue.db, "count_pending_media_purges", boom)

    got = deletion_queue.backlog()
    assert got["media_purges"] is None, (
        f"reported {got['media_purges']!r} for a count it could not take — "
        "an operator reads that as nothing owed")


def test_one_unreadable_count_does_not_hide_the_other(monkeypatch):
    def boom(*_a, **_k):
        raise errors.StorageUnavailable("down")
    monkeypatch.setattr(deletion_queue.db, "count_pending_media_purges", boom)
    monkeypatch.setattr(deletion_queue.db, "count_pending_deletion_notices",
                        lambda: 1)

    got = deletion_queue.backlog()
    assert got["media_purges"] is None
    assert got["deletion_notices"] == 1


def test_the_diagnostics_page_still_answers_during_the_outage(monkeypatch):
    """The reason backlog() does not raise. This page is what an operator
    reads WHILE things are broken; refusing it then is the wrong trade."""
    def boom(*_a, **_k):
        raise errors.StorageUnavailable("down")
    monkeypatch.setattr(deletion_queue.db, "count_pending_media_purges", boom)
    monkeypatch.setattr(deletion_queue.db, "count_pending_deletion_notices", boom)

    got = deletion_queue.backlog()
    assert got == {"media_purges": None, "deletion_notices": None}


def test_a_real_backlog_is_still_a_number(monkeypatch):
    monkeypatch.setattr(deletion_queue.db, "count_pending_media_purges",
                        lambda: 2)
    monkeypatch.setattr(deletion_queue.db, "count_pending_deletion_notices",
                        lambda: 0)
    assert deletion_queue.backlog() == {"media_purges": 2, "deletion_notices": 0}


def test_the_drain_loop_keeps_going_when_the_queue_cannot_be_read(monkeypatch):
    """A worker that cannot read the queue has nothing to do this pass, which
    is the same action as an empty queue. It must not take the process with
    it — the next pass looks again."""
    def boom(*_a, **_k):
        raise errors.StorageUnavailable("down")
    monkeypatch.setattr(deletion_queue.db, "count_pending_media_purges", boom)
    monkeypatch.setattr(deletion_queue.db, "count_pending_deletion_notices", boom)

    assert deletion_queue.run_pending(purge_media=lambda _lid: None) == {
        "media": 0, "notices": 0}


def test_the_counters_themselves_stop_swallowing(dbmod, monkeypatch):
    """Where the backlog's zero came from.

    The drain loop's own reader keeps returning `[]` on a failure and that is
    still right -- a worker that cannot look has nothing to do this pass. The
    counters are a separate pair precisely so the REPORT can tell the
    difference, and they are only worth anything if they actually raise.
    """
    def boom(*_a, **_k):
        raise RuntimeError("connection reset by peer")
    monkeypatch.setattr(dbmod, "Session", boom)

    with pytest.raises(errors.StorageUnavailable):
        dbmod.count_pending_media_purges()
    with pytest.raises(errors.StorageUnavailable):
        dbmod.count_pending_deletion_notices()


def test_the_counters_agree_with_the_queue_they_count(dbmod):
    """A count that drifts from the list is a number nobody can act on."""
    assert dbmod.count_pending_media_purges() == len(dbmod.pending_media_purges())
    assert (dbmod.count_pending_deletion_notices()
            == len(dbmod.pending_deletion_notices()))


def test_the_drain_loops_reader_is_deliberately_still_tolerant(dbmod, monkeypatch):
    """Stated as a test so the next person does not "fix" it to match.

    `pending_media_purges` answering `[]` on a failure is the documented
    choice: inventing work is worse than skipping a round, and the next pass
    looks again. It is safe here and unsafe in `backlog()` for one reason --
    a worker acts on it, an operator BELIEVES it.
    """
    def boom(*_a, **_k):
        raise RuntimeError("connection reset by peer")
    monkeypatch.setattr(dbmod, "Session", boom)

    assert dbmod.pending_media_purges() == []
    assert dbmod.pending_deletion_notices() == []

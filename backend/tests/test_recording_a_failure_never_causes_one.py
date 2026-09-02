"""The error sink must never be the reason a request fails.

This is the one write path in db.py that swallows instead of raising, and the
inversion is deliberate. db.admin_audit raises because an admin action nobody
can write down must not run. Here the failure has ALREADY happened to a
seller; turning it into a second, unhandled one to complain about the
bookkeeping helps nobody and loses the request as well as the row.

Three ways this could go wrong, all of them tested here.

**Recursion.** Every `except` in db.py logs a warning. Recording touches db.
So a database outage means: warning → capture handler → failed write → warning
→ capture handler, forever, at the exact moment production is already having a
bad day. The re-entrancy guard is the whole answer and has no other purpose.

**Blocking.** These are called from paths that are already going badly, and a
synchronous Postgres INSERT inside a log call would sit on the event loop. The
queue is bounded, and full means DROP — but a sink that quietly loses rows
while claiming to be a record of what happened is worse than no sink, because
it is believed. So the drop is counted and the count is readable.

**Raising.** record() is called from inside exception handlers. Anything it
raises replaces the failure being reported with a less useful one.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from backend import config
from backend.services import errorlog


def test_a_dead_database_does_not_raise(dbmod, monkeypatch):
    def _no(*_a, **_k):
        raise RuntimeError("neon is gone")

    monkeypatch.setattr(dbmod, "_get_engine", _no)
    assert dbmod.record_error_event(fingerprint="x", message="y") is False
    assert dbmod.mark_error_fixed("x") is False
    assert dbmod.prune_error_events(30) == 0


def test_a_dead_database_does_not_recurse(dbmod, monkeypatch):
    """The failure mode that would take production down rather than merely
    lose a row: every db failure logs, and every log would record."""
    calls = {"n": 0}

    def _explode(*_a, **_k):
        calls["n"] += 1
        assert calls["n"] < 50, "the sink is feeding itself"
        config.log.warning("db: read failed: %s", "boom")
        raise RuntimeError("neon is gone")

    monkeypatch.setattr(dbmod, "_get_engine", _explode)
    errorlog.install()
    config.log.warning("something went wrong: %s", "detail")
    errorlog.flush()


def test_record_returns_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr(errorlog, "fingerprint",
                        lambda **_k: (_ for _ in ()).throw(RuntimeError("no")))
    assert errorlog.record(message="anything") is None


def test_a_full_queue_drops_and_says_so(monkeypatch):
    """Bounded, because an unbounded queue turns an outage into an OOM."""
    before = errorlog.stats()["dropped"]
    monkeypatch.setattr(errorlog._queue, "put_nowait",
                        lambda _item: (_ for _ in ()).throw(
                            __import__("queue").Full()))

    assert errorlog.record(message="dropped on the floor") is not None
    assert errorlog.stats()["dropped"] == before + 1


def test_recording_does_not_write_on_the_calling_thread(dbmod, monkeypatch):
    """record() queues; only flush() and the writer thread touch the database."""
    wrote = []
    monkeypatch.setattr(dbmod, "record_error_event",
                        lambda **kw: wrote.append(kw) or True)

    errorlog.record(message="queued only")
    assert wrote == []

    errorlog.flush()
    assert len(wrote) == 1


def test_the_sink_can_be_turned_off_without_a_deploy(monkeypatch):
    """New machinery on the hot path of every log call needs an off switch."""
    monkeypatch.setattr(config, "ERROR_CAPTURE_ENABLED", False)
    assert errorlog.record(message="ignored") is None


def test_the_reader_still_raises(dbmod, monkeypatch):
    """The writer swallows; the REPORT does not. A console that renders zero
    errors because it could not read is lying about the thing it exists for."""
    from backend.errors import StorageUnavailable

    def _no(*_a, **_k):
        raise RuntimeError("neon is gone")

    monkeypatch.setattr(dbmod, "Session", _no)
    with pytest.raises(StorageUnavailable):
        dbmod.error_events_list()

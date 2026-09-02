"""Yesterday's failures survive the pruning of the table that held them.

`error_events` is pruned after ERROR_TTL_DAYS so a fixed bug stops being listed
forever, and Fly's own retained window is measured in hours. Between them,
nothing here would remember what production was doing two months ago — which is
fine for triage and wrong for every question asked after an incident: was this
happening before the deploy, is this the same failure as the one in July, how
long had it been building.

The prefix is the part most worth pinning. `sessions/` is walked by
objstore.delete_prefix on the reclaim sweep AND by the strict delete an account
erasure runs. A log archive under that prefix would be destroyed by
housekeeping and would place log data inside the scope of somebody's
right-to-erasure request. Wrong in both directions, and silently so.
"""
from __future__ import annotations

import datetime
import gzip
import json

import pytest

pytest.importorskip("sqlalchemy")

from backend import objstore
from backend.services import logarchive


def test_the_key_is_dated_and_sorts():
    key = logarchive.key_for(datetime.date(2026, 9, 1))
    assert key == "ops/errors/2026/09/01.jsonl.gz"
    assert logarchive.key_for(datetime.date(2026, 8, 31)) < key


def test_the_archive_never_lands_under_a_prefix_housekeeping_deletes():
    key = logarchive.key_for(datetime.date(2026, 9, 1))
    assert not key.startswith("sessions/")
    assert key.startswith("ops/")


def test_the_bundle_is_one_json_object_per_line():
    """JSONL so the archive can be read a line at a time, years later,
    without parsing the whole file or knowing what else is in it."""
    rows = [{"fingerprint": "a1", "message": "boom", "count": 3},
            {"fingerprint": "b2", "message": "other", "count": 1}]
    lines = gzip.decompress(logarchive.bundle(rows)).decode().splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["fingerprint"] == "a1"


def test_a_value_json_cannot_name_does_not_lose_the_bundle(monkeypatch):
    """A datetime, a Decimal — the row dict is not curated before it gets here."""
    body = logarchive.bundle([{"seen": datetime.datetime(2026, 9, 1)}])
    assert "2026-09-01" in gzip.decompress(body).decode()


def test_it_does_nothing_when_there_is_no_object_store(monkeypatch):
    monkeypatch.setattr(objstore, "enabled", lambda: False)
    assert logarchive.archive_day() is False


def test_a_failed_upload_is_not_an_incident(dbmod, monkeypatch):
    """It runs inside the thread recording live failures. Raising there would
    trade the whole error log for a missed backup."""
    dbmod.record_error_event(fingerprint="aa11", message="x", severity="high")
    monkeypatch.setattr(objstore, "enabled", lambda: True)
    monkeypatch.setattr(objstore, "put_bytes",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            RuntimeError("R2 is unreachable")))

    assert logarchive.archive_day() is False


def test_it_writes_the_day_it_was_asked_for(dbmod, monkeypatch):
    dbmod.record_error_event(fingerprint="aa11", message="x", severity="high")
    written = {}
    monkeypatch.setattr(objstore, "enabled", lambda: True)
    monkeypatch.setattr(objstore, "put_bytes",
                        lambda data, key, ctype: written.update(
                            data=data, key=key, ctype=ctype))

    assert logarchive.archive_day(datetime.date(2026, 9, 1)) is True
    assert written["key"] == "ops/errors/2026/09/01.jsonl.gz"
    assert written["ctype"] == "application/gzip"
    assert "aa11" in gzip.decompress(written["data"]).decode()


def test_an_empty_day_writes_nothing_at_all(dbmod, monkeypatch):
    """No object, rather than an empty one: a bucket full of empty files is
    a cost and a lie about what was checked."""
    calls = []
    monkeypatch.setattr(objstore, "enabled", lambda: True)
    monkeypatch.setattr(objstore, "put_bytes",
                        lambda *a, **k: calls.append(a))

    assert logarchive.archive_day() is False
    assert calls == []

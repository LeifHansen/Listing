"""A second archive of the same day adds to it, never replaces it.

The error writer archives "yesterday" on every boot (its daily timer starts
at zero) and reads the failures seen in the 48 hours before NOW. A deploy late
in the day wrote yesterday's object again with a window that had moved on, so
a failure seen early yesterday, and not since, was in the first write and in
neither file after the second. The table that still held it is pruned after
ERROR_TTL_DAYS, and then it was gone for good -- which is exactly the question
the archive exists to answer.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")

from backend import objstore  # noqa: E402
from backend.services import logarchive  # noqa: E402


@pytest.fixture
def bucket(monkeypatch):
    held: dict[str, bytes] = {}
    monkeypatch.setattr(objstore, "enabled", lambda: True)
    monkeypatch.setattr(objstore, "put_bytes",
                        lambda data, key, ctype: held.__setitem__(key, data))

    def _get(key):
        if key not in held:
            raise KeyError(key)  # what a missing object looks like: a raise
        return held[key]

    monkeypatch.setattr(objstore, "get_bytes", _get)
    return held


def _rows(held, key):
    return [json.loads(line) for line in
            gzip.decompress(held[key]).decode().splitlines()]


def _feed(monkeypatch, rows):
    from backend import db
    monkeypatch.setattr(db, "error_events_list", lambda **_k: rows)


def test_a_failure_from_the_first_boot_survives_the_second(bucket, monkeypatch):
    _feed(monkeypatch, [{"fingerprint": "early", "count": 2, "last_seen": "D-1 01:00"},
                        {"fingerprint": "both", "count": 5, "last_seen": "D-1 02:00"}])
    assert logarchive.archive_day() is True

    # A deploy at the end of the day: the window has moved past "early".
    _feed(monkeypatch, [{"fingerprint": "both", "count": 9, "last_seen": "D 22:00"},
                        {"fingerprint": "late", "count": 1, "last_seen": "D 22:30"}])
    assert logarchive.archive_day() is True

    [key] = bucket
    rows = {r["fingerprint"]: r for r in _rows(bucket, key)}
    assert set(rows) == {"early", "both", "late"}
    assert rows["both"]["count"] == 9  # the newer read of a row wins


def test_an_unreadable_old_object_is_replaced_rather_than_lost_to(bucket, monkeypatch):
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    bucket[logarchive.key_for(yesterday)] = b"not gzip"
    _feed(monkeypatch, [{"fingerprint": "only", "count": 1}])
    assert logarchive.archive_day() is True
    [key] = bucket
    assert [r["fingerprint"] for r in _rows(bucket, key)] == ["only"]

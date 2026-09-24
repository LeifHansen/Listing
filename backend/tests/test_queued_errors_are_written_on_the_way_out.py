"""Failures still queued when the app stops are written, not dropped.

error_events is fed by a queue that a daemon thread drains every few
seconds. A daemon dies with the process, so whatever was queued at shutdown —
the last seconds before a deploy or a restart, often exactly the failures worth
reading — was lost. errorlog.flush() existed "as the honest way to drain at
shutdown" and nothing called it.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402


@pytest.fixture
def quiet_boot(monkeypatch):
    for name in ("_warm_models", "_adopt_job_mirrors"):
        monkeypatch.setattr(main, name, lambda: None)
    monkeypatch.setattr(main, "_in_background", lambda *a, **k: None)


def test_the_queue_is_flushed_on_shutdown(monkeypatch, quiet_boot):
    flushed = []
    monkeypatch.setattr(main.errorlog, "writer_started", lambda: True)
    monkeypatch.setattr(main.errorlog, "flush", lambda: flushed.append(1) or 0)
    with TestClient(main.app):
        assert flushed == []
    assert flushed == [1]


def test_a_process_with_no_writer_has_nothing_to_flush(monkeypatch, quiet_boot):
    flushed = []
    monkeypatch.setattr(main.errorlog, "writer_started", lambda: False)
    monkeypatch.setattr(main.errorlog, "flush", lambda: flushed.append(1) or 0)
    with TestClient(main.app):
        pass
    assert flushed == []


def test_a_flush_that_fails_does_not_fail_the_shutdown(monkeypatch, quiet_boot):
    def boom():
        raise RuntimeError("database gone")
    monkeypatch.setattr(main.errorlog, "writer_started", lambda: True)
    monkeypatch.setattr(main.errorlog, "flush", boom)
    with TestClient(main.app):
        pass

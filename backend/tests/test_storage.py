"""Session-store naming and ordering helpers."""
from __future__ import annotations

import os

import pytest

from backend import storage


def test_safe_session_name_strips_to_alnum():
    assert storage.safe_session_name("ebay-168433981627") == "ebay168433981627"
    assert storage.safe_session_name("3aaeb40637a1") == "3aaeb40637a1"


def test_safe_session_name_rejects_empty():
    with pytest.raises(ValueError):
        storage.safe_session_name("../../")


def test_image_index():
    assert storage.image_index("img_000.jpg") == 0
    assert storage.image_index("img_017.jpg") == 17
    assert storage.image_index("cover.jpg") == -1


def test_natural_key_orders_numbers_numerically():
    names = ["img_10.jpg", "img_2.jpg", "img_100.jpg", "img_20.jpg"]
    assert sorted(names, key=storage.natural_key) \
        == ["img_2.jpg", "img_10.jpg", "img_20.jpg", "img_100.jpg"]


def test_sweep_orphan_sessions_reports_names_not_just_a_count(tmp_path, monkeypatch):
    """The caller purges the same sessions from R2, which needs their names:
    an upload reaches the bucket before any listing row exists, so nothing
    else can ever name those objects again."""
    import time as _time

    from backend import config

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions)

    old = _time.time() - 4 * 3600
    for name in ("keepme01", "orphan01", "orphan02", "freshone"):
        d = sessions / name
        (d / "optimized").mkdir(parents=True)
        (d / "optimized" / "img_000.jpg").write_bytes(b"x")
    for name in ("keepme01", "orphan01", "orphan02"):
        os.utime(sessions / name, (old, old))  # 'freshone' stays recent

    removed = storage.sweep_orphan_sessions({"keepme01"}, max_age_seconds=3 * 3600)

    assert sorted(removed) == ["orphan01", "orphan02"]
    assert (sessions / "keepme01").exists()   # a real listing
    assert (sessions / "freshone").exists()   # too recent — may be in flight
    assert not (sessions / "orphan01").exists()

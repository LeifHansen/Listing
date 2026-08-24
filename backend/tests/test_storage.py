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


def _age(root, when):
    """Backdate a whole session tree, deepest first, the way a real abandoned
    upload looks: nothing inside it has been touched either."""
    for p in sorted(root.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        os.utime(p, (when, when))
    os.utime(root, (when, when))


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
        # The whole tree, not just the top dir: a session is only idle when
        # nothing inside it has been touched either (see session_touched_at).
        _age(sessions / name, old)               # 'freshone' stays recent

    removed = storage.sweep_orphan_sessions({"keepme01"}, max_age_seconds=3 * 3600)

    assert sorted(removed) == ["orphan01", "orphan02"]
    assert (sessions / "keepme01").exists()   # a real listing
    assert (sessions / "freshone").exists()   # too recent — may be in flight
    assert not (sessions / "orphan01").exists()


def test_a_batch_still_writing_photos_is_not_swept(tmp_path, monkeypatch):
    """A session dir's own mtime only moves when an entry is added or removed
    from it DIRECTLY, and photos land a level down in original/. So a long
    batch's staging dir keeps the mtime it was created with, no matter how
    many photos it is still writing — and the sweep read that as "idle" and
    deleted the photos out from under the job that was mid-way through them."""
    import time as _time

    from backend import config

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions)

    staging = sessions / "batch001"
    (staging / "original").mkdir(parents=True)
    os.utime(staging, (_time.time() - 9 * 3600, _time.time() - 9 * 3600))
    # ...and it is right now writing the next photo of a long batch.
    (staging / "original" / "src_240.jpg").write_bytes(b"x")

    removed = storage.sweep_orphan_sessions(set(), max_age_seconds=3 * 3600)

    assert removed == [], "swept a batch that was still writing photos"
    assert (staging / "original" / "src_240.jpg").exists()


def test_a_genuinely_idle_orphan_is_still_swept(tmp_path, monkeypatch):
    """The other side of it — the sweep still has to reclaim the volume."""
    import time as _time

    from backend import config

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions)

    abandoned = sessions / "batch002"
    (abandoned / "original").mkdir(parents=True)
    (abandoned / "original" / "src_000.jpg").write_bytes(b"x")
    _age(abandoned, _time.time() - 9 * 3600)

    assert storage.sweep_orphan_sessions(set(), max_age_seconds=3 * 3600) == ["batch002"]
    assert not abandoned.exists()

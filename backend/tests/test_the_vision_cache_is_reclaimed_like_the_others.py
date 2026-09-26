"""The vision copies are reclaimed on the same timer as the other caches.

Every photo sent to a vision model gets a right-sized JPEG copy in its
session's vision/ dir (images.vision_copy). It is a cache — rebuilt from the
photo whenever it is missing or older than it — but nothing ever reclaimed it:
prune_originals swept original/ and as_shot/, prune_history swept history/, and
the R2 offload moves optimized/ only. So each copy sat on the 1GB volume for
the life of its listing, offloaded to R2 or not, and "Finish everything" over a
few hundred imported listings left hundreds of megabytes the reclaim daemon
could never get back.
"""
from __future__ import annotations

import os
import time

from backend import config, storage


def _file(path, size=1000, age_seconds=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_seconds:
        then = time.time() - age_seconds
        os.utime(path, (then, then))
    return path


def test_an_old_vision_copy_is_reclaimed_and_a_fresh_one_kept(tmp_path,
                                                              monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    old = _file(tmp_path / "s1" / "vision" / "img_000.jpg", 1500,
                age_seconds=10 * 3600)
    fresh = _file(tmp_path / "s1" / "vision" / "img_001.jpg", 700)
    photo = _file(tmp_path / "s1" / "optimized" / "img_000.jpg", 900,
                  age_seconds=10 * 3600)

    freed = storage.prune_originals(3600)

    assert not old.exists(), "a vision copy hours old was never reclaimed"
    assert fresh.exists()
    assert photo.exists(), "the photo itself is not a cache"
    assert freed == 1500

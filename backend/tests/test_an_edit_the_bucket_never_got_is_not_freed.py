"""The reclaim pass frees a photo only when the bucket holds THIS version.

It used to ask only whether some object existed at the photo's key. An edit,
rotate or cutout whose push to R2 was skipped (the latch) or failed left the
bucket holding the version from BEFORE it -- and once the file was old enough,
reclaim saw "an object exists", unlinked the newer local file, and /media and
eBay went back to the pre-edit photo. The edit was gone without a word.
"""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main  # noqa: E402


@pytest.fixture
def photo():
    d = main.storage.optimized_dir("reclaim1")
    p = d / "img_000.jpg"
    p.write_bytes(b"rotated-version")
    old = time.time() - 10 * 86400
    os.utime(p, (old, old))
    return p


def _bucket(monkeypatch, head):
    uploads = []
    monkeypatch.setattr(main.objstore, "enabled", lambda: True)
    monkeypatch.setattr(main.objstore, "head", lambda key: head(key))
    monkeypatch.setattr(main.objstore, "upload",
                        lambda p, key, **k: uploads.append(key) or key)
    return uploads


def test_a_stale_bucket_copy_is_replaced_not_trusted(photo, monkeypatch):
    # The bucket has the pre-rotation bytes: a different size, stored before.
    st = photo.stat()
    stale = {"size": st.st_size + 7, "modified": st.st_mtime - 3600}
    uploads = _bucket(monkeypatch, lambda key: stale)

    main._offload_to_r2(max_age_seconds=5 * 86400)

    assert uploads, "the newer local copy must be pushed"
    # The re-check still sees the stale copy (the stub never changes), so the
    # only copy of the edit stays on the volume.
    assert photo.is_file()


def test_a_current_bucket_copy_lets_the_local_one_go(photo, monkeypatch):
    st = photo.stat()
    current = {"size": st.st_size, "modified": st.st_mtime + 60}
    uploads = _bucket(monkeypatch, lambda key: current)

    main._offload_to_r2(max_age_seconds=5 * 86400)

    assert not uploads
    assert not photo.exists()


def test_a_missing_object_is_backfilled_then_freed(photo, monkeypatch):
    st = photo.stat()
    state = {"there": None}
    uploads = _bucket(monkeypatch, lambda key: state["there"])
    real_upload = main.objstore.upload

    def upload(p, key, **k):
        state["there"] = {"size": st.st_size, "modified": time.time()}
        return real_upload(p, key, **k)
    monkeypatch.setattr(main.objstore, "upload", upload)

    main._offload_to_r2(max_age_seconds=5 * 86400)

    assert uploads and not photo.exists()

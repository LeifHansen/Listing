"""Every derived JPEG (the working copy, the as-shot copy, the vision copy)
is written to a temp file and renamed into place, so nobody reads a torn
file. Two requests deriving the same photo at once must not share that temp
file: interleaved writes renamed into place are a corrupt copy that the
cache then serves until the photo is next edited. The vision copy's temp
name used to be the same for every writer, and the other two were unique
per process but not per thread.
"""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from backend.services import images  # noqa: E402


def test_concurrent_writers_use_their_own_temp_files(tmp_path, monkeypatch):
    seen: list[str] = []
    gate = threading.Barrier(2)
    real_save = Image.Image.save

    def save(self, fp, *args, **kwargs):
        seen.append(str(fp))
        gate.wait(timeout=5)  # both writers hold a temp file at once
        return real_save(self, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", save)
    dst = tmp_path / "img_000.jpg"
    workers = [threading.Thread(target=images._save_atomic,
                                args=(Image.new("RGB", (8, 8), c), dst),
                                kwargs={"quality": 85})
               for c in ("red", "blue")]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert len(set(seen)) == 2, seen
    with Image.open(dst) as done:
        done.verify()
    assert not list(tmp_path.glob("*.tmp"))


def test_a_failed_write_leaves_the_old_copy_and_no_temp_file(tmp_path, monkeypatch):
    dst = tmp_path / "img_000.jpg"
    Image.new("RGB", (8, 8), "green").save(dst, "JPEG")
    before = dst.read_bytes()

    def boom(self, fp, *args, **kwargs):
        with open(fp, "wb") as half:
            half.write(b"half a jpeg")
        raise OSError("disk full")

    monkeypatch.setattr(Image.Image, "save", boom)
    with pytest.raises(OSError):
        images._save_atomic(Image.new("RGB", (8, 8)), dst, quality=85)
    assert dst.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))

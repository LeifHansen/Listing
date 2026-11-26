"""Two requests bringing the same offloaded photo back from R2 both succeed.

`restore` and `download` wrote to a FIXED `<name>.tmp` and then renamed it.
Two at once -- the studio opening a photo while a Finish-all fill reads it --
shared that one temp file: the second write truncated the first's, the first
rename moved a file the second was still writing, and the second rename found
nothing and answered False, which the edit routes report to the seller as
"That photo isn't on the server anymore". images.write_jpeg was fixed for the
same interleaving; these two were not.

The barrier makes the race deterministic: both writers finish writing before
either renames, which is exactly the overlap that broke.
"""
from __future__ import annotations

import pathlib
import threading

import pytest

from backend import objstore


class _Body:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _Client:
    def get_object(self, Bucket, Key):
        return {"Body": _Body(b"the photo bytes")}

    def download_file(self, bucket, key, dest):
        pathlib.Path(dest).write_bytes(b"the video bytes")


@pytest.fixture()
def overlapping_renames(monkeypatch):
    monkeypatch.setattr(objstore, "_get_client", lambda: _Client())
    barrier = threading.Barrier(2, timeout=5)
    real_replace = pathlib.Path.replace

    def _replace(self, target):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", _replace)


def _twice(fn):
    results = []
    threads = [threading.Thread(target=lambda: results.append(fn()))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results


@pytest.mark.parametrize("op", ["restore", "download"])
def test_both_land_and_leave_no_temp_files(tmp_path, overlapping_renames, op):
    dest = tmp_path / "optimized" / "img_000.jpg"
    call = getattr(objstore, op)
    results = _twice(lambda: call("sessions/s/optimized/img_000.jpg", dest))

    assert results == [True, True]
    assert dest.read_bytes() in (b"the photo bytes", b"the video bytes")
    assert [p.name for p in dest.parent.iterdir()] == ["img_000.jpg"]


def test_a_download_cut_off_part_way_leaves_no_temp_file(tmp_path, monkeypatch):
    """Unique temp names must not become litter: a half-written one is
    removed rather than left on the 1GB volume under a name nothing reuses."""
    class _CutOff(_Client):
        def download_file(self, bucket, key, dest):
            pathlib.Path(dest).write_bytes(b"half a vid")
            raise OSError("connection reset")

    monkeypatch.setattr(objstore, "_get_client", lambda: _CutOff())
    dest = tmp_path / "video" / "v.mp4"
    assert objstore.download("k", dest) is False
    assert list(dest.parent.iterdir()) == []

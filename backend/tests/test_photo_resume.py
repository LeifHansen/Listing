"""The photo pass recognising work it already finished.

Half of "a batch survives the machine it started on" (the other half, deciding
which batches may be picked back up, is test_bulk_resume.py). A bulk batch does
every photo's background removal up front, so a machine that goes away during
that pass used to throw away every cutout it had finished — the optimized
photos were on the volume the whole time and nothing went looking for them.

Pillow only, deliberately: this is an image-pipeline invariant, so it belongs
in the gate that runs without fastapi.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from backend.services import images  # noqa: E402


def _photos(dir_, n=3):
    dir_.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = Image.new("RGB", (300, 300), (240, 240, 240))
        img.paste(Image.new("RGB", (120, 120), (30, 60, 120)), (90, 90))
        img.save(dir_ / f"src_{i:03d}.jpg", "JPEG")
    return dir_


def test_finished_photos_are_not_optimized_twice(tmp_path):
    """The point of the whole exercise: the second run must not redo the
    cutouts the first one already paid for."""
    src, dst = _photos(tmp_path / "orig"), tmp_path / "opt"

    first = images.optimize_all(src, dst)
    assert len(first) == 3
    assert not any(r.get("reused") for r in first)
    stamps = {p.name: p.stat().st_mtime_ns for p in sorted(dst.iterdir())}

    second = images.optimize_all(src, dst)
    assert [r.get("reused") for r in second] == [True, True, True]
    assert {p.name: p.stat().st_mtime_ns for p in sorted(dst.iterdir())} == stamps, \
        "a reused photo was rewritten"


def test_a_half_finished_batch_only_does_what_is_left(tmp_path):
    """The real resume shape: some outputs on disk, some not."""
    src, dst = _photos(tmp_path / "orig", n=4), tmp_path / "opt"
    images.optimize_all(src, dst)
    (dst / "img_001.jpg").unlink()
    (dst / "img_003.jpg").unlink()

    results = images.optimize_all(src, dst)
    assert [bool(r.get("reused")) for r in results] == [True, False, True, False]
    # Results stay in source order, so they still line up with the filenames.
    assert [r["file"] for r in results] == [
        "img_000.jpg", "img_001.jpg", "img_002.jpg", "img_003.jpg"]


def test_progress_counts_the_whole_batch_not_just_the_remainder(tmp_path):
    """A resumed batch's progress bar must not drop back to 0 — the seller is
    watching a count that already reached 38 of 40."""
    src, dst = _photos(tmp_path / "orig", n=4), tmp_path / "opt"
    images.optimize_all(src, dst)
    (dst / "img_003.jpg").unlink()

    ticks = []
    images.optimize_all(src, dst, progress=lambda done, total: ticks.append((done, total)))
    assert ticks == [(4, 4)], "resumed progress restarted instead of continuing"


def test_a_save_that_dies_partway_leaves_no_output_behind(tmp_path, monkeypatch):
    """Skipping finished photos is only safe because outputs are renamed into
    place, so a file existing means it is complete.

    This module sets LOAD_TRUNCATED_IMAGES (phone uploads arrive short), which
    makes the failure silent rather than loud: a half-written JPEG opens
    perfectly happily, so a resume would adopt it and ship a torn photo to a
    listing. Nothing downstream can catch that — the guarantee has to be that
    a partial file never appears at the destination in the first place."""
    src, dst = _photos(tmp_path / "orig", n=1), tmp_path / "opt"
    real_save = Image.Image.save

    def _die_partway(self, fp, *args, **kwargs):
        if not isinstance(fp, (str, Path)):
            return real_save(self, fp, *args, **kwargs)
        Path(fp).write_bytes(b"\xff\xd8\xff\xe0 half a jpeg")
        raise OSError("machine went away mid-save")

    monkeypatch.setattr(Image.Image, "save", _die_partway)
    results = images.optimize_all(src, dst)

    assert "mid-save" in (results[0].get("error") or ""), (
        f"the simulated failure never ran: {results[0]}")
    assert not (dst / "img_000.jpg").exists(), (
        "a half-written photo was left where a resume would adopt it as done")
    assert not list(dst.iterdir()), "a temp file was left behind"

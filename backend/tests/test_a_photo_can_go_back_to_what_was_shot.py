"""The upload is on the server. Until now nothing could reach it.

Every session keeps what the seller actually uploaded in original/, and the
app served only optimized/ — the copy the photo pass produced. For as long as
that pass never damaged a photo, the difference did not matter.

It damages photos. A seller sent a screenshot of five photos of one framed
watercolour: three came back as smears of brushwork and a tree on white,
because the background remover, handed a photograph OF A PICTURE, segments
what the picture depicts and deletes the artwork around it (see
test_the_cutout_does_not_eat_the_artwork.py, which stops that shipping
again). Their only option for the three already ruined was to delete the
tiles and re-upload photos the server already had, untouched, one directory
across.

So a photo can be put back. The rules it has to keep:

  * the restored file is RE-OPTIMIZED, not copied — still sized for eBay,
    still upright, and still stripped of the EXIF that carries the seller's
    home address;
  * the background remover does not run, which is the entire point;
  * the outgoing copy is snapshot to history first, so restore is itself
    undoable and is not the one action in the app that destroys something;
  * originals are pruned on a timer, so "there is nothing to go back to" is a
    real answer that has to be said rather than reported as success;
  * `name` comes off the wire and indexes a directory, so it cannot be
    allowed to walk out of it.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from pathlib import Path  # noqa: E402

from PIL import Image  # noqa: E402

from backend import storage  # noqa: E402
from backend.services import images  # noqa: E402

# A photo with a distinctive colour, so "did the original come back" is a
# question about pixels rather than about file size.
SHOT = (30, 140, 90)


def _session(tmp_path: Path, monkeypatch, count: int = 3) -> str:
    """A session whose originals are `count` green photos, and whose
    optimized copies have been replaced with white squares — standing in for
    the cutout having eaten them."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path, raising=False)
    sid = "sess1"
    orig = storage.original_dir(sid)
    opt = storage.optimized_dir(sid)
    for i in range(count):
        Image.new("RGB", (800, 600), SHOT).save(orig / f"IMG_{i + 1}.jpg", "JPEG")
        Image.new("RGB", (800, 600), (255, 255, 255)).save(
            opt / f"img_{i:03d}.jpg", "JPEG")
    return sid


def _corner(path: Path) -> tuple[int, int, int]:
    with Image.open(path) as im:
        return im.convert("RGB").getpixel((5, 5))


def test_the_original_is_findable_from_the_optimized_name(tmp_path, monkeypatch):
    sid = _session(tmp_path, monkeypatch)
    src = images.source_for(storage.original_dir(sid), "img_001.jpg")
    assert src is not None and src.name == "IMG_2.jpg"


def test_photo_ten_is_not_photo_one(tmp_path, monkeypatch):
    """Naturally sorted, so IMG_10 comes after IMG_9 rather than after IMG_1 —
    the same ordering optimize_all named the outputs from."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path, raising=False)
    orig = storage.original_dir("s2")
    for n in ("IMG_1.jpg", "IMG_2.jpg", "IMG_10.jpg"):
        Image.new("RGB", (40, 30), SHOT).save(orig / n, "JPEG")
    got = [images.source_for(orig, f"img_{i:03d}.jpg").name for i in range(3)]
    assert got == ["IMG_1.jpg", "IMG_2.jpg", "IMG_10.jpg"]


def test_a_name_cannot_walk_out_of_the_directory(tmp_path, monkeypatch):
    sid = _session(tmp_path, monkeypatch)
    orig = storage.original_dir(sid)
    for name in ("../../etc/passwd", "img_001.jpg/../../x", "", "notaphoto.png",
                 # Not the shape optimize_all writes (img_{i:03d}.jpg). It
                 # would have indexed the directory perfectly well, which is
                 # the reason to match what the app produces rather than
                 # whatever happens to parse.
                 "img_1.jpg", "img_.jpg", "IMG_001.JPG"):
        assert images.source_for(orig, name) is None, name


def test_an_index_past_the_end_has_nothing_to_restore(tmp_path, monkeypatch):
    sid = _session(tmp_path, monkeypatch, count=2)
    assert images.source_for(storage.original_dir(sid), "img_007.jpg") is None


def test_restoring_brings_the_shot_back_without_the_cutout(tmp_path, monkeypatch):
    """The end-to-end property, at the level the route works at: the ruined
    optimized copy is replaced by a re-optimized original."""
    sid = _session(tmp_path, monkeypatch)
    opt = storage.optimized_dir(sid) / "img_000.jpg"
    assert _corner(opt) == (255, 255, 255)          # eaten

    source = images.source_for(storage.original_dir(sid), "img_000.jpg")
    out = images.optimize(source, opt, remove_bg=False)

    assert _corner(opt) != (255, 255, 255)
    assert abs(_corner(opt)[1] - SHOT[1]) < 12       # green, modulo JPEG
    assert out["background_removed"] is False


def test_the_restored_file_still_carries_no_exif(tmp_path, monkeypatch):
    """A restore must not be the one path that puts the seller's GPS back on
    a public listing."""
    sid = _session(tmp_path, monkeypatch)
    opt = storage.optimized_dir(sid) / "img_000.jpg"
    source = images.source_for(storage.original_dir(sid), "img_000.jpg")
    images.optimize(source, opt, remove_bg=False)
    with Image.open(opt) as im:
        assert not dict(im.getexif())


def test_the_outgoing_copy_is_kept_so_restore_is_undoable(tmp_path, monkeypatch):
    sid = _session(tmp_path, monkeypatch)
    storage.snapshot_image(sid, "img_000.jpg")
    history = list(storage.history_dir(sid).rglob("*"))
    assert [p for p in history if p.is_file()], "nothing was snapshot"

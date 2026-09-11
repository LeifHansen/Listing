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

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

from PIL import Image  # noqa: E402

from backend import storage  # noqa: E402
from backend.services import images  # noqa: E402

# A photo with a distinctive colour, so "did the original come back" is a
# question about pixels rather than about file size.
SHOT = (30, 140, 90)


def _session(tmp_path: Path, monkeypatch, count: int = 3,
             sid: str = "sess1") -> str:
    """A session whose originals are `count` green photos, and whose
    optimized copies have been replaced with white squares — standing in for
    the cutout having eaten them.

    `sid` is per-test for anything that writes to history/. storage.session_dir
    reads config.SESSIONS_DIR, not the DATA_DIR patched here, so every test in
    this file shares one directory on disk: the originals and optimized copies
    below are rewritten each time and do not care, but a history entry would
    survive into the next test and answer its question for it.
    """
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path, raising=False)
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


def test_a_photo_added_later_goes_back_to_its_own_original(tmp_path, monkeypatch):
    """"Add photos" keeps its originals as add_NNN beside the upload's
    src_NNN, and add_ sorts before src_. Read by position, img_002 on a
    listing of two uploads and one added photo landed on src_001 -- and
    "Restore original" put a different photo where the seller asked for
    theirs. The file says which index it is for, and that is what is read."""
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path, raising=False)
    sid = "sess2"
    orig = storage.original_dir(sid)
    shots = {"src_000.jpg": (200, 0, 0), "src_001.jpg": (0, 200, 0),
             "add_002.jpg": (0, 0, 200)}
    for name, colour in shots.items():
        Image.new("RGB", (64, 48), colour).save(orig / name, "JPEG")

    assert images.source_for(orig, "img_002.jpg").name == "add_002.jpg"
    assert images.source_for(orig, "img_000.jpg").name == "src_000.jpg"
    assert images.source_for(orig, "img_001.jpg").name == "src_001.jpg"
    assert images.source_for(orig, "img_003.jpg") is None


# --- when the upload itself is gone -----------------------------------------
#
# Reported straight after the background remover ate part of a shirt:
# "reverting to original does not work. 'no longer on server'". Both halves
# were true and they compound. Originals are reclaimed after twelve hours (as
# little as fifteen minutes when the volume is tight) while HISTORY snapshots
# keep for fourteen days — so for all but the first half-day of a listing's
# life the upload is gone, and the button that undoes a damaged cutout was
# dead exactly when someone reached for it.
#
# There is almost always a way back: snapshot_image runs before every edit
# overwrites the working copy. Restore now falls back to the OLDEST of those.

def _snapshot(sid: str, name: str, stamp: int, colour) -> Path:
    """One history entry for `name`, stamped as `snapshot_image` stamps."""
    hist = storage.history_dir(sid)
    path = hist / f"{name}.{stamp}"
    Image.new("RGB", (800, 600), colour).save(path, "JPEG")
    return path


def test_the_oldest_snapshot_is_the_one_to_go_back_to(tmp_path, monkeypatch):
    """Oldest, not newest. snapshot_image runs BEFORE each edit, so on a photo
    that was cut out and then straightened the newest snapshot is still a
    cutout — and a cutout is what the seller is undoing."""
    sid = _session(tmp_path, monkeypatch, sid="hist_oldest")
    _snapshot(sid, "img_000.jpg", 1000, SHOT)              # before the cutout
    _snapshot(sid, "img_000.jpg", 2000, (255, 255, 255))   # before the straighten

    found = storage.earliest_snapshot(sid, "img_000.jpg")
    assert found is not None and found.name.endswith(".1000")


def test_a_photo_with_no_history_has_none(tmp_path, monkeypatch):
    sid = _session(tmp_path, monkeypatch, sid="hist_none")
    assert storage.earliest_snapshot(sid, "img_000.jpg") is None


def test_the_stamps_sort_as_numbers_not_as_text(tmp_path, monkeypatch):
    """`9` must not come after `10`. The stamps are milliseconds and roll over
    a digit constantly."""
    sid = _session(tmp_path, monkeypatch, sid="hist_stamps")
    _snapshot(sid, "img_000.jpg", 9, SHOT)
    _snapshot(sid, "img_000.jpg", 10, (255, 255, 255))
    found = storage.earliest_snapshot(sid, "img_000.jpg")
    assert found is not None and found.name.endswith(".9")


def test_another_photos_history_is_not_offered(tmp_path, monkeypatch):
    sid = _session(tmp_path, monkeypatch, sid="hist_other")
    _snapshot(sid, "img_001.jpg", 1000, SHOT)
    assert storage.earliest_snapshot(sid, "img_000.jpg") is None


def test_a_file_that_is_not_a_stamp_is_skipped(tmp_path, monkeypatch):
    """Anything else in history/ is not a version of this photo, and sorting
    it as text would put it first."""
    sid = _session(tmp_path, monkeypatch, sid="hist_junk")
    (storage.history_dir(sid) / "img_000.jpg.tmp").write_bytes(b"not a stamp")
    _snapshot(sid, "img_000.jpg", 1000, SHOT)
    found = storage.earliest_snapshot(sid, "img_000.jpg")
    assert found is not None and found.name.endswith(".1000")


def test_restore_falls_back_to_history_when_the_upload_is_pruned(tmp_path,
                                                                 monkeypatch):
    """The report, end to end: the upload is gone, a pre-cutout snapshot is
    not, and the button puts the photo back instead of refusing."""
    sid = _session(tmp_path, monkeypatch, sid="hist_restore")
    _snapshot(sid, "img_000.jpg", 1000, SHOT)
    shutil.rmtree(storage.original_dir(sid))       # twelve hours later

    assert images.source_for(storage.original_dir(sid), "img_000.jpg") is None
    source = storage.earliest_snapshot(sid, "img_000.jpg")
    assert source is not None
    images.optimize(source, storage.optimized_dir(sid) / "img_000.jpg",
                    remove_bg=False)

    assert _corner(storage.optimized_dir(sid) / "img_000.jpg") != (255, 255, 255)


def test_with_neither_an_upload_nor_a_snapshot_it_still_says_so(tmp_path,
                                                                monkeypatch):
    """The one case that is genuinely unrecoverable. Saying so beats reporting
    a restore that did not happen — the rule this file opened with."""
    sid = _session(tmp_path, monkeypatch, sid="hist_empty")
    shutil.rmtree(storage.original_dir(sid))
    assert images.source_for(storage.original_dir(sid), "img_000.jpg") is None
    assert storage.earliest_snapshot(sid, "img_000.jpg") is None


def test_the_as_shot_copy_is_preferred_over_a_snapshot(tmp_path, monkeypatch):
    """#291 keeps a faithful copy beside every cutout it changes. That IS the
    photo the camera saw, so restore reaches for it before the history
    snapshot, which is only ever "whatever the working copy was before some
    earlier edit"."""
    sid = _session(tmp_path, monkeypatch, sid="hist_as_shot")
    kept = storage.optimized_path(sid).parent / "as_shot"
    kept.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 600), SHOT).save(kept / "img_000.jpg", "JPEG")
    _snapshot(sid, "img_000.jpg", 1000, (255, 255, 255))

    found = storage.as_shot_copy(sid, "img_000.jpg")
    assert found is not None and found.parent.name == "as_shot"


def test_no_as_shot_copy_is_not_an_error(tmp_path, monkeypatch):
    """Most photos never had a cutout, so most have no copy — and the caller
    falls through to history rather than treating it as a failure."""
    sid = _session(tmp_path, monkeypatch, sid="hist_no_as_shot")
    assert storage.as_shot_copy(sid, "img_000.jpg") is None


def test_an_older_as_shot_copy_is_still_offered(tmp_path, monkeypatch):
    """images.as_shot() refuses a copy older than the working file, because
    the passes that READ a photo must let the seller's later edit win. Restore
    is the opposite case: undoing later edits is the whole request."""
    sid = _session(tmp_path, monkeypatch, sid="hist_old_as_shot")
    kept = storage.optimized_path(sid).parent / "as_shot"
    kept.mkdir(parents=True, exist_ok=True)
    copy = kept / "img_000.jpg"
    Image.new("RGB", (800, 600), SHOT).save(copy, "JPEG")
    import os
    old = copy.stat().st_mtime - 3600
    os.utime(copy, (old, old))          # the seller edited after the cutout

    assert images.as_shot(storage.optimized_dir(sid) / "img_000.jpg").parent.name \
        == "optimized", "the read path should refuse a stale copy"
    assert storage.as_shot_copy(sid, "img_000.jpg") is not None, \
        "restore should still offer it"

"""A cutout is for the buyer to look at, not for the app to think with.

The second half of the Hilo Hattie report. The cutout tore up two tag
close-ups; identify then read those same torn files, found no brand on them,
and invented one — "Lehio Hawaii". Sparing close-ups fixes that cause, but
not the shape of it: every vision pass reads the OPTIMIZED photo, so any
cutout that goes wrong for a reason not yet thought of silently becomes the
app's only record of the item, and the seller reads the result as the AI
being bad at identifying things.

So the pass keeps the photo the cutout replaced, and the passes that read an
item — what its tag says, what it is made of — open that instead. The one
that matters most is the zoomed tag read, which is not a whole-frame call at
all: it crops the small print straight out of the full-size photo.

What the copy has to be, and these tests hold each one:

  * the frame the pass produced, not the raw upload — upright, EXIF honoured,
    sized, metadata stripped;
  * written only when a cutout actually changed the photo, because otherwise
    the optimized photo already IS what the camera saw;
  * beaten by the seller's own edit, always. The studio's save, a quick
    rotate and Restore original all rewrite the working copy, and that alone
    has to be enough to retire this one — none of those routes should have to
    know it exists;
  * never able to cost a photo: a cache that cannot be written, or has been
    swept off the volume, leaves every pass reading what it read before.

Pillow only, with the cutout stood in for by a canvas the test draws itself.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from backend.services import images  # noqa: E402

RED = (200, 40, 40)
WHITE = (255, 255, 255)


def _session(tmp: Path) -> tuple[Path, Path]:
    """A session laid out the way storage does it, and one upload in it."""
    (tmp / "original").mkdir()
    (tmp / "optimized").mkdir()
    src = tmp / "original" / "src_000.jpg"
    Image.new("RGB", (900, 700), RED).save(src, "JPEG", quality=92)
    return src, tmp / "optimized" / "img_000.jpg"


def _erasing_cutout(img, wait=None):
    """A cutout that keeps nothing — the damage, in its purest form."""
    return Image.new("RGB", img.size, WHITE)


@pytest.fixture()
def cut_out(monkeypatch):
    monkeypatch.setattr(images, "cutout", _erasing_cutout)


def test_the_photo_the_cutout_replaced_is_what_the_ai_reads(tmp_path, cut_out):
    """The listing photo is the cutout. What a vision pass opens is not."""
    src, dst = _session(tmp_path)
    images.optimize(src, dst, remove_bg=True)

    with Image.open(dst) as shipped:
        assert shipped.getpixel((450, 350)) == WHITE     # the buyer sees this

    kept = images.as_shot(dst)
    assert kept != dst
    with Image.open(kept) as read:
        assert read.getpixel((450, 350))[0] > 150        # the AI reads this
        assert read.size == (900, 700)
        assert not read.getexif(), "the seller's GPS must not ride along"


def test_a_photo_no_cutout_changed_is_its_own_faithful_copy(tmp_path,
                                                            monkeypatch):
    """No second file, and nothing to invalidate: when the cutout did not run,
    or found nothing to keep, the optimized photo already IS what the camera
    saw."""
    monkeypatch.setattr(images, "cutout", lambda img, wait=None: None)
    src, dst = _session(tmp_path)

    images.optimize(src, dst, remove_bg=False)
    assert images.as_shot(dst) == dst

    images.optimize(src, dst, remove_bg=True)            # refused by the model
    assert images.as_shot(dst) == dst
    assert not (tmp_path / "as_shot").exists()


def test_a_close_up_needs_no_copy_either(tmp_path, cut_out):
    """The other half of this fix: a close-up is never handed to the model,
    so its optimized photo is untouched and speaks for itself."""
    src, dst = _session(tmp_path)
    images.optimize(src, dst, remove_bg=True, detail=True)
    assert images.as_shot(dst) == dst


def test_the_sellers_own_edit_wins(tmp_path, cut_out):
    """The studio's save, a quick rotate and Restore original all rewrite the
    working copy. That alone retires the kept copy — none of those routes
    knows it exists, and none of them should have to."""
    src, dst = _session(tmp_path)
    images.optimize(src, dst, remove_bg=True)
    assert images.as_shot(dst) != dst

    # An edit, exactly as /api/edit-image and /api/image/rotate write one.
    Image.new("RGB", (700, 900), (10, 80, 10)).save(dst, "JPEG", quality=88)
    os.utime(dst, (dst.stat().st_atime + 10, dst.stat().st_mtime + 10))

    assert images.as_shot(dst) == dst
    with Image.open(images.vision_copy(dst)) as seen:
        assert seen.getpixel((10, 10))[1] > 50, "the model read the old photo"


def test_the_vision_copy_is_made_from_it(tmp_path, cut_out):
    """The whole-frame payload every identify/specifics call sends, and the
    cached copy behind it."""
    src, dst = _session(tmp_path)
    images.optimize(src, dst, remove_bg=True)

    small = images.vision_copy(dst)
    assert small.parent.name == "vision"
    with Image.open(small) as seen:
        assert seen.getpixel((10, 10)) != WHITE
        assert max(seen.size) <= images.VISION_SIDE
    # ...and the cache is a cache: asked again, the same file, not rewritten.
    assert images.vision_copy(dst) == small


def test_a_stale_vision_copy_is_not_served_after_an_edit(tmp_path, cut_out):
    """The cached copy is compared against BOTH the photo and the copy it was
    made from, so an edit after a cutout cannot be served from before it."""
    src, dst = _session(tmp_path)
    images.optimize(src, dst, remove_bg=True)
    first = images.vision_copy(dst)
    before = first.stat().st_mtime

    Image.new("RGB", (700, 900), (10, 80, 10)).save(dst, "JPEG", quality=88)
    os.utime(dst, (dst.stat().st_atime + 10, dst.stat().st_mtime + 10))

    again = images.vision_copy(dst)
    assert again.stat().st_mtime > before
    with Image.open(again) as seen:
        assert seen.getpixel((10, 10))[1] > 50


def test_a_swept_copy_costs_nothing(tmp_path, cut_out):
    """as_shot/ is reclaimed on the same timer as the uploads
    (storage.prune_originals). Every pass then reads the optimized photo,
    exactly as it did before any of this existed."""
    src, dst = _session(tmp_path)
    images.optimize(src, dst, remove_bg=True)
    images.as_shot(dst).unlink()

    assert images.as_shot(dst) == dst
    assert images.vision_copy(dst).is_file()


def test_a_copy_that_cannot_be_written_does_not_cost_the_photo(tmp_path,
                                                               monkeypatch,
                                                               cut_out):
    """A cache is never worth a listing. With the volume full, the photo still
    ships and every pass reads it, exactly as for a photo that never had a
    cutout. _keep_as_shot swallows its own failure; nothing above it does."""
    src, dst = _session(tmp_path)
    real_mkdir = Path.mkdir

    def _full_volume(self, *a, **k):
        if self.name == "as_shot":
            raise OSError("No space left on device")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", _full_volume)

    out = images.optimize(src, dst, remove_bg=True)

    assert out["background_removed"] is True
    assert dst.is_file()
    assert not out.get("bg_error")
    assert images.as_shot(dst) == dst


def test_any_other_path_comes_straight_back(tmp_path):
    """Callers hand this whatever path they have — an export, a temp file, a
    photo outside a session. None of them may be rewritten to somewhere else."""
    loose = tmp_path / "somewhere.jpg"
    Image.new("RGB", (40, 40), RED).save(loose, "JPEG")
    assert images.as_shot(loose) == loose
    assert images.vision_copy(loose) == loose

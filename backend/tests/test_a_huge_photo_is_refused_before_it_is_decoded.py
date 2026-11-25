"""A photo with more pixels than the machine can hold is refused before decoding.

A PNG, HEIC or WebP has no reduced-scale decode the way a JPEG has
(`draft`), so its full size is what lands in memory. A 480KB PNG of one
colour can claim 12500x12500 pixels, and the photo pass decoded it to 1.3GB
on a 2GB machine, with nothing needed but an anonymous upload. Pillow's own
bomb guard refuses only past ~179MP.

So every place that decodes a photo from outside reads its size off the
header first and refuses past images.MAX_PIXELS. It counts the size the photo
will actually be DECODED at, so a big JPEG that `draft` brings down to an
eighth still goes through.

The second half is the copy. exif_transpose copies the whole image even when
the camera held the phone upright, so the photo is shrunk to its output size
BEFORE it is turned, never after.
"""
from __future__ import annotations

import struct
import zlib

import pytest

pytest.importorskip("PIL")

from io import BytesIO  # noqa: E402
from pathlib import Path  # noqa: E402

from PIL import Image, ImageFile, ImageOps  # noqa: E402

from backend.services import cutouts, image_import, images  # noqa: E402

pytestmark = pytest.mark.filterwarnings(
    "ignore::PIL.Image.DecompressionBombWarning")


def _huge_png(w: int = 9000, h: int = 7000) -> bytes:
    """A real, valid 1-bit PNG of `w`x`h` that is a few KB on disk -- the
    shape of the attack: tiny upload, enormous decode."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    row = b"\x00" + b"\x00" * ((w + 7) // 8)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * h, 9))
            + chunk(b"IEND", b""))


@pytest.fixture()
def no_decoding(monkeypatch):
    """Fail the test if anything decodes pixels: a refusal must come from
    the header alone."""
    def _refuse(self, *a, **k):
        raise AssertionError("pixels were decoded before the size was checked")
    monkeypatch.setattr(ImageFile.ImageFile, "load", _refuse)


def test_the_photo_pass_refuses_it_from_the_header(tmp_path, no_decoding):
    src = tmp_path / "huge.png"
    src.write_bytes(_huge_png())
    with pytest.raises(images.TooLarge):
        images._load(src)


def test_the_batch_reports_it_and_goes_on(tmp_path):
    src_dir, out_dir = tmp_path / "src", tmp_path / "out"
    src_dir.mkdir()
    (src_dir / "a_huge.png").write_bytes(_huge_png())
    Image.new("RGB", (800, 600), (30, 90, 160)).save(src_dir / "b_fine.jpg")
    results = images.optimize_all(src_dir, out_dir)
    assert "too large" in results[0]["error"]
    assert results[1]["output_size"] == (800, 600)


def test_the_ai_thumbnails_refuse_it_too(tmp_path, no_decoding):
    src = tmp_path / "huge.png"
    src.write_bytes(_huge_png())
    with pytest.raises(images.TooLarge):
        images.thumb_jpeg(src)


def test_a_cutout_source_is_refused(no_decoding):
    with pytest.raises(images.TooLarge):
        cutouts.decode(_huge_png())


def test_an_imported_photo_is_refused(monkeypatch):
    monkeypatch.setattr(image_import, "fetch_ebay_image", lambda url: _huge_png())
    from backend import storage
    assert image_import.import_listing_images(
        storage.new_session_id(), ["https://i.ebayimg.com/a.png"]) == []


def test_a_big_jpeg_that_decodes_small_still_goes_through(tmp_path, monkeypatch):
    """The cap is on what is DECODED. A JPEG `draft` brings down to an eighth
    is a small decode, however many pixels its header claims."""
    monkeypatch.setattr(images, "MAX_PIXELS", 3_000_000)
    src = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), (200, 60, 40)).save(src, quality=70)
    img, shot = images._load(src)
    assert shot == (4000, 3000) and max(img.size) == images.TARGET_SIZE


def test_the_photo_is_shrunk_before_it_is_turned(tmp_path, monkeypatch):
    """exif_transpose copies the whole image; handed the full-size photo
    that copy is the second full-size decode in memory."""
    seen = []
    real = ImageOps.exif_transpose

    def _spy(img, *a, **k):
        seen.append(img.size)
        return real(img, *a, **k)

    monkeypatch.setattr(images.ImageOps, "exif_transpose", _spy)
    src = tmp_path / "wide.png"
    img = Image.new("RGB", (4000, 3000), (10, 120, 200))
    exif = Image.Exif()
    exif[274] = 6  # the camera was turned: the photo is 3000 wide upright
    img.save(src, exif=exif)

    out, shot = images._load(src)
    assert out.size == (1200, 1600) and shot == (3000, 4000)
    assert max(seen[0]) <= images.TARGET_SIZE

    seen.clear()
    small = Image.open(BytesIO(images.thumb_jpeg(Path(src), side=512)))
    assert small.size == (384, 512)
    assert max(seen[0]) <= 512 * 2  # draft/reduce may leave up to 2x

"""The export contract: what comes out of optimize() and lands on eBay.

Five properties, each of which someone could plausibly "improve" away:
upright, at the frame the seller composed (never cropped, never upscaled),
no larger than 1600px on the long side, JPEG at the agreed quality, and
carrying no EXIF. The last one is a privacy guarantee -- a phone photo knows
where the seller lives -- and it holds today only because Pillow drops
metadata by default, which is exactly the kind of thing a helpful refactor
restores.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from pathlib import Path  # noqa: E402

from PIL import Image, ImageDraw, ImageOps  # noqa: E402

from backend.services import images  # noqa: E402


def _photo(tmp: Path, size=(1200, 1600), box=(300, 500, 900, 1100),
           exif=True) -> Path:
    """A portrait phone photo of a dark item on a light backdrop, tagged with
    an orientation flag and GPS like a real one."""
    img = Image.new("RGB", size, (246, 246, 248))
    ImageDraw.Draw(img).rounded_rectangle(box, radius=40, fill=(50, 60, 80))
    src = tmp / "src.jpg"
    if exif:
        data = Image.Exif()
        data[274] = 6                       # Orientation: rotate 90 CW
        data[34853] = {1: "N", 2: (37.0, 46.0, 0.0),           # GPS: home
                       3: "W", 4: (122.0, 24.0, 0.0)}
        img.save(src, "JPEG", quality=95, exif=data)
    else:
        img.save(src, "JPEG", quality=95)
    return src


def _dark_columns(path: Path):
    with Image.open(path) as done:
        arr = done.convert("L")
        w, h = arr.size
        px = arr.load()
        return [x for x in range(w) if any(px[x, y] < 200 for y in range(0, h, 8))], (w, h)


def test_output_is_a_jpeg_at_the_frame_the_seller_composed(tmp_path):
    """1200x1600 tagged sideways is a 1600x1200 photo: it comes out at exactly
    that -- the long side at the 1600 eBay asks for, the short side following,
    nothing cropped away and nothing padded on."""
    out = images.optimize(_photo(tmp_path), tmp_path / "out")
    assert out["output_size"] == (1600, 1200)
    with Image.open(tmp_path / "out.jpg") as done:
        assert done.size == (1600, 1200)
        assert done.format == "JPEG"


def test_the_saved_photo_carries_no_exif_and_no_gps(tmp_path):
    """The seller's home coordinates do not go on a public listing."""
    images.optimize(_photo(tmp_path), tmp_path / "out")
    with Image.open(tmp_path / "out.jpg") as done:
        assert not done.getexif(), "EXIF survived into the published photo"
        assert "exif" not in done.info


def test_camera_orientation_is_honoured(tmp_path):
    """EXIF orientation 6 means the sensor was sideways. A portrait photo
    tagged that way is really landscape, and the pipeline has to agree before
    anything downstream measures the subject."""
    src = _photo(tmp_path, size=(1200, 1600))
    with Image.open(src) as raw:
        assert raw.size == (1200, 1600)          # as stored...
        assert ImageOps.exif_transpose(raw).size == (1600, 1200)  # ...as meant
    out = images.optimize(src, tmp_path / "out")
    assert out["original_size"] == (1600, 1200)
    # The item's long axis ran down the stored frame, so after the turn it
    # runs across the output -- and the file is landscape, not a square.
    cols, (w, h) = _dark_columns(tmp_path / "out.jpg")
    assert w > h
    assert 0.30 < (cols[-1] - cols[0]) / w < 0.45


def test_a_photo_keeps_its_scale_and_its_scenery(tmp_path):
    """Nothing zooms, nothing crops: the backdrop the seller composed is still
    in every corner and the item takes up the share of the frame it was shot
    at. Tightening is the seller's call, in the editor."""
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=False)
    assert not out["background_removed"]
    with Image.open(tmp_path / "out.jpg") as done:
        w, h = done.size
        corners = [done.convert("L").getpixel(p)
                   for p in ((8, 8), (w - 9, 8), (8, h - 9), (w - 9, h - 9))]
    assert min(corners) > 200


def test_a_small_photo_is_never_upscaled(tmp_path):
    """Blowing a 900px photo up to 1600 adds blur, not detail; eBay would
    only shrink it again."""
    src = _photo(tmp_path, size=(900, 700), box=(200, 200, 600, 500), exif=False)
    out = images.optimize(src, tmp_path / "out")
    assert out["output_size"] == (900, 700)


def test_the_quality_setting_is_the_one_we_publish_at(tmp_path):
    assert images.JPEG_QUALITY == 90

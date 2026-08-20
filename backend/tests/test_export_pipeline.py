"""The export contract: what comes out of optimize() and lands on eBay.

Six properties, each of which someone could plausibly "improve" away:
upright, trimmed to the subject, centred on a square white canvas with even
padding, 1600x1600, JPEG at the agreed quality, and carrying no EXIF. The
last one is a privacy guarantee -- a phone photo knows where the seller
lives -- and it holds today only because Pillow drops metadata by default,
which is exactly the kind of thing a helpful refactor restores.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

from pathlib import Path  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

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


def test_output_is_a_1600_square_jpeg(tmp_path):
    out = images.optimize(_photo(tmp_path), tmp_path / "out")
    assert out["output_size"] == (1600, 1600)
    with Image.open(tmp_path / "out.jpg") as done:
        assert done.size == (1600, 1600)
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
        from PIL import ImageOps
        assert ImageOps.exif_transpose(raw).size == (1600, 1200)  # ...as meant
    out = images.optimize(src, tmp_path / "out")
    assert out["original_size"] == (1600, 1200)


def test_a_cutout_is_centred_with_padding_rather_than_cropped(tmp_path):
    """The framing rule for cut-out photos. The item is centred on white with
    an even margin, and the margin is not zero -- an item flush against the
    edge of a 220px search thumbnail reads as a mistake."""
    photo = Image.new("RGB", (1400, 1000), (255, 255, 255))
    ImageDraw.Draw(photo).rounded_rectangle((500, 300, 900, 700), radius=30,
                                            fill=(40, 40, 45))
    framed = images._frame_cutout(photo)
    assert framed.width == framed.height

    import numpy as np
    arr = np.asarray(framed.convert("L"), dtype=np.uint8)
    item = arr < 200
    ys, xs = np.nonzero(item)
    side = framed.width
    margins = [xs.min(), side - 1 - xs.max(), ys.min(), side - 1 - ys.max()]
    assert min(margins) > 0.04 * side, f"item is crowding the edge: {margins}"
    # Centred: opposing margins agree to within a couple of percent.
    assert abs(margins[0] - margins[1]) < 0.03 * side
    assert abs(margins[2] - margins[3]) < 0.03 * side


def test_framing_keeps_the_whole_item_even_when_it_is_long_and_thin(tmp_path):
    """A skateboard, a curtain rod, a pair of skis. Filling a square frame
    with one of these means cutting its ends off; padding does not."""
    photo = Image.new("RGB", (1400, 1000), (255, 255, 255))
    ImageDraw.Draw(photo).rectangle((150, 460, 1250, 540), fill=(30, 30, 30))
    framed = images._frame_cutout(photo)

    import numpy as np
    arr = np.asarray(framed.convert("L"), dtype=np.uint8) < 200
    xs = np.nonzero(arr.any(axis=0))[0]
    # The full 1100px length survived, scaled into the padded window.
    assert (xs.max() - xs.min()) >= 0.80 * framed.width
    assert xs.min() > 0 and xs.max() < framed.width - 1, "the ends were cut off"


def test_a_photo_that_keeps_its_background_is_still_cropped_to_fill(tmp_path):
    """The other half of the rule: white bars around a photo that kept its
    scenery look broken, so those still fill the frame."""
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=False)
    assert out["output_size"] == (1600, 1600)
    assert not out["background_removed"]


def test_the_quality_setting_is_the_one_we_publish_at(tmp_path):
    assert images.JPEG_QUALITY == 90

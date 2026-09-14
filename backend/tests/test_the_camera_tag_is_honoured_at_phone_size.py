"""A phone photo comes out the way the camera saw it, at phone size.

The photo pass asks a large JPEG to decode at a reduced scale (Image.draft)
before it honours the camera's EXIF orientation, and the orientation test
that already exists is small enough never to take that path -- and checks
only the size of what comes out, which a photo turned the wrong way round
would satisfy just the same. Reported as a batch where every item lay on
its side; what this pins is that the server's half of the story is right
for every one of the eight ways a camera can store a frame, at the size a
phone actually shoots.

Pillow only: an upright photo with a marker in two corners is stored each
of the eight ways and tagged accordingly, and each must come out with the
markers back where they started.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images  # noqa: E402

BACKDROP = (240, 238, 234)
RED = (220, 30, 30)
BLUE = (30, 30, 220)

# What a camera STORES for each Orientation tag: the inverse of the turn a
# viewer applies (Pillow: 6 -> ROTATE_270, 8 -> ROTATE_90, 5 -> TRANSPOSE,
# 7 -> TRANSVERSE, and the mirrors and the half turn are their own inverse).
STORED_AS = {
    1: None,
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_90,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_270,
}


def _upright(size=(3024, 4032)) -> Image.Image:
    """A 12MP portrait photo: an item standing in the lower middle, RED in
    the top-left corner and BLUE in the bottom-right, so every one of the
    eight turns is tellable from every other."""
    img = Image.new("RGB", size, BACKDROP)
    w, h = size
    d = ImageDraw.Draw(img)
    d.rectangle((w // 3, h // 2, 2 * w // 3, 5 * h // 6), fill=(40, 50, 70))
    d.rectangle((0, 0, w // 8, h // 8), fill=RED)
    d.rectangle((w - w // 8, h - h // 8, w, h), fill=BLUE)
    return img


def _corners(path: Path):
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        return im.size, im.getpixel((w // 16, h // 16)), im.getpixel((w - w // 16, h - h // 16))


def _near(px, colour, tol=40) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(px, colour))


@pytest.mark.parametrize("tag", sorted(STORED_AS))
def test_the_frame_comes_out_as_the_camera_saw_it(tmp_path, tag):
    stored_as = STORED_AS[tag]
    stored = _upright()
    if stored_as is not None:
        stored = stored.transpose(stored_as)
    exif = Image.Exif()
    exif[274] = tag
    src = tmp_path / "src.jpg"
    stored.save(src, "JPEG", quality=90, exif=exif)

    out = images.optimize(src, tmp_path / "out")

    size, top_left, bottom_right = _corners(tmp_path / "out.jpg")
    assert size == (1200, 1600), f"tag {tag}: came out {size}"
    assert _near(top_left, RED), f"tag {tag}: top-left is {top_left}, not the red marker"
    assert _near(bottom_right, BLUE), f"tag {tag}: bottom-right is {bottom_right}, not the blue marker"
    # And the size it reports as shot is the frame the seller composed --
    # portrait -- however the sensor lay.
    assert out["original_size"] == (3024, 4032)

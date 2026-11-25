"""A 16-bit greyscale photo comes out grey, not white.

A flatbed scan of a print, or a TIFF from a darkroom, is often 16-bit
greyscale. Pillow's convert("RGB") does not scale those values down to 8
bits, it CLIPS them at 255 -- so every pixel brighter than near-black became
pure white, and the listing photo of a print was an empty white square.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from backend.services import images  # noqa: E402


def test_a_mid_grey_scan_stays_mid_grey(tmp_path):
    src = tmp_path / "scan.png"
    Image.new("I;16", (600, 400), 30000).save(src)  # 30000 of 65535: mid grey
    with Image.open(src) as opened:
        assert opened.mode.startswith("I")  # really reaches the pass as 16-bit
    images.optimize(src, tmp_path / "out")
    with Image.open(tmp_path / "out.jpg") as saved:
        r, g, b = saved.convert("RGB").getpixel((300, 200))
    assert 105 <= r <= 130 and r == g == b, (r, g, b)


def test_a_photo_whose_values_already_fit_is_not_darkened():
    img = Image.new("I", (10, 10), 200)
    assert images.flatten(img).getpixel((5, 5)) == (200, 200, 200)

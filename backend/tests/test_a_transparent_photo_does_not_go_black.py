"""A photo that arrives already cut out must not come back on black.

Transparency has one dangerous property in this pipeline: a bare
`.convert("RGB")` does not drop it, it paints it BLACK. services/images has
_flatten for exactly that reason -- and the cutout path reached the source
photo without it, so a PNG cut-out, an iPhone "lift subject" shot or another
tool's export was handed to the matte as an item on a black field.

The browser half of the same defect is covered by
frontend/src/lib/uploadDownscale.test.js: a canvas exported as JPEG composites
what it cannot store onto black too, so photos were arriving black before the
server ever saw them.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

from pathlib import Path  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images  # noqa: E402


def _cutout_png(tmp: Path, size=(1400, 1400)) -> Path:
    """A photo of an item on nothing — every pixel outside it transparent."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((300, 300, 1100, 1100), fill=(190, 120, 60, 255))
    src = tmp / "cutout.png"
    img.save(src, "PNG")
    return src


def _corners(img: Image.Image):
    w, h = img.size
    return [img.getpixel(p) for p in
            ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3))]


def test_the_saved_photo_has_a_light_backdrop_not_a_black_one(tmp_path):
    out = images.optimize(_cutout_png(tmp_path), tmp_path / "out")
    with Image.open(tmp_path / "out.jpg") as saved:
        for px in _corners(saved.convert("RGB")):
            assert min(px) > 200, f"backdrop came out dark: {px}"
    assert out["output_size"] == (1400, 1400)  # as shot: never upscaled


def test_the_cutout_stage_is_handed_an_opaque_photo(tmp_path, monkeypatch):
    """The regression itself. Framing flattened; the background removal in
    between did not, and it is the stage that decides what the matte sees."""
    seen = {}

    def _spy(img, wait=None):
        seen["mode"] = img.mode
        seen["corner"] = img.convert("RGB").getpixel((2, 2))
        return None  # "found nothing": the photo is kept as shot

    monkeypatch.setattr(images, "cutout", _spy)
    images.optimize(_cutout_png(tmp_path), tmp_path / "out", remove_bg=True)

    assert seen["mode"] == "RGB"
    assert min(seen["corner"]) > 200, f"the matte was shown black: {seen['corner']}"

"""Square framing re-centres on the item; it never zooms and never cuts.

Two seller-visible failures live here. The first: a shirt shot to fill a
portrait photo came back with its shoulders and hem sliced away, because the
square window was centred on a subject taller than the window itself. The
second, the reason framing no longer tightens at all: the window used to
shrink to the subject plus ~30% and the resize to 1600px blew it back up, so
the backdrop the seller composed was cropped away and anything the cheap
corner-diff subject box under-measured — a strap, a pale sleeve — was cut off
in the gallery thumbnail. Tightening is now a manual choice (Crop and Smart
crop in the photo studio), so the window is always the largest square the
frame holds, slid over the item.

These build flat synthetic photos — a solid item on a backdrop — so the
subject is exactly known and "did we keep all of it" is countable. Skipped
where Pillow isn't installed (CI's lint+unit job skips the heavy image stack).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from backend.services import images  # noqa: E402

BACKDROP = (255, 255, 255)
ITEM = (60, 90, 140)


def _photo(w: int, h: int, sw: int, sh: int,
           backdrop: tuple = BACKDROP) -> Image.Image:
    """A `sw`x`sh` item centered on a `w`x`h` backdrop."""
    img = Image.new("RGB", (w, h), backdrop)
    img.paste(Image.new("RGB", (sw, sh), ITEM), ((w - sw) // 2, (h - sh) // 2))
    return img


def _item_px(img: Image.Image) -> int:
    """Item pixels in the frame — crops show up as a drop in this count."""
    px = img.convert("RGB").tobytes()
    return sum(1 for i in range(0, len(px), 3)
               if all(abs(px[i + c] - ITEM[c]) < 20 for c in range(3)))


def _item_box(img: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of the item in `img` — where framing put it."""
    rgb = img.convert("RGB")
    bg = Image.new("RGB", rgb.size, BACKDROP)
    from PIL import ImageChops
    box = ImageChops.difference(rgb, bg).convert("L").point(
        lambda v: 255 if v > 20 else 0).getbbox()
    assert box, "the item vanished from the frame"
    return box


def _kept(src: Image.Image) -> float:
    """Fraction of the item that survived framing (1.0 = nothing lost)."""
    out = images._fill_square(src)
    assert out.width == out.height, f"not square: {out.size}"
    return _item_px(out) / _item_px(src)


@pytest.mark.parametrize("w,h,sw,sh", [
    (750, 1000, 700, 900),   # the bug: shirt filling a portrait frame
    (750, 1000, 650, 950),   # taller still
    (1000, 750, 900, 650),   # the landscape mirror of it
    (600, 600, 570, 540),   # square frame, near-square item
    (750, 1000, 300, 500),   # small elongated item, room to spare
    (750, 750, 350, 375),   # modest item, room around it on every side
])
def test_whole_item_survives_framing(w, h, sw, sh):
    assert _kept(_photo(w, h, sw, sh)) == pytest.approx(1.0, abs=0.005)


def test_item_spanning_the_frame_is_padded_not_cropped():
    """No clean subject box (the item runs edge to edge) on a plain backdrop:
    pad out to square rather than center-cropping a quarter of it away."""
    img = Image.new("RGB", (750, 1000), BACKDROP)
    img.paste(Image.new("RGB", (740, 990), ITEM), (5, 5))
    assert _kept(img) == pytest.approx(1.0, abs=0.005)


@pytest.mark.parametrize("w,h,sw,sh", [
    (750, 1000, 300, 500),   # roomy portrait
    (1200, 800, 260, 300),   # roomy landscape
    (900, 900, 200, 200),    # small item, square frame
    (1000, 750, 500, 300),   # wide item with room to spare
])
def test_framing_never_zooms_in(w, h, sw, sh):
    """The window is the largest square the frame can hold — never smaller.
    An item with room around it keeps the size and the surroundings the seller
    shot it with; the pipeline does not magnify it into the frame."""
    src = _photo(w, h, sw, sh)
    out = images._fill_square(src)
    assert out.size == (min(w, h), min(w, h))
    assert _item_px(out) == _item_px(src), "the item was rescaled or clipped"


def test_the_window_slides_over_an_off_centre_item():
    """Centring is the whole job now: an item off to one side ends up in the
    middle of the square at its original scale, with the backdrop around it."""
    img = Image.new("RGB", (1200, 800), BACKDROP)
    img.paste(Image.new("RGB", (260, 300), ITEM), (500, 250))  # right of centre
    out = images._fill_square(img)
    assert out.size == (800, 800)
    box = _item_box(out)
    assert abs((box[0] + box[2]) / 2 - out.width / 2) <= 0.01 * out.width
    assert abs((box[1] + box[3]) / 2 - out.height / 2) <= 0.01 * out.height


def test_busy_background_still_center_crops():
    """No subject box and no plain backdrop — a textured scene. Behavior is
    unchanged there: the biggest centered square, no padding."""
    noise = Image.effect_noise((750, 1000), 90).convert("RGB")
    out = images._fill_square(noise)
    assert out.size == (750, 750)


def test_plain_backdrop_detection():
    plain = _photo(600, 800, 300, 350)
    assert images._plain_backdrop(images._flatten(plain)) is True
    busy = Image.effect_noise((600, 800), 90).convert("RGB")
    assert images._plain_backdrop(busy) is False

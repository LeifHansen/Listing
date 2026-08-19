"""Square framing must never cut the item off.

The seller-visible failure: a shirt shot to fill a portrait photo comes back
from the pipeline with its shoulders and hem sliced away, because _fill_square
took the biggest square the frame could hold and centered it on a subject that
was taller than that square. The old guard only caught ELONGATED subjects
(long side / short side > 1.35), so a 1.29-ratio shirt fell straight through
it and lost 600px off the top and bottom.

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
    (750, 750, 350, 375),   # modest item that should zoom, not pad
])
def test_whole_item_survives_framing(w, h, sw, sh):
    assert _kept(_photo(w, h, sw, sh)) == pytest.approx(1.0, abs=0.005)


def test_item_spanning_the_frame_is_padded_not_cropped():
    """No clean subject box (the item runs edge to edge) on a plain backdrop:
    pad out to square rather than center-cropping a quarter of it away."""
    img = Image.new("RGB", (750, 1000), BACKDROP)
    img.paste(Image.new("RGB", (740, 990), ITEM), (5, 5))
    assert _kept(img) == pytest.approx(1.0, abs=0.005)


def test_roomy_photo_still_crops_in_on_the_item():
    """The fix must not turn framing into "always pad": an item with room
    around it still gets a tight square crop, so it fills the listing photo."""
    out = images._fill_square(_photo(750, 750, 225, 240))
    assert out.width < 750
    assert _item_px(out) / (out.width * out.height) > 0.09


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

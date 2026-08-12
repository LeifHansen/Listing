"""Unit tests for the pure-Pillow parts of the image pipeline: matte
refinement sensitivity, enclosed-hole restoration, and subject-aware square
framing. No rembg/model inference involved — these all run on synthetic
images."""
from __future__ import annotations

from PIL import Image

from backend.services import images


BROWN = (150, 110, 70)   # wood-table backdrop
BLUE = (30, 60, 120)     # the item
RED = (200, 30, 30)


def _count_color(img: Image.Image, color: tuple, tol: int = 20) -> int:
    from PIL import ImageChops
    diff = ImageChops.difference(
        img, Image.new("RGB", img.size, color)).convert("L")
    return sum(diff.histogram()[:tol + 1])


# --- _fill_enclosed_holes ---------------------------------------------------

def _item_with_holes():
    """A blue item on a brown backdrop. The model's matte (alpha) wrongly
    punched two holes in it: a white label (should be restored — it's part of
    the item) and a genuine see-through gap showing the brown backdrop
    (should stay removed)."""
    rgb = Image.new("RGB", (200, 200), BROWN)
    rgb.paste(BLUE, (40, 40, 160, 160))
    rgb.paste((245, 245, 245), (85, 85, 115, 115))   # white label on the item
    rgb.paste(BROWN, (50, 90, 70, 110))              # see-through gap
    alpha = Image.new("L", (200, 200), 0)
    alpha.paste(255, (40, 40, 160, 160))
    alpha.paste(0, (85, 85, 115, 115))
    alpha.paste(0, (50, 90, 70, 110))
    return rgb, alpha


def test_fill_enclosed_holes_restores_item_interior():
    rgb, alpha = _item_with_holes()
    fixed = images._fill_enclosed_holes(rgb, alpha)
    # The white label is enclosed by the item and doesn't match the backdrop:
    # it must come back solid.
    assert fixed.getpixel((100, 100)) >= 200
    # The see-through gap matches the backdrop — it stays removed.
    assert fixed.getpixel((60, 100)) <= 40
    # The real background (border-connected) stays removed.
    assert fixed.getpixel((10, 10)) == 0
    assert fixed.getpixel((190, 190)) == 0
    # The item itself is untouched.
    assert fixed.getpixel((50, 50)) == 255


def test_fill_enclosed_holes_white_on_white_stays_removed():
    # A white patch on a white backdrop composites to white either way; the
    # backdrop-match test intentionally leaves it removed.
    rgb = Image.new("RGB", (200, 200), (250, 250, 250))
    rgb.paste(RED, (40, 40, 160, 160))
    rgb.paste((248, 248, 248), (85, 85, 115, 115))
    alpha = Image.new("L", (200, 200), 0)
    alpha.paste(255, (40, 40, 160, 160))
    alpha.paste(0, (85, 85, 115, 115))
    fixed = images._fill_enclosed_holes(rgb, alpha)
    assert fixed.getpixel((100, 100)) <= 40


def test_fill_enclosed_holes_no_holes_is_identity():
    rgb = Image.new("RGB", (120, 120), BROWN)
    rgb.paste(BLUE, (30, 30, 90, 90))
    alpha = Image.new("L", (120, 120), 0)
    alpha.paste(255, (30, 30, 90, 90))
    assert images._fill_enclosed_holes(rgb, alpha).tobytes() == alpha.tobytes()


# --- _refine_alpha ----------------------------------------------------------

def test_refine_alpha_keeps_uncertain_subject_pixels():
    # Alpha 80 = the model was unsure but leaning "subject" (fine fabric,
    # glossy or white-ish parts of the item). The old 90 floor erased these
    # outright — the "removes parts of the subject" bug.
    alpha = Image.new("L", (200, 200), 0)
    alpha.paste(80, (70, 70, 130, 130))
    refined = images._refine_alpha(alpha)
    assert refined.getpixel((100, 100)) > 30


def test_refine_alpha_still_kills_background_ghost():
    # Faint mid-alpha over the background (the "ghost") must still go to 0.
    alpha = Image.new("L", (200, 200), 0)
    alpha.paste(40, (70, 70, 130, 130))
    refined = images._refine_alpha(alpha)
    assert refined.getpixel((100, 100)) == 0


def test_refine_alpha_confident_subject_goes_solid():
    alpha = Image.new("L", (200, 200), 0)
    alpha.paste(200, (70, 70, 130, 130))
    refined = images._refine_alpha(alpha)
    assert refined.getpixel((100, 100)) == 255


# --- _fill_square -----------------------------------------------------------

def test_fill_square_pads_frame_filling_subject():
    # A subject that fills a portrait frame can't fit any square window — the
    # old center square crop chopped it. Now: rect-crop + pad to square.
    img = Image.new("RGB", (300, 400), (255, 255, 255))
    img.paste(RED, (5, 30, 295, 370))  # 290x340: not elongated, > min side
    before = _count_color(img, RED)
    out = images._fill_square(img, box=(5, 30, 295, 370))
    assert out.width == out.height
    assert _count_color(out, RED) == before  # nothing of the item was cut off


def test_fill_square_still_tight_crops_normal_subjects():
    # A small centered subject keeps the no-letterbox behavior: a square
    # window around it, cropped from the frame (no padding).
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    img.paste(RED, (180, 120, 240, 180))  # 60x60, comfortably fits a square
    before = _count_color(img, RED)
    out = images._fill_square(img, box=(180, 120, 240, 180))
    assert out.width == out.height
    assert out.width == 150  # max(min(400,300)//2, 60*1.3) — the window rule
    assert _count_color(out, RED) == before


def test_fill_square_elongated_subject_still_pads():
    img = Image.new("RGB", (400, 300), (255, 255, 255))
    img.paste(RED, (40, 130, 360, 170))  # 320x40 — a ski/bat shape
    before = _count_color(img, RED)
    out = images._fill_square(img, box=(40, 130, 360, 170))
    assert out.width == out.height
    assert _count_color(out, RED) == before


# --- _compose_on_white ------------------------------------------------------

def test_compose_on_white_returns_frame_box_covering_shadow():
    rgb = Image.new("RGB", (200, 200), RED)
    alpha = Image.new("L", (200, 200), 0)
    alpha.paste(255, (50, 50, 150, 150))
    out, box = images._compose_on_white(rgb, alpha, shadow=True)
    assert out.getpixel((100, 100)) == RED
    assert out.getpixel((5, 5)) == (255, 255, 255)
    # The frame box must cover the subject and its down-right shadow tail.
    assert box[0] <= 50 and box[1] <= 50
    assert box[2] > 150 and box[3] > 150


def test_remote_compose_returns_image_and_frame_box():
    cut = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    cut.paste(RED + (255,), (50, 50, 150, 150))
    out, box = images._remote_compose(cut, ValueError, "TestEngine")
    assert out.size == (200, 200)
    assert box is not None
    assert box[0] <= 50 and box[2] >= 150


def test_remote_compose_raises_on_empty_cutout():
    import pytest

    empty = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    with pytest.raises(ValueError, match="TestEngine"):
        images._remote_compose(empty, ValueError, "TestEngine")


def test_compose_on_white_no_shadow_box_is_subject_bbox():
    rgb = Image.new("RGB", (200, 200), RED)
    alpha = Image.new("L", (200, 200), 0)
    alpha.paste(255, (50, 50, 150, 150))
    _out, box = images._compose_on_white(rgb, alpha, shadow=False)
    assert box == (50, 50, 150, 150)

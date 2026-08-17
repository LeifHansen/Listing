"""Interior-hole repair for background removal.

The failure these cover is the one sellers actually report: the matte marks
something INSIDE the item (a printed graphic, a bright panel, a glossy face) as
background, so the cutout comes back with white holes punched through the
middle of the product. The repair puts those regions back while leaving real
see-through gaps — a mug handle, the space between straps — alone.

No model runs here: the tests feed hand-built mattes straight to the repair, so
they're fast and deterministic. Skipped where Pillow/numpy aren't installed
(CI's lint+unit job deliberately skips the heavy image stack).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from backend.services import images  # noqa: E402

SIZE = 400
ITEM = (30, 60, 180)      # a saturated blue item
GRAPHIC = (240, 200, 40)  # its printed graphic — what the model tends to eat
BACKDROP = (250, 250, 250)


def _scene(hole_color=GRAPHIC, hole_box=(150, 150, 250, 250),
           subject_box=(80, 80, 320, 320)):
    """A photo of a rectangular item on a near-white backdrop, plus the matte a
    model produced for it: the subject minus `hole_box`, which is the region it
    wrongly called background."""
    photo = Image.new("RGB", (SIZE, SIZE), BACKDROP)
    photo.paste(Image.new("RGB", (subject_box[2] - subject_box[0],
                                  subject_box[3] - subject_box[1]), ITEM),
                subject_box[:2])
    if hole_box:
        photo.paste(Image.new("RGB", (hole_box[2] - hole_box[0],
                                      hole_box[3] - hole_box[1]), hole_color),
                    hole_box[:2])
    alpha = Image.new("L", (SIZE, SIZE), 0)
    alpha.paste(255, subject_box)
    if hole_box:
        alpha.paste(0, hole_box)
    return photo, alpha


def _at(img, xy):
    return img.getpixel(xy)


def test_interior_hole_is_restored():
    """A graphic punched out of the item's middle comes back opaque."""
    photo, alpha = _scene()
    fixed, added = images._fill_interior_holes(alpha, photo)

    assert added is not None
    assert _at(fixed, (200, 200)) == 255            # hole centre restored
    assert _at(fixed, (100, 100)) == 255            # item untouched
    assert _at(fixed, (10, 10)) == 0                # backdrop still removed
    assert _at(fixed, (200, 40)) == 0               # backdrop above the item too


def test_backdrop_outside_the_subject_is_never_filled():
    """Repair only ever adds pixels sealed inside the subject — the removed
    backdrop stays removed, including a bay that opens onto the frame edge."""
    photo, alpha = _scene()
    # Carve a notch out of the item's top-right, open to the frame: that's
    # background reaching the border, not an interior hole — and it's dark, so
    # it's nothing like the backdrop either.
    alpha.paste(0, (280, 100, 400, 140))
    photo.paste(Image.new("RGB", (120, 40), (20, 20, 20)), (280, 100))
    fixed, _ = images._fill_interior_holes(alpha, photo)

    assert _at(fixed, (300, 120)) == 0              # the open notch stays out
    assert _at(fixed, (399, 120)) == 0
    assert _at(fixed, (200, 200)) == 255            # the sealed hole still fills


def test_see_through_gap_is_left_alone():
    """A real hole in the item (a handle) shows the backdrop through it — that
    must stay transparent instead of being filled back in."""
    photo, alpha = _scene(hole_color=BACKDROP)
    fixed, added = images._fill_interior_holes(alpha, photo)

    assert added is None
    assert _at(fixed, (200, 200)) == 0
    assert list(np.asarray(fixed).ravel()) == list(np.asarray(alpha).ravel())


def test_dark_backdrop_gap_versus_dark_item_interior():
    """The backdrop reference is the backdrop we actually removed, not an
    assumption that backdrops are white: on a black table, a gap showing the
    table stays open and a bright interior still fills."""
    photo = Image.new("RGB", (SIZE, SIZE), (12, 12, 12))
    photo.paste(Image.new("RGB", (240, 240), (200, 30, 30)), (80, 80))
    alpha = Image.new("L", (SIZE, SIZE), 0)
    alpha.paste(255, (80, 80, 320, 320))

    gap = photo.copy()
    gap.paste(Image.new("RGB", (60, 60), (12, 12, 12)), (170, 170))
    holed = alpha.copy()
    holed.paste(0, (170, 170, 230, 230))
    fixed, added = images._fill_interior_holes(holed, gap)
    assert added is None                              # shows the black table
    assert _at(fixed, (200, 200)) == 0

    lit = photo.copy()
    lit.paste(Image.new("RGB", (60, 60), (250, 240, 120)), (170, 170))
    fixed, added = images._fill_interior_holes(holed, lit)
    assert added is not None                          # a bright printed panel
    assert _at(fixed, (200, 200)) == 255


def test_repair_survives_a_matte_that_ate_most_of_the_item():
    """The worst-reported case: the model keeps a rim and calls the whole body
    of the garment background. Everything sealed inside comes back."""
    photo, alpha = _scene(hole_box=(90, 90, 310, 310), hole_color=(40, 70, 190))
    fixed, added = images._fill_interior_holes(alpha, photo)

    assert added is not None
    kept = np.asarray(fixed) >= 128
    subject = np.zeros((SIZE, SIZE), bool)
    subject[80:320, 80:320] = True
    # The rim plus the restored body = essentially the whole item back.
    assert kept.sum() >= 0.97 * subject.sum()
    assert not (kept & ~subject).any()


def test_compose_on_white_shows_item_pixels_through_the_repaired_hole():
    """End of the pipeline: the restored region composites the item's own
    pixels, not white."""
    photo, alpha = _scene()
    out = images._compose_on_white(photo, alpha, shadow=False)

    assert _at(out, (200, 200)) == GRAPHIC           # graphic is back
    assert _at(out, (100, 100)) == ITEM
    assert _at(out, (10, 10)) == images.WHITE        # backdrop still white


def test_remote_cutout_holes_are_patched_from_the_original_photo():
    """A remote engine's PNG can carry anything (often black) under its
    transparent pixels, so a restored hole must take its colour from the source
    photo rather than from whatever the cutout had there."""
    photo, alpha = _scene()
    cut = photo.copy()
    cut.paste(Image.new("RGB", (100, 100), (0, 0, 0)), (150, 150))  # engine zeroed it
    out = images._compose_on_white(cut, alpha, shadow=False, source=photo)

    assert _at(out, (200, 200)) == GRAPHIC
    assert _at(out, (10, 10)) == images.WHITE


def test_off_switch():
    """BG_FILL_HOLES=off leaves the matte exactly as the model produced it."""
    photo, alpha = _scene()
    images._FILL_HOLES = False
    try:
        fixed, added = images._fill_interior_holes(alpha, photo)
    finally:
        images._FILL_HOLES = True
    assert added is None
    assert fixed is alpha


def test_border_flood_reaches_around_obstacles():
    """The flood is a real 4-connected reachability test, not a straight-line
    one: background that snakes around the subject to the frame is background,
    not an interior hole."""
    bg = np.ones((60, 60), bool)
    bg[10:50, 10:50] = False          # solid subject block...
    bg[20:40, 20:40] = True           # ...with a sealed cavity
    bg[10:20, 25:30] = True           # ...and a corridor from the cavity, then
    bg[5:15, 25:30] = True            # out to the open background

    reached = images._border_connected(bg)
    assert reached[30, 30]            # cavity is connected via the corridor
    assert not reached[15, 15]        # subject is never "reached"

    sealed = bg.copy()
    sealed[10:20, 25:30] = False      # plug the corridor
    reached = images._border_connected(sealed)
    assert not reached[30, 30]        # now the cavity really is enclosed
    assert reached[0, 0]

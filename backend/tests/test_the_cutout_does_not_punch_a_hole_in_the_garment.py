"""A hole in a shirt is not a hole through a ring.

The report, with a screenshot of ten photos of one Scotch & Soda camp shirt:
"significant improvement in image optimization and back ground remover, but
still removing some internal parts of the image." The shirt is black across
the shoulders with a bright print below. The print survived every cutout. The
black did not — a shoulder gone on one photo, a sleeve on another, a white
hole punched through the middle of the back on a third.

NEITHER EXISTING GUARD COULD SEE IT, and not by accident. `_fill_interior`
and `_interior_solidity` both begin at "what the model SAW" — alpha above
_ALPHA_LOW — so a region the model set to ZERO is not seen, is therefore not
interior, and is therefore background as far as either is concerned. That
reading is right for the hole through a ring and exactly wrong for a shirt.
Meanwhile solidity stays high (the fabric that survived is perfectly opaque),
coverage stays high, the shape is one tidy object: every gate passes and the
photo ships with a hole in it. test_the_cutout_does_not_leave_a_ghost pins the
hedge failure and test_the_cutout_does_not_eat_the_artwork pins the wrong-
subject one; this is the third shape, and it needed a third measure.

What separates the two cases is not the matte — draw them and they are the
same picture — but the PHOTO underneath. A hole through a ring shows the
backdrop. A hole in a shirt shows the shirt. So `_reclaim_enclosed` is the one
repair in this file that reads the photo, and it asks each enclosed region
which of the two it resembles.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images  # noqa: E402

SIZE = (1200, 900)
BACKDROP = (232, 232, 230)     # a pale studio sweep
BLACK_FABRIC = (24, 26, 38)    # the shirt's shoulders
PRINT = (232, 84, 96)          # the bright print below them


def _matte(draw_fn, size=SIZE) -> Image.Image:
    a = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(a), size)
    return a


def _photo(draw_fn, size=SIZE) -> Image.Image:
    img = Image.new("RGB", size, BACKDROP)
    draw_fn(ImageDraw.Draw(img), size)
    return img


# --- the shirt, and the hole punched in it ---------------------------------

def _shirt_body(d, size):
    w, h = size
    d.rectangle([w * .22, h * .18, w * .78, h * .84], fill=255)


def _shirt_photo(d, size):
    """Black across the shoulders, bright print below — the reported garment."""
    w, h = size
    d.rectangle([w * .22, h * .18, w * .78, h * .50], fill=BLACK_FABRIC)
    d.rectangle([w * .22, h * .50, w * .78, h * .84], fill=PRINT)


def _shirt_with_the_shoulder_dropped(d, size):
    """What the model actually returned: the body kept, a chunk of the black
    shoulder set to zero — enclosed by fabric on every side."""
    _shirt_body(d, size)
    w, h = size
    d.rectangle([w * .30, h * .24, w * .55, h * .44], fill=0)


def test_the_black_the_model_dropped_is_given_back():
    """The report. The hole is enclosed by shirt and looks like shirt, so it
    was never backdrop and the matte gets it back."""
    alpha = _matte(_shirt_with_the_shoulder_dropped)
    photo = _photo(_shirt_photo)
    hole = (int(SIZE[0] * .42), int(SIZE[1] * .34))

    assert alpha.getpixel(hole) == 0, "the fixture is not reproducing the bug"
    repaired = images._reclaim_enclosed(photo, alpha)
    assert repaired.getpixel(hole) >= 250, (
        "the shoulder is still missing from the matte")


def test_the_repaired_matte_survives_hardening():
    """`cutout` hardens after repairing, and a repair that hardening undoes
    would be no repair at all."""
    alpha = images._harden(images._fill_interior(images._reclaim_enclosed(
        _photo(_shirt_photo), _matte(_shirt_with_the_shoulder_dropped))))
    assert alpha.getpixel((int(SIZE[0] * .42), int(SIZE[1] * .34))) >= 250


def test_what_ships_carries_the_shoulder(monkeypatch):
    """End to end through cutout(): the pixel that was gone comes back as the
    shirt's own black, not as white."""
    monkeypatch.setattr(
        images, "_mask",
        lambda rgb, wait=None: _matte(_shirt_with_the_shoulder_dropped, rgb.size))
    out = images.cutout(_photo(_shirt_photo))
    assert out is not None, "the whole photo was refused"
    r, g, b = out.getpixel((int(SIZE[0] * .42), int(SIZE[1] * .34)))
    assert r < 80 and g < 80 and b < 90, (
        f"the shoulder shipped as {(r, g, b)} rather than the shirt's black")


# --- and the holes that are real -------------------------------------------

def test_a_hole_through_a_ring_still_shows_the_backdrop():
    """The case the gate at _ALPHA_LOW was protecting, now asked of the photo
    instead of the matte. The hole shows the sweep, so it stays a hole —
    filling it would paste a disc of old backdrop into the middle of the
    ring."""
    def ring(d, size):
        w, h = size
        d.ellipse([w * .34, h * .22, w * .66, h * .78], fill=255)
        d.ellipse([w * .43, h * .40, w * .57, h * .62], fill=0)

    def ring_photo(d, size):
        w, h = size
        d.ellipse([w * .34, h * .22, w * .66, h * .78], fill=(198, 160, 62))
        d.ellipse([w * .43, h * .40, w * .57, h * .62], fill=BACKDROP)

    alpha = _matte(ring)
    repaired = images._reclaim_enclosed(_photo(ring_photo), alpha)
    hole = (SIZE[0] // 2, SIZE[1] // 2)
    assert repaired.getpixel(hole) == 0, "the ring was filled in"


def test_the_gap_between_a_pair_of_boots_is_untouched():
    """Real backdrop with a way out to the frame edge. It is not enclosed, so
    this never even asks what colour it is."""
    def two_boots(d, size):
        w, h = size
        d.rectangle([w * .12, h * .30, w * .44, h * .72], fill=255)
        d.rectangle([w * .56, h * .30, w * .88, h * .72], fill=255)

    def boots_photo(d, size):
        w, h = size
        d.rectangle([w * .12, h * .30, w * .44, h * .72], fill=BLACK_FABRIC)
        d.rectangle([w * .56, h * .30, w * .88, h * .72], fill=BLACK_FABRIC)

    alpha = _matte(two_boots)
    repaired = images._reclaim_enclosed(_photo(boots_photo), alpha)
    assert repaired.getpixel((SIZE[0] // 2, SIZE[1] // 2)) == 0


def test_a_dark_gap_that_reaches_the_edge_is_still_backdrop():
    """The two-sided test never runs on an open gap, which matters most when
    the backdrop is DARK and would otherwise read as fabric: a shirt shot on
    charcoal, photographed with daylight between the sleeve and the body."""
    def open_gap(d, size):
        w, h = size
        d.rectangle([w * .15, h * .10, w * .40, h * .95], fill=255)
        d.rectangle([w * .60, h * .10, w * .85, h * .95], fill=255)

    dark = (30, 30, 34)
    img = Image.new("RGB", SIZE, dark)
    dd = ImageDraw.Draw(img)
    dd.rectangle([SIZE[0] * .15, SIZE[1] * .10, SIZE[0] * .40, SIZE[1] * .95],
                 fill=PRINT)
    dd.rectangle([SIZE[0] * .60, SIZE[1] * .10, SIZE[0] * .85, SIZE[1] * .95],
                 fill=PRINT)

    repaired = images._reclaim_enclosed(img, _matte(open_gap))
    assert repaired.getpixel((SIZE[0] // 2, SIZE[1] // 2)) == 0


def test_an_enclosed_patch_of_backdrop_is_left_alone():
    """A belt buckle's gap, the space inside a handle: enclosed, but it shows
    the sweep. Colour is the whole difference between this and the shirt."""
    def with_gap(d, size):
        _shirt_body(d, size)
        w, h = size
        d.rectangle([w * .40, h * .30, w * .55, h * .45], fill=0)

    def photo_with_real_gap(d, size):
        _shirt_photo(d, size)
        w, h = size
        d.rectangle([w * .40, h * .30, w * .55, h * .45], fill=BACKDROP)

    repaired = images._reclaim_enclosed(_photo(photo_with_real_gap),
                                        _matte(with_gap))
    assert repaired.getpixel((int(SIZE[0] * .47), int(SIZE[1] * .37))) == 0


# --- and it must not disturb the healthy case ------------------------------

def test_a_matte_with_nothing_enclosed_is_returned_untouched():
    """The overwhelmingly common photo. No labelling of colours, no
    allocation — the same object back."""
    alpha = _matte(_shirt_body)
    assert images._reclaim_enclosed(_photo(_shirt_photo), alpha) is alpha


def test_an_item_filling_the_frame_has_no_backdrop_to_compare_with():
    """No evidence either way, so nothing is invented: the matte comes back as
    it went in rather than being painted over on a guess."""
    def edge_to_edge(d, size):
        w, h = size
        d.rectangle([0, 0, w, h], fill=255)
        d.rectangle([w * .40, h * .40, w * .60, h * .60], fill=0)

    alpha = _matte(edge_to_edge)
    assert images._reclaim_enclosed(_photo(_shirt_photo), alpha) is alpha


def test_the_repair_is_the_reason_the_shirt_ships(monkeypatch):
    """The counterfactual, so this file cannot pass by accident: without the
    new pass the same photo goes out with the hole still in it."""
    alpha = _matte(_shirt_with_the_shoulder_dropped)
    hole = (int(SIZE[0] * .42), int(SIZE[1] * .34))
    old_way = images._harden(images._fill_interior(alpha))
    assert old_way.getpixel(hole) == 0, (
        "the old pipeline already repaired this — the test proves nothing")

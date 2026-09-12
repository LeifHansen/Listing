"""A picture inside a border is one object, and the picture is the item.

The fourth shape of the same failure. test_the_cutout_does_not_eat_the_artwork
pins the model keeping the wrong fifth of the photo, _does_not_leave_a_ghost
pins it hedging an item into a smear, _does_not_punch_a_hole_in_the_garment
pins it zeroing a chunk of fabric. This one is what happens when the thing it
zeroes is BORDERED: a framed watercolour comes back as an empty picture frame
on white, and every guard in the file signs it off.

Why they all pass. The frame survives as one connected ring holding 99% of
what was kept, its interior is one enclosed region, the matte is perfectly
opaque wherever it kept anything — coverage 0.19, solidity 0.95, largest
region 0.99, box fill 0.54 against a floor of 0.30. A ring is a respectable
shape. Nothing about it says the middle is missing.

And `_reclaim_enclosed`, which exists precisely to give back what the model
zeroed, throws this one away on purpose. It asks whether the enclosed region
looks like the backdrop, by mean colour, and a white mount plus a pale sky
plus a green tree averages to (203, 219, 225) against a backdrop's
(211, 208, 201) — 27 apart, inside _HOLE_COLOUR_DIST. Read as backdrop. Read
as a hole. Deleted.

SIZE CANNOT SEPARATE THEM, which is worth stating because it is the obvious
fix and it does not work: measured on these fixtures the artwork's hole is
0.61 of the matte, a wreath's is 0.76 and an empty frame's is 1.14, and both
of those are real holes that must stay holes.

What does separate them is that a backdrop seen through a gap IS the
backdrop — the same sweep, the same table, and so the same flatness — while
anything photographed inside a border is a picture and carries detail. So the
second question is asked of SPREAD: enough detail to be a picture at all, and
markedly more of it than this photo's own backdrop carries. Both halves are
needed. The ratio alone fills a ring's hole, because a backdrop drawn as one
flat colour has a spread of zero and everything beats a multiple of zero.

These tests draw the matte themselves, so they run without rembg.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images  # noqa: E402

SIZE = (1200, 900)
BACKDROP = (232, 232, 230)     # a pale studio sweep
FRAME = (120, 85, 45)          # the moulding

# Where the frame sits, and where its opening is, as fractions of the frame.
OUTER = (.30, .12, .70, .88)
INNER = (.34, .18, .66, .82)


def _box(size, rect):
    w, h = size
    return [w * rect[0], h * rect[1], w * rect[2], h * rect[3]]


def _matte(draw_fn, size=SIZE) -> Image.Image:
    a = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(a), size)
    return a


def _photo(draw_fn, size=SIZE) -> Image.Image:
    img = Image.new("RGB", size, BACKDROP)
    draw_fn(ImageDraw.Draw(img), size)
    return img


def _frame_only(d, size):
    """What the model returned: the moulding kept, everything inside it zero."""
    d.rectangle(_box(size, OUTER), fill=255)
    d.rectangle(_box(size, INNER), fill=0)


def _framed_picture(d, size):
    """The photo: a moulding, a broad white mount, and a painting inside it.

    The painting is what makes this a picture rather than a hole — bands of
    sky, a horizon and a tree, which is detail a studio sweep does not have.
    The mount is broad on purpose: it is what drags the interior's MEAN back
    towards the backdrop's, which is the whole reason the colour test cannot
    answer this one (see the test below, which pins that number).
    """
    w, h = size
    d.rectangle(_box(size, OUTER), fill=FRAME)
    d.rectangle(_box(size, INNER), fill=(250, 249, 244))        # the mount
    d.rectangle([w * .42, h * .32, w * .58, h * .49], fill=(150, 190, 220))   # sky
    d.rectangle([w * .42, h * .49, w * .58, h * .60], fill=(105, 145, 85))    # ground
    d.ellipse([w * .46, h * .42, w * .52, h * .58], fill=(45, 85, 50))        # a tree


def _middle(size=SIZE):
    """A point inside the frame's opening, in the painting's sky."""
    return (int(size[0] * .50), int(size[1] * .36))


# --- the report ------------------------------------------------------------

def test_the_painting_inside_the_frame_is_given_back():
    """The whole bug. The interior is enclosed and averages out to something
    near the backdrop, but it is a picture, so it is the item."""
    alpha = _matte(_frame_only)
    assert alpha.getpixel(_middle()) == 0, "the fixture is not reproducing the bug"

    repaired = images._reclaim_enclosed(_photo(_framed_picture), alpha)
    assert repaired.getpixel(_middle()) >= 250, (
        "the painting was thrown away as a hole")


def test_the_colour_test_on_its_own_would_have_thrown_this_away():
    """The measurement the fix is answering, pinned so nobody has to take the
    docstring's word for it: by mean colour alone, this interior reads as the
    backdrop."""
    photo = _photo(_framed_picture)
    inside = _matte(lambda d, size: d.rectangle(_box(size, INNER), fill=255))
    outside = _matte(lambda d, size: (
        d.rectangle([0, 0, size[0], size[1]], fill=255),
        d.rectangle(_box(size, OUTER), fill=0)))

    apart = images._apart(images._mean_rgb(photo, inside),
                          images._mean_rgb(photo, outside))
    assert apart <= images._HOLE_COLOUR_DIST, (
        f"the interior is {apart:.0f} from the backdrop, so the colour test "
        "would have caught this and the fixture proves nothing")


def test_what_ships_carries_the_painting(monkeypatch):
    """End to end through cutout(). The pixel that was deleted comes back as
    the painting's own sky, not as the white canvas it was composited onto —
    which is what an empty picture frame is made of."""
    monkeypatch.setattr(images, "_mask",
                        lambda rgb, wait=None: _matte(_frame_only, rgb.size))
    out = images.cutout(_photo(_framed_picture))
    assert out is not None, "the whole photo was refused"
    r, g, b = out.getpixel(_middle())
    assert (r, g, b) != images.WHITE, "the frame shipped empty"
    assert b > r and b > 180 and g > 150, (
        f"the sky shipped as {(r, g, b)} rather than the painting's blue")


def test_the_whole_face_is_solid_after_hardening():
    """A hedged band is not an edge when it is in the middle of a picture.

    The model does not answer zero everywhere inside the frame — it answers
    about 70 across the mount, which is over _ALPHA_LOW and so belongs to no
    enclosed region at all, and too ragged for _fill_interior to promote.
    _harden turns a 70 into a 53, and the band ships at a fifth of its
    opacity: the same white smear as before, in a thinner ring.
    """
    def hedged(d, size):
        _frame_only(d, size)
        w, h = size
        # the band the model was unsure about, just inside the moulding
        d.rectangle([w * .345, h * .19, w * .655, h * .81], fill=70)
        d.rectangle([w * .38, h * .24, w * .62, h * .76], fill=0)

    alpha = images._harden(images._fill_interior(images._reclaim_enclosed(
        _photo(_framed_picture), _matte(hedged))))
    band = (int(SIZE[0] * .355), int(SIZE[1] * .50))
    assert alpha.getpixel(band) >= 250, (
        "the hedged band inside the frame still ships half rubbed out")
    assert alpha.getpixel(_middle()) >= 250


def test_the_outer_edge_of_the_frame_stays_soft():
    """Filling the face must reach the inside and never the rim. Soft alpha at
    the item's own boundary is the matte doing its job, and hardening it
    trades a white smear for a jagged edge."""
    def soft_edged(d, size):
        w, h = size
        d.rectangle(_box(size, OUTER), fill=255)
        d.rectangle(_box(size, INNER), fill=0)
        # one row of half-alpha along the top of the moulding
        d.rectangle([w * .30, h * .12, w * .70, h * .125], fill=128)

    repaired = images._reclaim_enclosed(_photo(_framed_picture),
                                        _matte(soft_edged))
    rim = (int(SIZE[0] * .50), int(SIZE[1] * .122))
    assert repaired.getpixel(rim) < 250, "the soft outer rim was hardened"


# --- and the holes that are still holes ------------------------------------

def test_an_empty_frame_still_shows_the_backdrop_through_it():
    """A frame with nothing in it is a real product and a real hole: what is
    inside it IS the sweep behind it, so it is as flat as the sweep."""
    def bare(d, size):
        d.rectangle(_box(size, OUTER), fill=FRAME)
        d.rectangle(_box(size, INNER), fill=BACKDROP)

    repaired = images._reclaim_enclosed(_photo(bare), _matte(_frame_only))
    assert repaired.getpixel(_middle()) == 0, "the empty frame was filled in"


def test_a_wreath_is_not_a_picture():
    """The same shape and nearly the same size of hole — 0.76 of the matte
    against the artwork's 0.61 — decided the other way, on detail."""
    def ring(d, size):
        w, h = size
        d.ellipse([w * .30, h * .10, w * .70, h * .90], fill=255)
        d.ellipse([w * .38, h * .26, w * .62, h * .74], fill=0)

    def ring_photo(d, size):
        w, h = size
        d.ellipse([w * .30, h * .10, w * .70, h * .90], fill=(70, 95, 60))
        d.ellipse([w * .38, h * .26, w * .62, h * .74], fill=BACKDROP)

    repaired = images._reclaim_enclosed(_photo(ring_photo), _matte(ring))
    assert repaired.getpixel((SIZE[0] // 2, SIZE[1] // 2)) == 0, (
        "the wreath was filled in")


def test_a_hole_too_small_to_be_the_face_keeps_its_colour_verdict():
    """Openwork is not asked this question at all. A mug's handle, a basket's
    weave, the gap under a shoe's laces — every one of them is a few percent
    of the matte, and a statistic taken over a handful of cells is not worth
    trading a well-understood rule for. See _HOLE_BIG_SHARE.

    So: the same picture, in a small opening. Detail says picture (spread 57,
    against a floor of 10) and colour says backdrop (31, inside 40). Only the
    size gate stands between them, and a hole worth 3% of the matte is not
    the item's own face.
    """
    small = (.46, .44, .54, .56)

    def locket(d, size):
        d.rectangle(_box(size, OUTER), fill=255)
        d.rectangle(_box(size, small), fill=0)

    def locket_photo(d, size):
        w, h = size
        d.rectangle(_box(size, OUTER), fill=FRAME)
        d.rectangle(_box(size, small), fill=(250, 249, 244))
        d.rectangle([w * .47, h * .46, w * .53, h * .50], fill=(150, 190, 220))
        d.ellipse([w * .485, h * .505, w * .515, h * .54], fill=(45, 85, 50))

    repaired = images._reclaim_enclosed(_photo(locket_photo), _matte(locket))
    assert repaired.getpixel((int(SIZE[0] * .50), int(SIZE[1] * .47))) == 0, (
        "a hole worth 3% of the matte was treated as the item's face")


def test_a_matte_with_nothing_enclosed_is_still_returned_untouched():
    """The overwhelmingly common photo pays for none of this."""
    alpha = _matte(lambda d, size: d.rectangle(_box(size, OUTER), fill=255))
    assert images._reclaim_enclosed(_photo(_framed_picture), alpha) is alpha

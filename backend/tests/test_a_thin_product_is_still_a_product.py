"""A necklace is not a scattered painting, and the guard could not tell.

The shape guard asks two questions of a matte — is it one object, and does
what it kept fill the box around it — and the second was asked of everything
as a precondition on the lot. Run the real model over a chain laid out on a
sweep and it produces a flawless matte: one connected region, 100% of what
was kept, the clasp and the pendant included. It fills 0.20 of its bounding
box, the floor is 0.30, and the seller is told "the background remover found
no item in this photo".

Measured the same way: a belt laid diagonally 0.12, a guitar 0.20, a bangle
0.29. Chains, straps, cables, tools, instruments, hoops, hangers and anything
photographed at an angle are all that shape, and they are a large part of
what resells. The necklace was refused twice over, in fact — it covers 1.5%
of the frame against a coverage floor of 2%.

Neither refusal is what either measure was written for.

BOX FILL is about PIECES. Its own case is "a tree at one edge and a boat at
the other span nearly the whole photo while covering little of it", and every
fixture it exists to refuse — the brushstrokes, the tree and the boat, two
fragments in opposite corners — is two or more pieces. One connected object
cannot be spread across a frame. It can only be long, and long is a shape
products come in. So the precondition holds where it did the work, on a matte
that is not one object, and one object answers a much lower floor: it still
has to have substance, because the model handed a close-up of fabric traces a
wisp that fills 0.01 of its box.

COVERAGE is about finding NOTHING, and nothing does not score 1.5%. Measured
through the real model: an empty backdrop 0.0000, a close-up of fabric
0.0009, a speck of dust 0.0017 — an order of magnitude below the thinnest
real product. The floor was on the wrong side of that gap.

These tests draw the mattes themselves, so they run without rembg.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images  # noqa: E402

SIZE = (1200, 900)


def _matte(draw_fn, size=SIZE) -> Image.Image:
    a = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(a), size)
    return a


def _shape(draw_fn):
    kept = _matte(draw_fn).point(lambda a: 255 if a >= 128 else 0)
    return images._kept_shape(kept)


# --- the products that were refused ----------------------------------------

def _necklace(d, size):
    """A chain hanging in a curve, with a pendant at the bottom."""
    w, h = size
    d.arc([w * .28, h * .18, w * .72, h * .72], start=20, end=160,
          fill=255, width=18)
    d.ellipse([w * .48, h * .58, w * .52, h * .68], fill=255)


def _bangle(d, size):
    """A ring: the shape that fills its box least for its size."""
    w, h = size
    d.ellipse([w * .32, h * .16, w * .68, h * .84], outline=255, width=34)


def _belt(d, size):
    """Laid corner to corner, the way a long thing fits in a frame."""
    w, h = size
    d.line([(w * .12, h * .82), (w * .84, h * .20)], fill=255, width=40)


def _guitar(d, size):
    w, h = size
    d.line([(w * .28, h * .78), (w * .70, h * .20)], fill=255, width=22)
    d.ellipse([w * .16, h * .62, w * .40, h * .92], fill=255)


THIN = [("a necklace", _necklace), ("a bangle", _bangle),
        ("a belt laid diagonally", _belt), ("a guitar", _guitar)]


@pytest.mark.parametrize("name,draw", THIN, ids=[n for n, _ in THIN])
def test_a_thin_product_is_the_product(name, draw):
    """The report. One object, however little of its box it fills."""
    total, regions, box_fill = _shape(draw)
    assert regions[0][0] / total >= images._MIN_LARGEST_REGION, (
        f"{name}: the fixture is not one object, so it tests something else")
    assert images._kept_is_the_product(total, regions, box_fill), name


@pytest.mark.parametrize("name,draw", THIN, ids=[n for n, _ in THIN])
def test_these_are_exactly_the_mattes_the_old_floor_refused(name, draw):
    """Without this the fixtures could drift into compact shapes and the file
    would go on passing while testing nothing."""
    _total, _regions, box_fill = _shape(draw)
    assert box_fill < images._MIN_BBOX_FILL, (
        f"{name} fills {box_fill:.2f} of its box, so the old floor would have "
        "let it through and this fixture proves nothing")


def test_a_thin_product_covers_little_of_the_frame_and_is_still_an_item():
    """The necklace's second refusal. 1.5% of the frame is a chain, not an
    empty photo."""
    kept = _matte(_necklace).point(lambda a: 255 if a >= 128 else 0)
    coverage = sum(kept.histogram()[128:]) / (SIZE[0] * SIZE[1])
    assert coverage < 0.02, "the fixture no longer sits under the old floor"
    assert coverage >= images._MIN_FG_COVERAGE


# --- and what must still be refused ----------------------------------------

def test_a_wisp_traced_across_the_frame_is_not_an_object():
    """One region holding everything, spread over the whole photo at 1% of its
    box: what the model returns for a close-up of fabric. One object still has
    to be an object."""
    def wisp(d, size):
        # Corner to corner, a few pixels wide: the box around it is the whole
        # photo and almost none of that box is the trace. A strap laid on the
        # same diagonal is forty pixels wide and fills 0.12.
        w, h = size
        d.line([(w * .04, h * .06), (w * .96, h * .94)], fill=255, width=12)

    total, regions, box_fill = _shape(wisp)
    assert regions[0][0] / total >= images._MIN_LARGEST_REGION, "one region"
    assert box_fill < images._MIN_BBOX_FILL_ONE_OBJECT
    assert not images._kept_is_the_product(total, regions, box_fill)


def test_the_pieces_of_a_painting_are_still_refused_on_box_fill():
    """The precondition still stands where it was written to stand. These are
    two compact pieces in opposite corners — each fills its own box, so only
    the box around BOTH refuses them."""
    def far_apart(d, size):
        w, h = size
        d.rectangle([w * .04, h * .06, w * .20, h * .26], fill=255)
        d.rectangle([w * .80, h * .74, w * .96, h * .94], fill=255)

    total, regions, box_fill = _shape(far_apart)
    assert regions[0][0] / total < images._MIN_LARGEST_REGION, (
        "the fixture must not be one object, or it tests the other branch")
    assert all(fill >= images._MIN_BBOX_FILL for _c, fill in regions)
    assert not images._kept_is_the_product(total, regions, box_fill)


def test_a_pair_of_products_still_answers_the_stricter_floor():
    """Two things side by side fill the box they share, which is what lets a
    pair through where a tree and a boat do not. Nothing here moves that."""
    def two_canvases(d, size):
        w, h = size
        d.rectangle([w * .10, h * .20, w * .47, h * .82], fill=255)
        d.rectangle([w * .53, h * .20, w * .90, h * .82], fill=255)

    total, regions, box_fill = _shape(two_canvases)
    assert regions[0][0] / total < images._MIN_LARGEST_REGION
    assert box_fill >= images._MIN_BBOX_FILL
    assert images._kept_is_the_product(total, regions, box_fill)


def test_a_speck_is_still_nothing():
    """The coverage floor kept its job: what "found nothing" scores is three
    orders of magnitude below a product, not one."""
    def speck(d, size):
        w, h = size
        d.ellipse([w * .49, h * .48, w * .51, h * .52], fill=255)

    kept = _matte(speck).point(lambda a: 255 if a >= 128 else 0)
    coverage = sum(kept.histogram()[128:]) / (SIZE[0] * SIZE[1])
    assert coverage < images._MIN_FG_COVERAGE


def test_an_empty_matte_is_still_refused():
    total, regions, box_fill = images._kept_shape(Image.new("L", (64, 64), 0))
    assert not images._kept_is_the_product(total, regions, box_fill)

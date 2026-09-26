"""The baby is not the item. The painting is.

The report, with a screenshot: a Marcia Alpert gouache, "Baby in a Basket",
photographed front, back, signature and detail. The listing came back with the
BABY cut out of the painting — lifted off the teal water and the patchwork
quilt she painted it on, floating on white. Another photo kept the turtle and
the signature and deleted everything else. The seller's word was "horrifically
bad", and it is exactly that: the item they were selling had been erased from
the photos of it.

Every guard in services/images passed, and every one of them was right to. They
read the ALPHA and ask whether what survived looks like a product: one
connected piece, filling its own bounding box, solid through the middle. A baby
lifted out of a painting is all three — arithmetically indistinguishable from a
perfect cutout of a figurine. The fact that separates them is not in the matte
at all. It is that the item IS AN IMAGE, and a salient-object model handed a
picture answers the only question it knows: which part of this is the subject.
For a painting there is no honest answer to that question.

So a picture does not go to the model. It gets a geometric rule instead:

    find the outer outline, keep everything inside it, whole.

Two properties are what this file exists to hold down, and they are the two the
seller asked for in those words:

  * NOTHING INSIDE THE OUTLINE IS EVER REMOVED. Structural, not statistical:
    artwork.outline_matte fills the outline's corners solid, so there is no
    code path that could take a pixel out of the middle of a painting.
  * NO CLEAR OUTLINE MEANS DO NOTHING. Every doubt — a picture bleeding off
    the frame, a shape that is not a rectangle, something too small to be the
    piece — returns None and the photo is kept exactly as shot. None must
    never mean "fall back to the model", because the model is the bug.

Most tests here ask for the BOX around the outline (_border below) and check it
encloses the piece, because the direction that matters is the same for a box
as for four corners: wider keeps a strip of wall, narrower cuts the artwork.
The ones that check the matte that actually ships ask for the corners.

Pillow only, no rembg and no download: the outline is geometry, not inference.
"""
from __future__ import annotations

import io
import math
import random

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageChops, ImageDraw, ImageFilter  # noqa: E402

from backend.services import artwork  # noqa: E402

WALL = (238, 236, 232)
FRAME = (30, 30, 35)
SIZE = (1200, 900)


def _painted(d: ImageDraw.ImageDraw, box, step=22) -> None:
    """Something that looks like a painting: a colour field with a pattern
    over it, the way a gouache of water and a patchwork quilt does."""
    d.rectangle(box, fill=(90, 200, 190))
    for x in range(box[0], box[2], step):
        d.line([(x, box[1]), (x, box[3])], fill=(150, 120, 200), width=3)


def _framed(box=(300, 120, 900, 800), size=SIZE, wall=WALL, frame=FRAME):
    """A framed picture hanging on a wall — the ordinary case."""
    im = Image.new("RGB", size, wall)
    d = ImageDraw.Draw(im)
    d.rectangle(box, fill=frame)
    _painted(d, (box[0] + 18, box[1] + 18, box[2] - 18, box[3] - 18))
    return im


def _turn(pt, centre, deg):
    a = math.radians(deg)
    x, y = pt[0] - centre[0], pt[1] - centre[1]
    return (centre[0] + x * math.cos(a) - y * math.sin(a),
            centre[1] + x * math.sin(a) + y * math.cos(a))


def _hand_held(deg, box=(330, 150, 870, 770), mat=50, size=SIZE,
               sweep=(255, 255, 255), frame=FRAME):
    """The seller's photo: a framed picture, mounted behind a white mat, lying
    on a plain white sweep and shot by somebody holding the phone.

    Returns the photo and the four corners of the frame as it actually lies in
    it, which is what the border has to keep — not the upright rectangle it
    was drawn from.
    """
    im = Image.new("RGB", size, sweep)
    d = ImageDraw.Draw(im)
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def _corners(b):
        return [_turn(p, centre, deg) for p in
                ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]))]

    outer = _corners(box)
    d.polygon(outer, fill=frame)
    glazed = (box[0] + 18, box[1] + 18, box[2] - 18, box[3] - 18)
    d.polygon(_corners(glazed), fill=(252, 252, 250))       # the white mount
    art = (glazed[0] + mat, glazed[1] + mat,
           glazed[2] - mat, glazed[3] - mat)
    d.polygon(_corners(art), fill=(90, 200, 190))
    for x in range(art[0], art[2], 22):
        d.line([_turn((x, art[1]), centre, deg),
                _turn((x, art[3]), centre, deg)],
               fill=(150, 120, 200), width=3)
    return im, outer


def _bbox(corners):
    return (min(x for x, _ in corners), min(y for _, y in corners),
            max(x for x, _ in corners), max(y for _, y in corners))


def _border(im):
    """The box around every outline artwork found in `im`, or None."""
    found = artwork.outline(im)
    if found is None:
        return None
    return _bbox([p for quad in found for p in quad])


def _matte(im):
    """The matte that actually ships for `im` — or None when nothing would."""
    found = artwork.outline(im)
    return None if found is None else artwork.outline_matte(im.size, found)


def _encloses(found, truth) -> bool:
    """Whether `found` contains all of `truth`. The only direction that
    matters: a border wider than the piece keeps a strip of wall, a border
    narrower than it cuts the seller's artwork off."""
    return bool(found) and (found[0] <= truth[0] and found[1] <= truth[1]
                            and found[2] >= truth[2] and found[3] >= truth[3])


# ------------------------------------------- nothing inside is ever removed

def test_the_matte_for_a_picture_is_a_solid_outline():
    """The seller's requirement, held structurally. Whatever the outline turns
    out to be, everything inside it survives — there is no threshold here to
    get wrong and no shape to mis-measure."""
    box = (300, 120, 900, 800)
    m = artwork.outline_matte(SIZE, [((box[0], box[1]), (box[2], box[1]),
                                      (box[2], box[3]), (box[0], box[3]))])

    for x in range(box[0], box[2], 37):
        for y in range(box[1], box[3], 37):
            assert m.getpixel((x, y)) == 255, (x, y)
    # ...and the wall around it is gone.
    assert m.getpixel((10, 10)) == 0
    assert m.getpixel((1190, 890)) == 0


def test_an_outline_past_the_edge_of_the_photo_keeps_what_is_in_it():
    """Corners may land a little outside the photo — a piece shot with a
    sliver of wall is found with the rim of its own edge included. Clipped,
    not refused: the matte keeps everything inside the outline, so it keeps
    everything of the piece that is in the photo."""
    m = artwork.outline_matte(SIZE, [((-8, -5), (1210, -5), (1210, 700),
                                      (-8, 700))])

    assert m.getpixel((0, 0)) == 255 and m.getpixel((1199, 0)) == 255
    assert m.getpixel((600, 899)) == 0


def test_the_cut_photo_keeps_every_painted_pixel():
    """End to end through the composite: the painting that comes out is the
    painting that went in, pixel for pixel, with only the wall replaced."""
    im = _framed()
    out = _matte(im)
    inside = [(x, y) for x in range(320, 880, 40) for y in range(140, 780, 40)]
    assert all(out.getpixel(p) == 255 for p in inside)


def test_a_salient_subject_inside_the_picture_changes_nothing():
    """The reported photo in miniature: a big, compact, obviously-salient
    figure painted in the middle of the piece. This is what the model latched
    onto. The border does not look inside the picture at all, so a baby in a
    basket is exactly as irrelevant to it as an empty field of colour."""
    plain = _framed()
    with_baby = _framed()
    d = ImageDraw.Draw(with_baby)
    d.ellipse((480, 300, 760, 640), fill=(240, 215, 195))    # the baby
    d.ellipse((560, 360, 600, 400), fill=(60, 90, 160))      # an eye

    assert artwork.outline(plain) == artwork.outline(with_baby)


# ----------------------------------------------------- and it finds the edge

def test_a_framed_picture_on_a_wall():
    truth = (300, 120, 900, 800)
    assert _encloses(_border(_framed(truth)), truth)


def test_a_pale_picture_on_a_dark_wall():
    """The contrast runs the other way. Nothing here assumes the surround is
    lighter than the piece — it assumes only that the surround is whatever is
    at the edge of the photo."""
    im = Image.new("RGB", (900, 1200), (60, 58, 56))
    d = ImageDraw.Draw(im)
    truth = (150, 200, 760, 1000)
    d.rectangle(truth, fill=(245, 243, 240))
    _painted(d, (200, 260, 710, 940))

    assert _encloses(_border(im), truth)


def test_a_picture_photographed_at_an_angle():
    """A hand-held shot is never square to the frame. The border is the box
    around the whole tilted quadrilateral, so all four corners survive."""
    im = Image.new("RGB", SIZE, WALL)
    d = ImageDraw.Draw(im)
    d.polygon([(320, 140), (910, 180), (890, 790), (300, 750)], fill=FRAME)
    _painted(d, (360, 220, 860, 720))

    assert _encloses(_border(im), (300, 140, 910, 790))


# --- and it survives being hand-held -----------------------------------------
#
# The report: a framed picture, seven photos, every one of them whole in the
# frame on a plain white background, and the background removed from none of
# them. Nothing had gone wrong with the photos. A picture fills its own box
# because a picture is a rectangle -- but a TURNED rectangle fills its
# axis-aligned box badly (0.85 at 5 degrees, 0.74 at 10), and the test was
# asking whether the shape was an upright rectangle rather than a rectangle.
# One pair of hands tilts a whole set the same way, which is why the answer
# was seven out of seven rather than a photo here and there.


@pytest.mark.parametrize("deg", [0, 2, 4, 6, 8, 10, 12, 15, 20])
def test_a_framed_picture_shot_hand_held_is_still_a_picture(deg):
    """The reported set, one tilt at a time. A seller holding a phone is a few
    degrees off square on every shot, and none of those degrees makes the
    thing in front of them stop being a picture."""
    im, corners = _hand_held(deg)

    found = _border(im)
    assert found is not None, f"refused at {deg} degrees"
    assert _encloses(found, _bbox(corners)), (found, _bbox(corners))


@pytest.mark.parametrize("deg", [5, 10, 20])
def test_nothing_is_cut_off_the_corners_of_a_tilted_picture(deg):
    """The direction that matters, checked on the matte that actually ships.
    The outline follows the tilt, so there is no wedge of background left at
    each corner — and it must never clip a corner of the frame either, which
    is the one error here that destroys the thing being sold."""
    im, corners = _hand_held(deg)

    m = _matte(im)
    for x, y in corners:
        assert m.getpixel((round(x), round(y))) == 255, (deg, x, y)


@pytest.mark.parametrize("deg,taper", [(0, 0.08), (5, 0.08), (10, 0.06),
                                       (3, 0.14), (8, 0.12)])
def test_a_picture_shot_from_slightly_off_to_one_side(deg, taper):
    """Hand-held is not only turned, it is KEYSTONED: a phone held a little
    low or off to one side sees the far edge of the frame shorter than the
    near one, so the shape is a trapezium rather than a rectangle. It is still
    a picture, and fitting a rectangle to it still tells it apart from the
    shapes that are not one. `taper` is how much narrower the top edge comes
    out than the bottom."""
    im = Image.new("RGB", SIZE, (255, 255, 255))
    d = ImageDraw.Draw(im)
    box = (330, 150, 870, 770)
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def _shape(b):
        inset = (b[2] - b[0]) * taper / 2
        return [_turn(p, centre, deg) for p in
                ((b[0] + inset, b[1]), (b[2] - inset, b[1]),
                 (b[2], b[3]), (b[0], b[3]))]

    corners = _shape(box)
    d.polygon(corners, fill=FRAME)
    glazed = (box[0] + 18, box[1] + 18, box[2] - 18, box[3] - 18)
    d.polygon(_shape(glazed), fill=(252, 252, 250))
    d.polygon(_shape((glazed[0] + 50, glazed[1] + 50,
                      glazed[2] - 50, glazed[3] - 50)), fill=(90, 200, 190))

    found = _border(im)
    assert found is not None, f"refused at {deg} degrees, taper {taper}"
    assert _encloses(found, _bbox(corners)), (found, _bbox(corners))


def test_a_white_mounted_print_on_a_white_table():
    """The hard one, and the reason the scan runs inward from the edge of the
    photo instead of outward from the picture. The printed area stops eighty
    pixels short of the sheet's own edge, and that gap is blank paper on a
    table almost exactly its colour — there is nothing in it to grow along,
    so a scan that starts at the artwork can never cross it. Starting at the
    wall does: it stops at the sheet's shadow without ever standing on the
    mount."""
    im = Image.new("RGB", SIZE, (250, 249, 247))
    shadow = Image.new("L", SIZE, 0)
    ImageDraw.Draw(shadow).rectangle((256, 158, 958, 790), fill=90)
    im.paste(Image.new("RGB", SIZE, (120, 118, 116)), (0, 0),
             shadow.filter(ImageFilter.GaussianBlur(9)))
    d = ImageDraw.Draw(im)
    paper = (250, 150, 950, 780)
    d.rectangle(paper, fill=(252, 251, 249))
    _painted(d, (330, 240, 870, 690))

    assert _encloses(_border(im), paper)


# --- ...and survives being cropped square ------------------------------------
#
# The report straight after the one above, and the same shape of answer: art
# photos, square, and the background removed from almost none of them.
#
# A picture fills its own box -- but only the part of the picture that is IN
# THE MASK, and a pale mount is not. Wall and mount are both near white, so the
# mask is the moulding with a ring of nothing inside it and the artwork in the
# middle. A ring with a hole in it fills 0.71 of its box; an ellipse, which is
# never a picture, fills 0.785. The gate read the picture as the worse shape of
# the two.
#
# WHICH CROP that lands on is an accident of arithmetic. The working grid is
# 240 cells on the LONG side, so a square photo's short side is 240 cells where
# a 4:3 photo's is 180 -- the same piece, cropped square, arrives with its
# mount a third wider in cells, and past about eight cells the closing that
# used to bridge it no longer does. Nothing about the photo was wrong, and one
# crop shapes a whole set the same way, which is why the answer was almost all
# of them rather than a photo here and there.
#
# So the hole is filled before the shape is measured. Enclosed background only,
# which is what keeps every refusal below standing -- the last two tests here
# are that half of it.


@pytest.mark.parametrize("deg", [0, 1, 3, 4, 6, 7, 9, 12])
def test_a_matted_picture_cropped_square_is_still_a_picture(deg):
    """The reported set, one tilt at a time. A square crop is a fact about the
    photo; it is not a fact about the item in it."""
    im, corners = _hand_held(deg, box=(230, 230, 770, 770), size=(1000, 1000))

    found = _border(im)
    assert found is not None, f"refused a square photo at {deg} degrees"
    assert _encloses(found, _bbox(corners)), (found, _bbox(corners))


@pytest.mark.parametrize("size,box", [((1200, 900), (330, 150, 870, 770)),
                                      ((1000, 1000), (230, 230, 770, 770)),
                                      ((900, 1200), (180, 330, 720, 870))])
def test_the_shape_of_the_photo_does_not_decide_whether_it_is_art(size, box):
    """One piece, three crops, at the tilt a pair of hands actually produces.
    Whether a border is found has to be a question about the picture."""
    im, corners = _hand_held(4, box=box, size=size)

    found = _border(im)
    assert found is not None, f"refused a {size[0]}x{size[1]} photo"
    assert _encloses(found, _bbox(corners)), (found, _bbox(corners))


def test_only_the_background_a_shape_encloses_is_filled():
    """The half of the repair that keeps every refusal below standing. A gap
    with a way out to the frame edge is not a hole in anything — it is the
    space between two objects, and two objects spanning a box between them
    must never read as one picture."""
    enclosed = Image.new("L", (200, 160), 0)
    d = ImageDraw.Draw(enclosed)
    d.rectangle((20, 20, 180, 140), fill=255)
    d.rectangle((50, 50, 150, 110), fill=0)            # a mount: enclosed
    open_to_the_edge = enclosed.copy()
    ImageDraw.Draw(open_to_the_edge).rectangle((50, 50, 150, 200), fill=0)

    filled = artwork._solid(enclosed)
    assert all(filled.getpixel((x, y)) == 255
               for x in range(21, 180, 7) for y in range(21, 140, 7))
    # ...and the one with a way out is handed back exactly as it came in.
    assert artwork._solid(open_to_the_edge).tobytes() == \
        open_to_the_edge.tobytes()


def test_a_wreath_is_not_a_picture_just_because_its_middle_was_filled_in():
    """The shape filling a hole could have let through, and does not. A ring
    filled in is a disc, and a disc fills 0.785 of its box at every angle —
    the same score as the ellipse below, and nowhere near the bar. Filling a
    hole says nothing about the outer edge, which is the only thing that
    decides this."""
    im = Image.new("RGB", SIZE, WALL)
    d = ImageDraw.Draw(im)
    d.ellipse((250, 100, 950, 800), fill=(40, 110, 50))
    d.ellipse((390, 240, 810, 660), fill=WALL)

    assert _border(im) is None


# ------------------------------------------- ...and refuses when it cannot

def test_a_picture_that_bleeds_off_the_frame_is_left_alone():
    """The seller's own rule for this case, in their words: if there are no
    clear borders, assume it bleeds over and do nothing. Not "cut to the whole
    photo", which would composite the picture onto white and re-encode it to
    change nothing — None, so the photo is kept exactly as shot."""
    im = Image.new("RGB", SIZE, (90, 200, 190))
    _painted(ImageDraw.Draw(im), (0, 0, SIZE[0], SIZE[1]))

    assert _border(im) is None


def test_two_objects_spanning_a_rectangle_are_not_a_picture():
    """The shape that has to stay refused: a tree at one edge and a boat at
    the other span a box between them while covering little of it. A picture
    fills its own box, because a picture is a rectangle."""
    im = Image.new("RGB", SIZE, WALL)
    d = ImageDraw.Draw(im)
    d.ellipse((120, 200, 340, 700), fill=(40, 110, 50))
    d.rectangle((880, 520, 1080, 660), fill=(120, 70, 40))

    assert _border(im) is None


@pytest.mark.parametrize("deg", [0, 7, 15])
def test_two_objects_are_not_a_picture_at_any_angle_either(deg):
    """The shape above, turned. Fitting a rectangle at its best angle is a
    weaker question than fitting an upright one, so the refusals have to hold
    under rotation too — otherwise the hand-held case buys a picture back by
    letting everything else in with it."""
    im = Image.new("RGB", SIZE, WALL)
    d = ImageDraw.Draw(im)
    centre = (600, 450)
    d.ellipse((120, 200, 340, 700), fill=(40, 110, 50))
    d.rectangle((880, 520, 1080, 660), fill=(120, 70, 40))
    im = im.rotate(deg, resample=Image.BICUBIC, fillcolor=WALL, center=centre)

    assert _border(im) is None


def test_a_round_object_is_not_a_picture_at_any_angle():
    """A circle is the shape that proves the fit is doing something: it scores
    the same at every angle a rectangle would be rescued by, so a test that
    merely tried harder would let it through. It is never a picture."""
    im = Image.new("RGB", SIZE, WALL)
    ImageDraw.Draw(im).ellipse((250, 150, 950, 780), fill=(40, 110, 50))

    assert _border(im) is None


def test_a_figure_lifted_out_of_a_painting_is_not_a_picture():
    """The original bug's shape, asked of the border finder directly. This is
    what the model returns when it is handed a painting — the subject, cut
    free of the artwork around it. It is a perfectly good matte and it is not
    a rectangle at any angle."""
    im = Image.new("RGB", SIZE, WALL)
    d = ImageDraw.Draw(im)
    d.ellipse((430, 250, 780, 620), fill=(240, 215, 195))       # a head
    d.polygon([(400, 620), (810, 620), (880, 830), (330, 830)],
              fill=(70, 90, 150))                                # and shoulders

    assert _border(im) is None


def test_something_too_small_to_be_the_piece_is_refused():
    im = Image.new("RGB", SIZE, WALL)
    _painted(ImageDraw.Draw(im), (560, 420, 660, 500))

    assert _border(im) is None


def test_an_empty_wall_is_refused():
    assert _border(Image.new("RGB", SIZE, WALL)) is None


# --- ...and survives being photographed on something other than a flat wall --
#
# The report after the two above, and the same three words: the background was
# removed from almost none of them. Framed prints this time, shot on a cream
# sheet, and the answer had nothing to do with the pictures. Every one of them
# was found: a black frame on pale cloth is 0.89 of its own box and 0.42 of
# the photo, a textbook content mask. What happened to them happened afterwards.
#
# "Is this still the wall" was two fixed numbers measured against ONE median
# colour -- 6 levels of difference, 6 of texture -- and a sheet has folds in it.
# The first line in from the edge of the photo already cleared both, on all
# four sides, so the box grew to the whole photo and the bleed test threw the
# picture away for running off an edge it never reached.
#
# A plain sweep does it too, which is what makes this MOST of a set rather than
# the odd photo. A sweep is lit: 22 levels brighter at the top of the frame
# than at the bottom is an ordinary lamp against a bar of 6, and every row of a
# clean white background scores a hit share of 1.00 with no picture in it at
# all. One backdrop, one lamp, one afternoon -- and the seller's whole set
# comes back exactly as they shot it.
#
# The scan that stood here then learned to raise its bars to what the band did,
# which fixed these photos and not the next ones — see the section after them.
# Today neither a fold nor a lamp is measured against a colour at all: neither
# has an EDGE in it, so a flood from the edge of the photo crosses both for
# free and stops at the frame.

CLOTH = (198, 188, 166)


def _sheet(size=SIZE, seed=3, folds=14, depth=34, lamp=26):
    """A cream sheet: soft fold shading, and a lamp on one side of it.

    Nothing in here is an edge. The folds are blurred by a thirtieth of the
    frame, which is what a fold in cloth looks like and is why the content mask
    has no trouble with them — the fixed bars they defeat are the scan's.
    """
    rnd = random.Random(seed)
    w, h = size
    im = Image.new("RGB", size, CLOTH)
    d = ImageDraw.Draw(im)
    for _ in range(folds):
        x, y = rnd.randrange(-w // 4, w), rnd.randrange(-h // 4, h)
        k = rnd.randrange(-depth, depth)
        d.line([(x, y), (x + rnd.randrange(-w // 2, w // 2),
                         y + rnd.randrange(h // 3, h))],
               width=rnd.randrange(30, 120),
               fill=tuple(max(0, min(255, c + k)) for c in CLOTH))
    im = im.filter(ImageFilter.GaussianBlur(min(size) // 28))
    lit = Image.new("RGB", size)
    ld = ImageDraw.Draw(lit)
    for x in range(w):
        v = round(255 - lamp * 2 * abs(x / (w - 1) - 0.25))
        ld.line([(x, 0), (x, h)], fill=(v, v, v))
    return Image.blend(im, lit, 0.35)


def _sweep(size=SIZE, span=24):
    """A plain seamless sweep with a lamp above it: one flat colour, shaded top
    to bottom. There is no picture in the band and no texture anywhere."""
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for y in range(size[1]):
        v = round(255 - span * y / (size[1] - 1))
        d.line([(0, y), (size[0], y)], fill=(v, v, v))
    return im


def _on(bg, deg=5.0, box=(300, 150, 900, 780), frame=FRAME):
    """A framed picture lying on `bg`, held by hand."""
    im = bg.copy()
    d = ImageDraw.Draw(im)
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    corners = [_turn(pt, centre, deg) for pt in
               ((box[0], box[1]), (box[2], box[1]),
                (box[2], box[3]), (box[0], box[3]))]
    d.polygon(corners, fill=frame)
    glass = (box[0] + 18, box[1] + 18, box[2] - 18, box[3] - 18)
    d.polygon([_turn(pt, centre, deg) for pt in
               ((glass[0], glass[1]), (glass[2], glass[1]),
                (glass[2], glass[3]), (glass[0], glass[3]))], fill=(150, 148, 145))
    return im, corners


def _covers(found, size) -> float:
    """What share of the photo the border encloses."""
    return (found[2] - found[0]) * (found[3] - found[1]) / (size[0] * size[1])


def test_a_framed_picture_on_a_lit_sweep_is_still_a_picture():
    """The clearest form of the report, because there is nothing in this photo
    to be confused by: one flat colour, shaded by a lamp, and a frame in the
    middle of it. The gradient alone used to put every row of the background
    over the bar and take the box out to all four edges."""
    im, corners = _on(_sweep())

    found = _border(im)
    assert found is not None, "refused a framed picture on a plain lit sweep"
    assert _encloses(found, _bbox(corners)), (found, _bbox(corners))


@pytest.mark.parametrize("size,box", [((1200, 900), (330, 150, 870, 770)),
                                      ((1000, 1000), (230, 230, 770, 770)),
                                      ((900, 1200), (180, 330, 720, 870))])
@pytest.mark.parametrize("deg", [0, 3, 6])
def test_a_framed_picture_on_a_cloth_backdrop_is_still_a_picture(size, box, deg):
    """The reported set: one sheet, one pair of hands, every crop. What the
    backdrop is made of is not a fact about the item lying on it."""
    im, corners = _on(_sheet(size), deg=deg, box=box)

    found = _border(im)
    assert found is not None, f"refused a {size[0]}x{size[1]} photo at {deg}°"
    assert _encloses(found, _bbox(corners)), (found, _bbox(corners))


@pytest.mark.parametrize("bg", ["sheet", "sweep", "wall"])
def test_the_border_is_snug_around_the_piece_not_the_whole_photo(bg):
    """What the seller actually saw, and what "enclosing the piece" alone will
    not catch. The old scan did return a box for some of these — a box covering
    0.87 to 0.99 of the photo, which composites the sheet, the fold and the
    floor onto white along with the picture and calls it a cutout. The piece
    here is 0.30 of the frame; a border that keeps half the photo has not
    removed a background, whatever it reports."""
    im, corners = _on({"sheet": _sheet(), "sweep": _sweep(),
                       "wall": Image.new("RGB", SIZE, WALL)}[bg])

    found = _border(im)
    assert found is not None
    piece = _covers(_bbox(corners), SIZE)
    assert _covers(found, SIZE) <= piece * 1.5, (
        f"border covers {_covers(found, SIZE):.2f} of the photo for a piece "
        f"that is {piece:.2f} of it")


def test_a_picture_that_bleeds_off_a_textured_backdrop_is_still_left_alone():
    """A backdrop with folds in it gives the ladder of bars plenty to climb,
    and at every rung the thing left standing is the picture itself, running
    off the edge of the photo. The seller's rule for that has not changed."""
    im = _sheet()
    _painted(ImageDraw.Draw(im), (0, 0, SIZE[0], SIZE[1]))

    assert _border(im) is None


def _draw_ellipse(d):
    d.ellipse((250, 150, 950, 780), fill=(40, 110, 50))


def _draw_figure(d):
    d.ellipse((430, 250, 780, 620), fill=(240, 215, 195))       # a head
    d.polygon([(400, 620), (810, 620), (880, 830), (330, 830)],
              fill=(70, 90, 150))                                # and shoulders


def _draw_two_objects(d):
    d.ellipse((120, 200, 340, 700), fill=(40, 110, 50))         # a tree
    d.rectangle((880, 520, 1080, 660), fill=(120, 70, 40))      # and a boat


def _draw_wreath(d):
    d.ellipse((250, 150, 950, 780), fill=(120, 70, 40))
    d.ellipse((400, 290, 800, 640), fill=CLOTH)


def _draw_small(d):
    _painted(d, (560, 420, 660, 500))


@pytest.mark.parametrize("draw", [_draw_ellipse, _draw_figure, _draw_two_objects,
                                  _draw_wreath, _draw_small])
def test_the_refusals_hold_on_a_cloth_backdrop(draw):
    """The half worth watching. Every shape this module exists to refuse is
    refused on a flat wall, where the first rung of the ladder already has the
    background flooded. On the sheet the ladder has to climb past the folds
    first, and the higher the bar, the more of an object's weaker edges give
    way — a pale face before dark shoulders. Climbing until the background
    holds still must not be a way of buying an outline for something that is
    not a picture."""
    im = _sheet()
    draw(ImageDraw.Draw(im))

    assert _border(im) is None


def test_the_outline_sits_on_the_edge_and_shaves_no_side():
    """Where each side lands is settled from outside in, at twice the working
    size, so the outline is snug on every side rather than on the ones a scan
    happened to walk towards. A square frame centred in a square photo, so the
    answer is symmetry: what is left at the left is left at the right, and no
    side is inside the frame."""
    im = Image.new("RGB", (1200, 1200), (252, 251, 249))
    ImageDraw.Draw(im).rectangle((200, 200, 999, 999), fill=FRAME)

    left, top, right, bottom = _border(im)

    assert 180 <= left <= 200 and 180 <= top <= 200, (left, top)
    assert 1000 <= right <= 1020 and 1000 <= bottom <= 1020, (right, bottom)
    assert abs((200 - left) - (right - 1000)) <= 6
    assert abs((200 - top) - (bottom - 1000)) <= 6


@pytest.mark.parametrize("size", [(1, 1), (4, 4), (7, 200)])
def test_a_photo_too_small_to_reason_about_is_refused(size):
    """Never raises on a degenerate input — a photo must not fail to be listed
    because the border finder was handed something odd."""
    assert _border(Image.new("RGB", size, WALL)) is None


# --- ...and finds it in a room, not only on a sweep --------------------------
#
# The report: "the image remover still fails nearly every time for art pieces
# despite the item being fully in frame". Every fixture above is drawn in flat
# colour, and the scan that stood here passed them all; photos taken in a room
# it found about one time in forty. A room is lit by a lamp, so a wall is
# brighter at one end than the other; a phone darkens its own corners; a frame
# throws a shadow; a floor has planks; and a picture propped up to be
# photographed stands on the floor with the wall behind it. The scan measured
# every pixel against ONE background colour, so every one of those read as
# part of the picture, joined it to the frame, and the rectangle it was looking
# for stopped being one.
#
# These are drawn to have all of that at once — lamp, lens, shadow, sensor
# grain and a JPEG round trip — because it was never one of them that did it.


def _lamp(size, at=(0.15, 0.1), fall=0.35, vignette=0.25):
    """How a room lights a wall: brightest near a lamp at `at` (fractions of
    the frame), `fall` darker across the room, and a lens darkening its own
    corners. 0-255, smooth — drawn small and scaled up."""
    w, h = 64, 48
    lx, ly = at[0] * w, at[1] * h
    diag = math.hypot(w, h)
    light = Image.new("L", (w, h))
    light.putdata([round(255 * (1 - fall * math.hypot(x - lx, y - ly) / diag)
                         * (1 - vignette * (((x - w / 2) / (w / 2)) ** 2
                                            + ((y - h / 2) / (h / 2)) ** 2) / 2))
                   for y in range(h) for x in range(w)])
    return light.resize(size, Image.BICUBIC)


def _in_a_room(im, **lamp):
    """`im` lit by _lamp, with sensor grain, through a phone's JPEG."""
    im = ImageChops.multiply(im, Image.merge("RGB", [_lamp(im.size, **lamp)] * 3))
    grain = Image.effect_noise(im.size, 4)
    im = ImageChops.add(im, Image.merge("RGB", [grain] * 3), 1.0, -128)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def _planks(size=SIZE, seed=5):
    """A wood floor: boards of slightly different tones, dark seams, grain."""
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    x = 0
    while x < size[0]:
        wide = rnd.randrange(120, 190)
        tone = rnd.uniform(0.85, 1.15)
        board = tuple(round(c * tone) for c in (150, 105, 70))
        d.rectangle((x, 0, x + wide, size[1]), fill=board)
        for _ in range(6):
            gx = x + rnd.randrange(8, wide - 8)
            d.line([(gx, 0), (gx + rnd.randrange(-20, 20), size[1])],
                   fill=tuple(round(c * 0.9) for c in board), width=2)
        d.line([(x, 0), (x, size[1])], fill=(80, 55, 38), width=3)
        x += wide
    return im


def _outer(box, deg=0.0, taper=0.0):
    """A frame's outer corners: `box`, its top edge `taper` narrower (a phone
    held a little low), turned `deg` (held by hand)."""
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    inset = (box[2] - box[0]) * taper / 2
    return [_turn(p, centre, deg) for p in
            ((box[0] + inset, box[1]), (box[2] - inset, box[1]),
             (box[2], box[3]), (box[0], box[3]))]


def _hung(bg, corners, frame=FRAME, mount=None, shadow=(14, 18)):
    """A framed picture with these outer corners over `bg`, throwing a soft
    shadow down and to the right."""
    im = bg.copy()
    if shadow:
        cast = Image.new("L", im.size, 0)
        ImageDraw.Draw(cast).polygon(
            [(x + shadow[0], y + shadow[1]) for x, y in corners], fill=120)
        im.paste(Image.new("RGB", im.size, (40, 40, 40)), (0, 0),
                 cast.filter(ImageFilter.GaussianBlur(12)))
    d = ImageDraw.Draw(im)
    d.polygon(corners, fill=frame)
    cx = sum(x for x, _ in corners) / 4
    cy = sum(y for _, y in corners) / 4

    def _in(k):
        return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in corners]

    if mount:
        d.polygon(_in(0.93), fill=mount)
    art = _in(0.78 if mount else 0.92)
    d.polygon(art, fill=(90, 200, 190))
    for k in range(12):
        f = k / 11
        d.line([(art[0][0] + (art[1][0] - art[0][0]) * f,
                 art[0][1] + (art[1][1] - art[0][1]) * f),
                (art[3][0] + (art[2][0] - art[3][0]) * f,
                 art[3][1] + (art[2][1] - art[3][1]) * f)],
               fill=(150, 120, 200), width=4)
    return im


def _kept(im, *pieces):
    """(share of the pieces the matte keeps, matte area against theirs)."""
    matte = _matte(im)
    assert matte is not None, "no outline found — the photo was kept as shot"
    truth = Image.new("L", im.size, 0)
    for corners in pieces:
        ImageDraw.Draw(truth).polygon(corners, fill=255)
    both = ImageChops.multiply(truth, matte).histogram()[255]
    area = truth.histogram()[255]
    return both / area, matte.histogram()[255] / area


PAINTED_WALL = (214, 208, 196)
MOUNT = (245, 243, 236)


@pytest.mark.parametrize("frame,mount", [(FRAME, None),
                                         ((176, 140, 70), MOUNT),
                                         ((236, 234, 230), MOUNT)],
                         ids=["black", "gold-mounted", "white-mounted"])
@pytest.mark.parametrize("deg,taper", [(0, 0), (4, 0.06), (-6, 0.1)])
def test_a_framed_picture_on_a_lamp_lit_wall(frame, mount, deg, taper):
    """The report itself. A painted wall a lamp lights unevenly, a phone
    that darkens its own corners, a frame's shadow — and the piece squarely
    in the middle of the photo. All of it kept, and the wall gone: an outline
    that took a quarter of the wall with it would be the old failure again,
    wearing a different face."""
    corners = _outer((320, 140, 880, 760), deg, taper)
    im = _in_a_room(_hung(Image.new("RGB", SIZE, PAINTED_WALL), corners,
                          frame, mount))

    kept, spread = _kept(im, corners)

    assert kept >= 0.99, f"the outline cut {1 - kept:.1%} of the piece off"
    assert spread <= 1.25, f"the outline kept {spread - 1:.0%} more than it"


def test_a_picture_leaning_on_the_skirting_board():
    """How a picture is usually photographed to be sold: propped on the floor
    against the wall. Two backgrounds, a skirting board running across the
    photo under the frame, and the frame's shadow falling across all three.
    The old scan took one of the two as "the background" and the other as
    part of the picture."""
    room = Image.new("RGB", SIZE, PAINTED_WALL)
    room.paste(_planks().crop((0, 0, SIZE[0], 170)), (0, 730))
    ImageDraw.Draw(room).rectangle((0, 715, SIZE[0], 730), fill=(240, 238, 234))
    corners = _outer((380, 170, 820, 720), 1.5, 0.04)
    im = _in_a_room(_hung(room, corners, (120, 80, 45), MOUNT), at=(0.8, 0.0))

    kept, spread = _kept(im, corners)

    assert kept >= 0.99
    assert spread <= 1.25


def test_a_canvas_lying_on_a_plank_floor():
    """No frame at all, and a floor that is nothing BUT edges: seams, grain,
    boards of different tones. None of them reaches all the way round the
    canvas, so none of them can pass for its outline."""
    corners = _outer((300, 160, 900, 740), -3, 0.12)
    im = _in_a_room(_hung(_planks(), corners, (90, 200, 190), shadow=(8, 10)),
                    at=(0.5, -0.3), fall=0.3)

    kept, spread = _kept(im, corners)

    assert kept >= 0.99
    assert spread <= 1.25


def test_two_pictures_side_by_side_are_both_kept():
    """A pair photographed together is a pair for sale. The scan took the
    larger of the two and cut the other one away with the wall."""
    left = _outer((120, 220, 540, 700), 2)
    right = _outer((660, 260, 1060, 660), -2)
    wall = Image.new("RGB", SIZE, PAINTED_WALL)
    im = _in_a_room(_hung(_hung(wall, left, FRAME, MOUNT), right,
                          (120, 80, 45)))

    kept, _ = _kept(im, left, right)

    assert len(artwork.outline(im)) == 2
    assert kept >= 0.99


def test_a_frame_that_melts_into_the_wall_keeps_its_frame():
    """A frame within a few levels of the wall it hangs on. The flood gets
    into the moulding through the stretch where the two meet, and the first
    clean shape it finds is the white mount inside — a perfect rectangle, and
    the frame cut off round it. The frame's outer edge still runs parallel
    to that rectangle, a moulding's width outside it on every side, and that
    is what the outline grows back out to."""
    corners = _outer((320, 140, 880, 760), 3)
    im = _in_a_room(_hung(Image.new("RGB", SIZE, PAINTED_WALL), corners,
                          (205, 199, 188), (250, 249, 246)), at=(0.5, -0.2))

    kept, _ = _kept(im, corners)

    # The mount alone is 0.86 of the frame.
    assert kept >= 0.97, "the frame was cut off to the mount inside it"


def test_a_four_cornered_shape_no_photo_of_a_rectangle_makes_is_refused():
    """Four corners are not enough. A subject lifted out of a painting can
    have four corners — a figure with a diagonal back, a sail — and it is
    still not a picture, because no photograph of a rectangle, however it was
    held, shows one side at half the length of the side opposite it."""
    im = Image.new("RGB", SIZE, WALL)
    ImageDraw.Draw(im).polygon([(300, 380), (900, 150), (900, 760), (300, 760)],
                               fill=(90, 60, 40))

    assert artwork.outline(im) is None


@pytest.mark.parametrize("side", ["top", "left", "corner"])
def test_a_frame_running_off_the_photo_is_never_cut_to_what_is_inside_it(side):
    """The case every guard here exists for, on a lit wall: the frame runs
    off one edge (or two) of the photo. The flood reaches its moulding from
    that edge and stops at the mount, which is a clean rectangle. Cutting to
    it would crop the frame. Either nothing happens, or what is kept is
    everything of the piece that is in the photo."""
    box = {"top": (300, -120, 900, 700), "left": (-150, 150, 700, 760),
           "corner": (-150, -120, 700, 700)}[side]
    corners = _outer(box)
    im = _in_a_room(_hung(Image.new("RGB", SIZE, PAINTED_WALL), corners,
                          (120, 80, 45), MOUNT))

    found = artwork.outline(im)
    if found is not None:
        visible = Image.new("L", SIZE, 0)
        ImageDraw.Draw(visible).polygon(corners, fill=255)
        matte = artwork.outline_matte(SIZE, found)
        both = ImageChops.multiply(visible, matte).histogram()[255]
        assert both >= 0.99 * visible.histogram()[255], \
            "the frame was cropped to the mount inside it"


# --------------------------------------------- and the model is never asked
#
# The border rule above is only worth having if a picture actually reaches it.
# These drive the pass the uploads take, with the model stood in for by a
# recorder — the question each one asks is not "was the cutout good" but "was
# the model consulted at all", because on a picture its answer is confidently
# wrong and no guard downstream can see that.

def _photo(dirpath, name, im=None) -> "object":
    (im or _framed()).save(dirpath / name, "JPEG", quality=92)
    return dirpath / name


class _Recorder:
    """Stands in for the salient-object model, and remembers being asked."""

    def __init__(self):
        self.asked = []

    def __call__(self, img, wait=None):
        self.asked.append(img.size)
        return Image.new("RGB", img.size, (255, 255, 255))


def test_a_picture_never_reaches_the_model(tmp_path, monkeypatch):
    """The fix, stated. Not "the model's answer is checked more carefully" —
    it is not asked, because there is no version of "which part of this
    painting is the subject" with an honest answer."""
    from backend.services import images

    model = _Recorder()
    monkeypatch.setattr(images, "cutout", model)
    src = _photo(tmp_path, "src_000.jpg")

    out = images.optimize(src, tmp_path / "img_000.jpg", remove_bg=True,
                          art=True)

    assert model.asked == []
    assert out["background_removed"] is True
    assert not out.get("bg_error")


def test_a_picture_with_no_findable_border_is_kept_as_shot(tmp_path,
                                                           monkeypatch):
    """And when the border cannot be found, the model still is not asked.
    None from the art path means "do nothing" — never "fall back", which
    would hand the painting to the very thing that eats it."""
    from backend.services import images

    model = _Recorder()
    monkeypatch.setattr(images, "cutout", model)
    bleeding = Image.new("RGB", SIZE, (90, 200, 190))
    _painted(ImageDraw.Draw(bleeding), (0, 0, SIZE[0], SIZE[1]))
    src = _photo(tmp_path, "src_000.jpg", bleeding)

    out = images.optimize(src, tmp_path / "img_000.jpg", remove_bg=True,
                          art=True)

    assert model.asked == []
    assert out["background_removed"] is False
    assert out["bg_error"] == images.ART_NO_BORDER_KEPT_AS_SHOT
    # ...and the photo on disk is the photo that was taken.
    with Image.open(tmp_path / "img_000.jpg") as saved:
        assert saved.getpixel((10, 10)) != (255, 255, 255)


def test_the_reported_photo_comes_back_cut_out(tmp_path, monkeypatch):
    """End to end, the photo the report was about: a framed piece, whole in
    the frame, on a lamp-lit wall, through the pass the uploads take. It used
    to come back exactly as shot, with a sentence saying the picture's edge
    was not in the frame."""
    from backend.services import images

    model = _Recorder()
    monkeypatch.setattr(images, "cutout", model)
    monkeypatch.setattr(images, "_mask", model)
    corners = _outer((320, 140, 880, 760), 4, 0.06)
    im = _in_a_room(_hung(Image.new("RGB", SIZE, PAINTED_WALL), corners,
                          (176, 140, 70), MOUNT))
    src = _photo(tmp_path, "src_000.jpg", im)

    out = images.optimize(src, tmp_path / "img_000.jpg", remove_bg=True,
                          art=True)

    assert out["background_removed"] is True
    assert not out.get("bg_error")
    assert model.asked == [], "neither model was needed to find the frame"
    with Image.open(tmp_path / "img_000.jpg") as saved:
        assert saved.getpixel((5, 5)) == images.WHITE, "the wall is gone"
        assert saved.getpixel((600, 450)) != images.WHITE, "the picture is not"


def test_everything_that_is_not_a_picture_still_goes_to_the_model(tmp_path,
                                                                  monkeypatch):
    """The feature is not weakened for the photos it was built for."""
    from backend.services import images

    model = _Recorder()
    monkeypatch.setattr(images, "cutout", model)
    src = _photo(tmp_path, "src_000.jpg")

    out = images.optimize(src, tmp_path / "img_000.jpg", remove_bg=True)

    assert len(model.asked) == 1
    assert out["background_removed"] is True


def test_a_picture_costs_nothing_when_the_seller_never_asked(tmp_path,
                                                             monkeypatch):
    """Cutouts off: a picture is just a photo. Nothing to skip, nothing to
    say about it, nothing to refund."""
    from backend.services import images

    monkeypatch.setattr(images, "cutout", _Recorder())
    out = images.optimize(_photo(tmp_path, "src_000.jpg"),
                          tmp_path / "img_000.jpg", remove_bg=False, art=True)

    assert not out.get("bg_error")
    assert out["background_removed"] is False


def test_the_batch_routes_pictures_away_from_the_model(tmp_path, monkeypatch):
    """End to end through the pass the uploads actually take: four photos,
    one screen answer, and only the two that are not pictures reach the
    model."""
    from backend.services import images

    model = _Recorder()
    monkeypatch.setattr(images, "cutout", model)
    names = [f"src_{i:03d}.jpg" for i in range(4)]
    srcs = [_photo(tmp_path, n) for n in names]
    monkeypatch.setattr(
        images, "_screen_for",
        lambda sources, should_stop=None: (
            {}, frozenset(), frozenset({"src_001.jpg", "src_002.jpg"})))

    results = images.optimize_batch(
        [(s, tmp_path / f"img_{i:03d}.jpg") for i, s in enumerate(srcs)],
        remove_bg=True)

    assert len(model.asked) == 2, "only the non-pictures may be asked about"
    assert all(r["background_removed"] for r in results), \
        "the pictures still get a cutout — to their own border"


def test_a_close_up_still_wins_over_a_picture(tmp_path, monkeypatch):
    """A zoomed shot of a signature in the corner of a canvas is both a
    picture and a detail. Detail wins: there is no border in the frame to
    find, so the honest answer is to leave the photo alone rather than to go
    looking for a rectangle that is not there."""
    from backend.services import images

    model = _Recorder()
    monkeypatch.setattr(images, "cutout", model)
    src = _photo(tmp_path, "src_000.jpg")

    out = images.optimize(src, tmp_path / "img_000.jpg", remove_bg=True,
                          detail=True, art=True)

    assert model.asked == []
    assert out["bg_error"] == images.DETAIL_KEPT_AS_SHOT

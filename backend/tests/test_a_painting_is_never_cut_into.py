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

    find the outer border, keep everything inside it, whole.

Two properties are what this file exists to hold down, and they are the two the
seller asked for in those words:

  * NOTHING INSIDE THE BORDER IS EVER REMOVED. Structural, not statistical:
    artwork.mask returns a filled rectangle, so there is no code path that
    could take a pixel out of the middle of a painting.
  * NO CLEAR BORDER MEANS DO NOTHING. Every doubt — a picture bleeding off the
    frame, a shape that is not a rectangle, something too small to be the
    piece — returns None and the photo is kept exactly as shot. None must
    never mean "fall back to the model", because the model is the bug.

Pillow only, no rembg and no download: the border is geometry, not inference.
"""
from __future__ import annotations

import math

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

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


def _encloses(found, truth) -> bool:
    """Whether `found` contains all of `truth`. The only direction that
    matters: a border wider than the piece keeps a strip of wall, a border
    narrower than it cuts the seller's artwork off."""
    return bool(found) and (found[0] <= truth[0] and found[1] <= truth[1]
                            and found[2] >= truth[2] and found[3] >= truth[3])


# ------------------------------------------- nothing inside is ever removed

def test_the_matte_for_a_picture_is_a_solid_rectangle():
    """The seller's requirement, held structurally. Whatever the border turns
    out to be, everything inside it survives — there is no threshold here to
    get wrong and no shape to mis-measure."""
    box = (300, 120, 900, 800)
    m = artwork.mask(SIZE, box)

    for x in range(box[0], box[2], 37):
        for y in range(box[1], box[3], 37):
            assert m.getpixel((x, y)) == 255, (x, y)
    # ...and the wall around it is gone.
    assert m.getpixel((10, 10)) == 0
    assert m.getpixel((1190, 890)) == 0


def test_the_cut_photo_keeps_every_painted_pixel():
    """End to end through the composite: the painting that comes out is the
    painting that went in, pixel for pixel, with only the wall replaced."""
    im = _framed()
    out = artwork.mask(im.size, artwork.border(im))
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

    assert artwork.border(plain) == artwork.border(with_baby)


# ----------------------------------------------------- and it finds the edge

def test_a_framed_picture_on_a_wall():
    truth = (300, 120, 900, 800)
    assert _encloses(artwork.border(_framed(truth)), truth)


def test_a_pale_picture_on_a_dark_wall():
    """The contrast runs the other way. Nothing here assumes the surround is
    lighter than the piece — it assumes only that the surround is whatever is
    at the edge of the photo."""
    im = Image.new("RGB", (900, 1200), (60, 58, 56))
    d = ImageDraw.Draw(im)
    truth = (150, 200, 760, 1000)
    d.rectangle(truth, fill=(245, 243, 240))
    _painted(d, (200, 260, 710, 940))

    assert _encloses(artwork.border(im), truth)


def test_a_picture_photographed_at_an_angle():
    """A hand-held shot is never square to the frame. The border is the box
    around the whole tilted quadrilateral, so all four corners survive."""
    im = Image.new("RGB", SIZE, WALL)
    d = ImageDraw.Draw(im)
    d.polygon([(320, 140), (910, 180), (890, 790), (300, 750)], fill=FRAME)
    _painted(d, (360, 220, 860, 720))

    assert _encloses(artwork.border(im), (300, 140, 910, 790))


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

    found = artwork.border(im)
    assert found is not None, f"refused at {deg} degrees"
    assert _encloses(found, _bbox(corners)), (found, _bbox(corners))


@pytest.mark.parametrize("deg", [5, 10, 20])
def test_nothing_is_cut_off_the_corners_of_a_tilted_picture(deg):
    """The direction that matters, checked on the matte that actually ships.
    The box around a tilted picture keeps a wedge of background at each
    corner — on a white sweep it is white on white, and on any other surface
    it is a slightly worse cutout of an INTACT item. What it must never do is
    clip a corner of the frame, which is the one error here that destroys the
    thing being sold."""
    im, corners = _hand_held(deg)

    m = artwork.mask(im.size, artwork.border(im))
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

    found = artwork.border(im)
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

    assert _encloses(artwork.border(im), paper)


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

    found = artwork.border(im)
    assert found is not None, f"refused a square photo at {deg} degrees"
    assert _encloses(found, _bbox(corners)), (found, _bbox(corners))


@pytest.mark.parametrize("size,box", [((1200, 900), (330, 150, 870, 770)),
                                      ((1000, 1000), (230, 230, 770, 770)),
                                      ((900, 1200), (180, 330, 720, 870))])
def test_the_shape_of_the_photo_does_not_decide_whether_it_is_art(size, box):
    """One piece, three crops, at the tilt a pair of hands actually produces.
    Whether a border is found has to be a question about the picture."""
    im, corners = _hand_held(4, box=box, size=size)

    found = artwork.border(im)
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

    assert artwork.border(im) is None


# ------------------------------------------- ...and refuses when it cannot

def test_a_picture_that_bleeds_off_the_frame_is_left_alone():
    """The seller's own rule for this case, in their words: if there are no
    clear borders, assume it bleeds over and do nothing. Not "cut to the whole
    photo", which would composite the picture onto white and re-encode it to
    change nothing — None, so the photo is kept exactly as shot."""
    im = Image.new("RGB", SIZE, (90, 200, 190))
    _painted(ImageDraw.Draw(im), (0, 0, SIZE[0], SIZE[1]))

    assert artwork.border(im) is None


def test_two_objects_spanning_a_rectangle_are_not_a_picture():
    """The shape that has to stay refused: a tree at one edge and a boat at
    the other span a box between them while covering little of it. A picture
    fills its own box, because a picture is a rectangle."""
    im = Image.new("RGB", SIZE, WALL)
    d = ImageDraw.Draw(im)
    d.ellipse((120, 200, 340, 700), fill=(40, 110, 50))
    d.rectangle((880, 520, 1080, 660), fill=(120, 70, 40))

    assert artwork.border(im) is None


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

    assert artwork.border(im) is None


def test_a_round_object_is_not_a_picture_at_any_angle():
    """A circle is the shape that proves the fit is doing something: it scores
    the same at every angle a rectangle would be rescued by, so a test that
    merely tried harder would let it through. It is never a picture."""
    im = Image.new("RGB", SIZE, WALL)
    ImageDraw.Draw(im).ellipse((250, 150, 950, 780), fill=(40, 110, 50))

    assert artwork.border(im) is None


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

    assert artwork.border(im) is None


def test_something_too_small_to_be_the_piece_is_refused():
    im = Image.new("RGB", SIZE, WALL)
    _painted(ImageDraw.Draw(im), (560, 420, 660, 500))

    assert artwork.border(im) is None


def test_an_empty_wall_is_refused():
    assert artwork.border(Image.new("RGB", SIZE, WALL)) is None


@pytest.mark.parametrize("size", [(1, 1), (4, 4), (7, 200)])
def test_a_photo_too_small_to_reason_about_is_refused(size):
    """Never raises on a degenerate input — a photo must not fail to be listed
    because the border finder was handed something odd."""
    assert artwork.border(Image.new("RGB", size, WALL)) is None


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

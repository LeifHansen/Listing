"""A cutout that keeps the wrong fifth of the photo is not a cutout.

The report, with a screenshot: five photos of one framed watercolour. The two
wide shots came out right. The three close-ups came back as smears of
brushwork and a tree floating on white — the frame, the mount and most of the
painting gone. Nothing failed, nothing was logged, and the grid said Complete.

Every one of those three passed the only check there was. `_MIN_FG_COVERAGE`
asks HOW MUCH of the frame survived and refuses a matte that kept almost
nothing, because shipping that would ship a white square. A fifth of the
frame did survive here. It was a fifth made of the wrong pixels, and coverage
cannot tell the difference — which is the whole bug: the guard measured
quantity and the failure was about shape.

It is also not a strange edge case. A salient-object model handed a
photograph OF A PICTURE finds the subject the picture depicts — the tree, the
boat — and deletes the artwork around it, exactly as built. Art, prints,
posters, book covers, trading cards, patterned fabric and printed packaging
are the same trap, and they are a large share of what people resell.

So the matte is now asked whether it is THE PRODUCT, by measures that fail
the two shapes this produces:

  * what was kept must fill its own bounding box — against a tree at one edge
    and a boat at the other, which together span the photo while covering
    little of it. This one is a precondition for everything below.
  * and it must be one object — the largest connected region most of what was
    kept — OR a few compact ones. That second half was added after "one
    product is one object" turned out to be false for a great deal of what
    resells: a seller photographing a PAIR of shoes, two paintings, earrings
    or a boxed set got a perfect matte thrown away for having two pieces in
    it. See _COMPANION_SHARE in services/images. The box-fill precondition is
    what keeps the trap above refused while a pair gets through.

Both are deliberately generous, because the error directions are not equal. A
cutout wrongly refused leaves the photo EXACTLY AS SHOT and says why, costing
a feature that was opt-in anyway. A cutout wrongly shipped destroys the photo
and says nothing. These tests pin both directions: the healthy mattes below
must survive with margin, and the reported ones must not.

The model is stood in for by a matte the test draws itself, so this runs
without rembg or a download.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images  # noqa: E402

SIZE = (1200, 900)


def _matte(draw_fn, size=SIZE) -> Image.Image:
    m = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(m))
    return m


# --- what a good matte of a real product looks like -------------------------

def _framed_picture(d):
    """The whole framed piece — what the two wide shots correctly produced."""
    d.rectangle([120, 90, 1080, 810], fill=255)


def _mug(d):
    d.ellipse([400, 250, 800, 650], fill=255)


def _shirt(d):
    """Body plus two sleeves: one object, but not a tidy rectangle."""
    d.rectangle([400, 200, 800, 700], fill=255)
    d.rectangle([250, 220, 400, 380], fill=255)
    d.rectangle([800, 220, 950, 380], fill=255)


def _shoe(d):
    d.ellipse([200, 380, 1000, 620], fill=255)


# --- and the three that were shipped ----------------------------------------

def _brushstrokes(d):
    """The close-up of the painting's texture: a dozen disconnected smears."""
    for i, x in enumerate(range(120, 1080, 90)):
        d.ellipse([x, 250 + (i % 4) * 90, x + 60, 300 + (i % 4) * 90], fill=255)


def _tree_and_boat(d):
    """What the model found INSIDE the painting: two things, far apart."""
    d.polygon([(180, 700), (260, 250), (340, 700)], fill=255)
    d.ellipse([760, 520, 1020, 640], fill=255)


def _tree_boat_and_sketch(d):
    d.polygon([(150, 720), (240, 220), (330, 720)], fill=255)
    d.ellipse([700, 500, 980, 630], fill=255)
    d.rectangle([520, 380, 640, 470], fill=255)


GOOD = [("a framed picture", _framed_picture), ("a mug", _mug),
        ("a shirt with sleeves", _shirt), ("a shoe", _shoe)]
SHIPPED = [("scattered brushstrokes", _brushstrokes),
           ("a tree and a boat", _tree_and_boat),
           ("a tree, a boat and a sketch", _tree_boat_and_sketch)]


# --- the measure ------------------------------------------------------------

@pytest.mark.parametrize("name,draw", GOOD, ids=[n for n, _ in GOOD])
def test_one_object_reads_as_one_object(name, draw):
    largest, box_fill = images._shape_stats(_matte(draw))
    assert largest >= images._MIN_LARGEST_REGION, name
    assert box_fill >= images._MIN_BBOX_FILL, name


@pytest.mark.parametrize("name,draw", SHIPPED, ids=[n for n, _ in SHIPPED])
def test_the_pieces_of_a_painting_do_not(name, draw):
    largest, box_fill = images._shape_stats(_matte(draw))
    assert (largest < images._MIN_LARGEST_REGION
            or box_fill < images._MIN_BBOX_FILL), name


def test_an_empty_matte_answers_rather_than_dividing_by_zero():
    assert images._shape_stats(Image.new("L", (64, 64), 0)) == (0.0, 0.0)


def test_the_measure_does_not_depend_on_the_photo_being_large():
    """Shape is judged on a downscaled copy, so a 4000px photo and a 400px one
    must reach the same verdict about the same silhouette."""
    for size in ((400, 300), (1200, 900), (4000, 3000)):
        largest, fill = images._shape_stats(_matte(_framed_picture, size))
        assert largest >= images._MIN_LARGEST_REGION
        assert fill >= images._MIN_BBOX_FILL


# --- and what cutout() does with it -----------------------------------------

@pytest.fixture()
def model(monkeypatch):
    """Stand in for the model: `model.matte = fn` chooses what it returns."""
    state = {"draw": _framed_picture}
    monkeypatch.setattr(
        images, "_mask",
        lambda rgb, wait=None: _matte(state["draw"], rgb.size))

    class _Model:
        @staticmethod
        def says(draw_fn):
            state["draw"] = draw_fn
    return _Model


def _photo(size=SIZE) -> Image.Image:
    return Image.new("RGB", size, (200, 190, 170))


@pytest.mark.parametrize("name,draw", GOOD, ids=[n for n, _ in GOOD])
def test_a_real_item_is_still_cut_out(model, name, draw):
    """The guard must not cost the feature: every healthy matte still ships."""
    model.says(draw)
    assert images.cutout(_photo()) is not None, name


@pytest.mark.parametrize("name,draw", SHIPPED, ids=[n for n, _ in SHIPPED])
def test_the_photo_is_kept_as_shot_instead_of_wrecked(model, name, draw):
    """None is how cutout() says "keep the photo as shot" — the seller's own
    photo, untouched, with a sentence saying why."""
    model.says(draw)
    assert images.cutout(_photo()) is None, name


def test_the_studio_says_why_rather_than_silently_doing_nothing(model):
    model.says(_brushstrokes)
    with pytest.raises(ValueError):
        images.remove_background_white(_photo())


def test_the_batch_keeps_the_photo_and_reports_it(model, tmp_path):
    """optimize() must still write the photo — as shot — and say the cutout
    did not happen, so the caller can hand the charge back."""
    src = tmp_path / "src.jpg"
    _photo().save(src, "JPEG", quality=92)
    model.says(_tree_and_boat)

    out = images.optimize(src, tmp_path / "out.jpg", remove_bg=True)

    assert out["background_removed"] is False
    assert out.get("bg_error")
    assert (tmp_path / "out.jpg").is_file()


def test_a_guard_that_refuses_everything_would_be_caught_here(model):
    """The pair above only means something together: this is the test that
    fails if someone tightens a threshold until nothing is ever cut out."""
    model.says(_framed_picture)
    assert images.cutout(_photo()) is not None


# --- one product is not always one object -----------------------------------
#
# The report: two canvas paintings lying side by side on grass, and background
# removal doing nothing at all. Run through the real model that photo mattes
# perfectly -- both canvases kept as clean rectangles, the figures painted on
# them correctly ignored -- and scored coverage 0.47, solidity 1.00, box fill
# 0.87. It was refused on largest-region 0.51 against a floor of 0.60, for no
# reason except that the seller was selling two things.

def _two_canvases(d, size=SIZE):
    """The reported photo: two rectangular canvases side by side, a sliver of
    background between them."""
    w, h = size
    d.polygon([(w*.10, h*.22), (w*.46, h*.17), (w*.49, h*.80), (w*.13, h*.86)],
              fill=255)
    d.polygon([(w*.53, h*.16), (w*.90, h*.19), (w*.88, h*.82), (w*.51, h*.79)],
              fill=255)


def _pair_of_shoes(d, size=SIZE):
    w, h = size
    d.ellipse([w*.10, h*.38, w*.46, h*.64], fill=255)
    d.polygon([(w*.16, h*.44), (w*.30, h*.24), (w*.40, h*.30), (w*.40, h*.48)],
              fill=255)
    d.ellipse([w*.54, h*.38, w*.90, h*.64], fill=255)
    d.polygon([(w*.60, h*.44), (w*.74, h*.24), (w*.84, h*.30), (w*.84, h*.48)],
              fill=255)


def _a_jug_and_a_bowl(d, size=SIZE):
    """Two pieces of different shapes and sizes rather than a matched pair —
    a jug beside its bowl, a phone beside its box, a lamp beside its shade."""
    w, h = size
    d.ellipse([w*.14, h*.28, w*.56, h*.74], fill=255)
    d.ellipse([w*.58, h*.30, w*.99, h*.68], fill=255)


PAIRS = [("two canvases", _two_canvases),
         ("a pair of shoes", _pair_of_shoes),
         ("a jug and a bowl", _a_jug_and_a_bowl)]


@pytest.mark.parametrize("name,draw", PAIRS, ids=[n for n, _ in PAIRS])
def test_a_pair_of_products_is_still_the_product(name, draw):
    kept = _matte(draw).point(lambda a: 255 if a >= 128 else 0)
    total, regions, box_fill = images._kept_shape(kept)
    assert regions[0][0] / total < images._MIN_LARGEST_REGION, (
        f"{name}: fixture no longer exercises the multi-object path")
    assert images._kept_is_the_product(total, regions, box_fill), name


@pytest.mark.parametrize("name,draw", SHIPPED, ids=[n for n, _ in SHIPPED])
def test_the_pieces_of_a_painting_are_still_refused(name, draw):
    """The whole point of letting a pair through is that it must not let THESE
    through. Each is two or three pieces too, and each is refused."""
    kept = _matte(draw).point(lambda a: 255 if a >= 128 else 0)
    total, regions, box_fill = images._kept_shape(kept)
    assert not images._kept_is_the_product(total, regions, box_fill), name


def test_a_tidy_arrangement_of_too_many_pieces_is_refused():
    """A seller sells a pair or a small set. A dozen compact fragments filling
    the frame neatly is a matte that fell apart, not eleven products."""
    def many(d, size=SIZE):
        w, h = size
        for row in range(3):
            for col in range(4):
                x, y = w*(.10 + col*.21), h*(.16 + row*.26)
                d.rectangle([x, y, x + w*.15, y + h*.19], fill=255)

    kept = _matte(many).point(lambda a: 255 if a >= 128 else 0)
    total, regions, box_fill = images._kept_shape(kept)
    assert box_fill >= images._MIN_BBOX_FILL, "the arrangement is tidy"
    assert len(regions) > images._MAX_OBJECTS
    assert not images._kept_is_the_product(total, regions, box_fill)


def test_a_pair_that_does_not_fill_its_shared_box_is_refused():
    """Two compact pieces in opposite corners are the tree-and-boat shape,
    whatever each one looks like on its own."""
    def far_apart(d, size=SIZE):
        w, h = size
        d.rectangle([w*.04, h*.06, w*.20, h*.26], fill=255)
        d.rectangle([w*.80, h*.74, w*.96, h*.94], fill=255)

    kept = _matte(far_apart).point(lambda a: 255 if a >= 128 else 0)
    total, regions, box_fill = images._kept_shape(kept)
    assert all(fill >= images._MIN_BBOX_FILL for _c, fill in regions), (
        "each piece is compact on its own — the union is what refuses it")
    assert not images._kept_is_the_product(total, regions, box_fill)


def test_the_regions_are_reported_largest_first():
    kept = _matte(_a_jug_and_a_bowl).point(lambda a: 255 if a >= 128 else 0)
    _total, regions, _fill = images._kept_shape(kept)
    assert [r[0] for r in regions] == sorted([r[0] for r in regions], reverse=True)


def test_an_empty_matte_has_no_regions():
    assert images._kept_shape(Image.new("L", (64, 64), 0)) == (0, [], 0.0)
    assert not images._kept_is_the_product(0, [], 0.0)

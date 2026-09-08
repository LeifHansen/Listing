"""An item rubbed out to a third of itself is not a cutout either.

The report, with a screenshot: a grid of clothing, cutouts on. The maroon
shirt came out right. The white oxford and the cream fleece came back as pale
smears dissolving into the backdrop — the collar label, the placket and a
printed logo still solid, and the fabric around them a ghost. Nothing failed,
nothing was logged, and the grid said Complete.

The correlation between the one that survived and the two that did not is the
whole diagnosis, and it is not about shape. The model is confident where there
is contrast and unsure where there is not, so a PALE ITEM ON A PALE BACKDROP —
a white shirt on white foamboard, which is what sellers are told to shoot on —
mattes its high-contrast details at 255 and its fabric somewhere in the middle
of the range. Maroon on white has contrast everywhere and mattes solid.

Both existing guards read a BINARISED matte, "the pixels at least half
opaque". Neither can see this, because on a ghost they are looking at the
details:

  * coverage is comfortable — the placket and the label are a few percent of
    the frame, well over the floor;
  * the shape measures are excellent — those details really are one connected
    blob that really does fill its own bounding box.

Every one of the three below passes all three. What ships is the matte used as
an ALPHA CHANNEL, and that is the thing nothing was asking about: a pixel the
model was half sure about is not kept or dropped, it is composited at half
strength onto white.

So the matte is asked whether the item is opaque where it is not an edge.
Softness at the boundary is a good matte doing its job, so the measure erodes
the boundary off first and reads the interior — which is why a fur collar, a
wig and an openwork lace panel below still ship, and the ghosts do not.

The model is stood in for by a matte the test draws itself, so this runs
without rembg or a download.
"""
from __future__ import annotations

import math
import random

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from backend.services import images  # noqa: E402

SIZE = (1200, 900)


def _matte(draw_fn, size=SIZE) -> Image.Image:
    m = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(m), size)
    return m


# --- mattes that are soft for a good reason ---------------------------------
#
# All three are soft at the edge and opaque in the body, which is what a matte
# of a real object looks like. They are here because a guard that cannot tell
# them from a ghost would cost the feature on half the clothing on the site.

def _shirt(d, size):
    """The plain healthy case: solid, with the rim a real matte would have."""
    w, h = size
    d.rectangle([w * .33, h * .22, w * .67, h * .78], fill=255)
    d.rectangle([w * .21, h * .24, w * .33, h * .42], fill=255)
    d.rectangle([w * .67, h * .24, w * .79, h * .42], fill=255)


def _fur_collar(d, size):
    """A fluffy coat: opaque body, a wide ragged fringe of translucent tufts
    OUTSIDE it. The fringe is most of what a solidity measure would see if it
    looked at the whole matte, and none of the interior."""
    w, h = size
    cx, cy, rx, ry = w * .5, h * .5, w * .21, h * .26
    rnd = random.Random(7)
    for _ in range(2500):
        a = rnd.uniform(0, math.tau)
        out = rnd.uniform(1.0, 1.18)
        x, y = cx + math.cos(a) * rx * out, cy + math.sin(a) * ry * out
        r = rnd.uniform(w * .007, w * .038)
        d.ellipse([x - r, y - r, x + r, y + r], fill=rnd.randint(70, 200))
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)  # body, last


def _wig(d, size):
    """Flyaway strands over the crown — faint, and every one of them at the
    boundary."""
    w, h = size
    rnd = random.Random(3)
    for _ in range(900):
        x0, y0 = rnd.uniform(w * .32, w * .68), rnd.uniform(h * .22, h * .29)
        d.line([x0, y0, x0 + rnd.uniform(-w * .05, w * .05),
                y0 - rnd.uniform(h * .02, h * .13)],
               fill=rnd.randint(60, 190), width=3)
    d.ellipse([w * .32, h * .27, w * .68, h * .78], fill=255)


def _lace_panel(d, size):
    """Openwork: opaque threads and a great many holes. Soft all over the
    frame, and solid everywhere it is actually there."""
    w, h = size
    d.rectangle([w * .32, h * .26, w * .68, h * .74], fill=255)
    step, hole = w * .033, w * .018
    y = h * .28
    while y < h * .72:
        x = w * .33
        while x < w * .67:
            d.ellipse([x, y, x + hole, y + hole], fill=0)
            x += step
        y += step


# --- and the three that shipped ---------------------------------------------

def _white_oxford(d, size):
    """The reported tile: sure about the label, the placket and the buttons,
    unsure about every inch of white poplin between them."""
    w, h = size
    d.rectangle([w * .28, h * .17, w * .72, h * .87], fill=110)
    d.rectangle([w * .15, h * .20, w * .28, h * .47], fill=105)
    d.rectangle([w * .72, h * .20, w * .85, h * .47], fill=105)
    d.rectangle([w * .47, h * .20, w * .53, h * .84], fill=255)   # placket
    d.rectangle([w * .43, h * .17, w * .57, h * .26], fill=255)   # collar band
    for i in range(5):                                            # buttons
        y = h * .29 + i * h * .1
        d.ellipse([w * .487, y, w * .512, y + h * .033], fill=255)


def _cream_fleece(d, size):
    """The other one: a pale fleece where only the printed logo held."""
    w, h = size
    d.ellipse([w * .21, h * .20, w * .79, h * .84], fill=95)
    d.rectangle([w * .40, h * .43, w * .66, h * .53], fill=255)


def _never_committed(d, size):
    """No confident pixel anywhere — the model hedged on the whole frame."""
    w, h = size
    d.ellipse([w * .25, h * .22, w * .75, h * .78], fill=150)


SOFT = [("a shirt with a soft rim", _shirt, 3),
        ("a fur collar", _fur_collar, 5),
        ("a wig", _wig, 4),
        ("a lace panel", _lace_panel, 2)]
GHOSTS = [("a white oxford on white", _white_oxford, 0),
          ("a cream fleece", _cream_fleece, 3),
          ("a matte that never committed", _never_committed, 0)]


def _blurred(draw, blur, size=SIZE) -> Image.Image:
    m = _matte(draw, size)
    return m.filter(ImageFilter.GaussianBlur(blur)) if blur else m


# --- the measure ------------------------------------------------------------

@pytest.mark.parametrize("name,draw,blur", SOFT, ids=[n for n, _, _ in SOFT])
def test_soft_at_the_edge_reads_as_solid(name, draw, blur):
    """Every one of these is soft — that is why they are here. What makes
    them cutouts is that the softness stops at the boundary."""
    solidity = images._interior_solidity(images._harden(_blurred(draw, blur)))
    assert solidity >= images._MIN_INTERIOR_SOLIDITY, f"{name}: {solidity:.2f}"


@pytest.mark.parametrize("name,draw,blur", GHOSTS, ids=[n for n, _, _ in GHOSTS])
def test_see_through_in_the_middle_does_not(name, draw, blur):
    solidity = images._interior_solidity(images._harden(_blurred(draw, blur)))
    assert solidity < images._MIN_INTERIOR_SOLIDITY, f"{name}: {solidity:.2f}"


def test_the_guards_already_there_would_have_passed_every_ghost():
    """The reason this file exists. If coverage and shape caught these, the
    new measure would be dead weight — and if someone later widens one of
    them into doing this job, this test says the ghost is covered twice
    rather than leaving it silently uncovered."""
    for name, draw, blur in GHOSTS:
        alpha = images._harden(_blurred(draw, blur))
        kept = alpha.point(lambda a: 255 if a >= 128 else 0)
        coverage = sum(kept.histogram()[128:]) / (SIZE[0] * SIZE[1])
        largest, box_fill = images._shape_stats(kept)
        assert coverage >= images._MIN_FG_COVERAGE, name
        assert largest >= images._MIN_LARGEST_REGION, name
        assert box_fill >= images._MIN_BBOX_FILL, name


def test_an_item_too_thin_to_have_an_interior_is_not_called_a_ghost():
    """A chain, a wire hanger, a filigree earring: erosion leaves nothing, so
    there is no interior to be see-through and this measure must abstain
    rather than refuse the photo."""
    m = Image.new("L", SIZE, 0)
    d = ImageDraw.Draw(m)
    d.line([200, 700, 600, 200, 1000, 700], fill=255, width=5)
    assert images._interior_solidity(images._harden(m)) == 1.0


def test_the_measure_does_not_depend_on_the_photo_being_large():
    """It is read off a downscaled copy, so a 4000px photo and a 400px one
    must reach the same verdict about the same matte."""
    for size in ((400, 300), (1200, 900), (4000, 3000)):
        assert images._interior_solidity(
            images._harden(_blurred(_shirt, 3, size))
        ) >= images._MIN_INTERIOR_SOLIDITY, size
        assert images._interior_solidity(
            images._harden(_blurred(_white_oxford, 0, size))
        ) < images._MIN_INTERIOR_SOLIDITY, size


# --- and what cutout() does with it -----------------------------------------

@pytest.fixture()
def model(monkeypatch):
    """Stand in for the model: `model.says(fn, blur)` chooses the matte."""
    state = {"draw": _shirt, "blur": 3}
    monkeypatch.setattr(
        images, "_mask",
        lambda rgb, wait=None: _blurred(state["draw"], state["blur"], rgb.size))

    class _Model:
        @staticmethod
        def says(draw_fn, blur=0):
            state["draw"], state["blur"] = draw_fn, blur
    return _Model


def _photo(size=SIZE) -> Image.Image:
    """A pale garment on a pale backdrop — the shot this is all about."""
    return Image.new("RGB", size, (238, 236, 232))


@pytest.mark.parametrize("name,draw,blur", SOFT, ids=[n for n, _, _ in SOFT])
def test_a_real_item_is_still_cut_out(model, name, draw, blur):
    """The guard must not cost the feature: fur, hair and lace still ship."""
    model.says(draw, blur)
    assert images.cutout(_photo()) is not None, name


@pytest.mark.parametrize("name,draw,blur", GHOSTS, ids=[n for n, _, _ in GHOSTS])
def test_the_photo_is_kept_as_shot_instead_of_erased(model, name, draw, blur):
    model.says(draw, blur)
    assert images.cutout(_photo()) is None, name


def test_what_ships_is_the_item_at_full_strength(model):
    """The positive form of the same claim, read off the pixels rather than
    the matte: where the item is, the cutout is the item — not a blend of it
    and the white it was pasted onto."""
    model.says(_shirt, 3)
    photo = Image.new("RGB", SIZE, (120, 40, 60))       # the maroon shirt
    out = images.cutout(photo)
    assert out is not None
    assert out.getpixel((SIZE[0] // 2, SIZE[1] // 2)) == (120, 40, 60)


def test_the_studio_says_why_rather_than_silently_wrecking_the_photo(model):
    model.says(_white_oxford)
    with pytest.raises(ValueError, match="pale item on a pale backdrop"):
        images.remove_background_white(_photo())


def test_the_batch_keeps_the_photo_and_reports_it(model, tmp_path):
    """optimize() must still write the photo — as shot — and say the cutout
    did not happen, so the caller can hand the charge back."""
    src = tmp_path / "src.jpg"
    _photo().save(src, "JPEG", quality=92)
    model.says(_cream_fleece, 3)

    out = images.optimize(src, tmp_path / "out.jpg", remove_bg=True)

    assert out["background_removed"] is False
    assert out.get("bg_error")
    assert (tmp_path / "out.jpg").is_file()


def test_a_guard_that_refuses_everything_would_be_caught_here(model):
    """The pair above only means something together: this is the test that
    fails if someone raises the floor until nothing is ever cut out."""
    model.says(_shirt, 3)
    assert images.cutout(_photo()) is not None

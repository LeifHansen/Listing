"""The dark-surround guard: does it still catch a mangled cutout, and does it
finally leave a good one alone?

The bug these exist for: the guard asked "is the region we removed large and
dark?" and threw the cutout away when it was, on the premise that real
backdrops are white or neutral. Sellers who shoot on a dark floor, slate, a
black sweep or asphalt fail that test with a PERFECT cutout — so background
removal quietly did nothing for them, on every photo, with no reason given.

So the cases here are built as pairs. Same darkness, same geometry, opposite
right answers — which is exactly what brightness alone cannot separate and
what the boundary evidence can.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from backend.services import images, matte  # noqa: E402

SIDE = 384


def _surface(mean, sd, seed=0):
    """A dark surface with grain — concrete, slate, worn asphalt."""
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(mean, sd, (SIDE, SIDE, 3)), 0, 255)


def _centred(frac):
    """(slice, slice) for a centred square covering `frac` of the frame."""
    side = int(SIDE * np.sqrt(frac))
    start = (SIDE - side) // 2
    return slice(start, start + side), slice(start, start + side)


def _judge(img, mask, dark_guard=True):
    rgb = Image.fromarray(img.astype(np.uint8), "RGB")
    alpha = Image.fromarray(mask, "L")
    result = matte.CutoutResult()
    kept = images._subject_survived(alpha, rgb, result, dark_guard=dark_guard)
    return kept, result


def _item_on_dark_surface(item_colour=(205, 150, 90), floor=45, grain=38,
                          frac=0.30):
    """A real product photographed on a dark surface: two materials, a real
    silhouette between them, and a matte that found it exactly."""
    img = _surface(floor, grain, seed=3)
    ys, xs = _centred(frac)
    img[ys, xs] = np.asarray(item_colour, dtype=float)
    mask = np.zeros((SIDE, SIDE), np.uint8)
    mask[ys, xs] = 255
    return img, mask


def _cut_across_one_material(frac=0.25):
    """The failure the guard is really for: the matte carved a shape out of
    ONE continuous dark material, so there is no edge beneath its outline."""
    img = _surface(42, 20, seed=5)
    ys, xs = _centred(frac)
    mask = np.zeros((SIDE, SIDE), np.uint8)
    mask[ys, xs] = 255
    return img, mask


# --- the false positive that made removal look dead -------------------------
@pytest.mark.parametrize("floor", [30, 45, 60])
@pytest.mark.parametrize("frac", [0.12, 0.25, 0.45])
def test_a_perfect_cutout_survives_a_dark_surface(floor, frac):
    """Ground truth in, ground truth out. Whatever the surface is made of, a
    matte that traced the item exactly must never be thrown away — this is
    the regression that made the engine look broken."""
    kept, result = _judge(*_item_on_dark_surface(floor=floor, frac=frac))
    assert kept, (f"a perfect cutout was discarded on a floor of luma {floor} "
                  f"({result.error_code}): {result.metrics}")
    assert result.error_code == ""


def test_the_surface_being_dark_is_not_by_itself_a_complaint():
    """A clean cutout on a dark surface goes out clean — no warning, nothing
    for the seller to read, because nothing went wrong."""
    _, result = _judge(*_item_on_dark_surface(frac=0.30))
    assert result.warnings == []


def test_a_dark_item_on_a_dark_surface_still_survives():
    """The hardest good case: low contrast between item and surface. The
    silhouette is faint but it is real, so the cutout stands."""
    kept, result = _judge(*_item_on_dark_surface(item_colour=(96, 88, 84),
                                                 floor=40, grain=12))
    assert kept, f"low-contrast but real edge was discarded: {result.metrics}"


# --- the failure it exists to catch -----------------------------------------
def test_a_cut_across_one_material_is_still_refused():
    """No edge under the outline means the model invented it — keep the
    seller's original photo rather than a shape cut out of the floor."""
    kept, result = _judge(*_cut_across_one_material())
    assert not kept
    assert result.error_code == matte.ERR_DARK_SUBJECT
    assert result.status == matte.KEPT_ORIGINAL


def test_the_refusal_says_why():
    """Whatever the wording, a refusal has to arrive with a reason attached —
    a silent revert is the thing that left sellers guessing."""
    _, result = _judge(*_cut_across_one_material())
    assert result.warnings, "the cutout was refused without telling anyone why"


def test_a_survivor_the_size_of_a_printed_graphic_is_flagged():
    """A dark garment reduced to its chest print keeps a real edge — the
    print's outline — so evidence alone cannot refuse it, and nothing local
    can. It ships flagged instead of unremarked, for the seller to judge."""
    img = _surface(38, 12, seed=9)          # black fabric, edge to edge
    ys, xs = _centred(0.11)
    img[ys, xs] = np.asarray([235, 235, 225], dtype=float)   # the print
    mask = np.zeros((SIDE, SIDE), np.uint8)
    mask[ys, xs] = 255                       # ...and only the print survives
    kept, result = _judge(img, mask)
    assert kept
    assert result.warnings, "a graphic-sized survivor went out unremarked"


# --- the guard's own contract -----------------------------------------------
def test_the_guard_is_off_in_the_photo_studio():
    """The studio shows the seller the cutout and offers Revert, so it must
    not hard-fail behind their back."""
    kept, _ = _judge(*_cut_across_one_material(), dark_guard=False)
    assert kept


def test_an_erased_subject_is_still_refused_on_any_surface():
    """The coverage floor is independent of all of this and stays put."""
    img = _surface(45, 38, seed=1)
    mask = np.zeros((SIDE, SIDE), np.uint8)
    mask[:6, :6] = 255
    kept, result = _judge(img, mask)
    assert not kept
    assert result.error_code == matte.ERR_SUBJECT_ERASED


def _support(img, mask):
    return images._boundary_support(Image.fromarray(mask, "L"),
                                    Image.fromarray(img.astype(np.uint8), "RGB"))


def test_boundary_evidence_is_scale_free():
    """A grainy surface and a smooth one are judged on one scale: the
    silhouette is measured against the grain of the very region being
    removed, so texture raises both sides of the comparison rather than
    tipping it."""
    smooth = _support(*_item_on_dark_surface(floor=45, grain=4))
    grainy = _support(*_item_on_dark_surface(floor=45, grain=40))
    assert smooth["supported"], smooth
    assert grainy["supported"], grainy


def test_an_invented_outline_scores_far_below_a_real_one():
    """The two ends of the measurement, so a threshold change has to face
    what it is choosing between: a real silhouette clears the bar with room
    to spare, an invented one sits at the surface's own noise floor."""
    real = _support(*_item_on_dark_surface())
    invented = _support(*_cut_across_one_material())
    assert real["edge_ratio"] > matte.MIN_EDGE_RATIO
    assert invented["edge_ratio"] < matte.MIN_EDGE_RATIO
    assert invented["colour_gap"] < matte.MIN_COLOUR_GAP < real["colour_gap"]

# --- surfaces whose own texture is structured -------------------------------
def _dark_wood(seed=4):
    """The awkward surface: plank lines survive the blur that flattens sensor
    noise, so the surface's own grain inflates the baseline the silhouette is
    measured against. This is where an edge test alone gets it wrong."""
    rng = np.random.default_rng(seed)
    rows = np.arange(SIDE)[:, None]
    grain = 26 * np.sin(rows / 3.1) + 18 * np.sin(rows / 1.7 + 1.2)
    img = np.zeros((SIDE, SIDE, 3))
    for channel, tint in enumerate((52, 38, 28)):
        img[..., channel] = tint + grain + rng.normal(0, 6, (SIDE, SIDE))
    return np.clip(img, 0, 255)


def _on_wood(item_colour, frac=0.28):
    img = _dark_wood()
    ys, xs = _centred(frac)
    img[ys, xs] = np.asarray(item_colour, dtype=float)
    mask = np.zeros((SIDE, SIDE), np.uint8)
    mask[ys, xs] = 255
    return img, mask


@pytest.mark.parametrize("item_colour", [(205, 150, 90), (140, 140, 138)])
def test_plank_grain_does_not_cost_a_good_cutout(item_colour):
    """Measuring the silhouette against the surface's OWN grain is what keeps
    a grainy floor from reading as a missing edge."""
    kept, result = _judge(*_on_wood(item_colour))
    assert kept, f"wood grain sank a good cutout: {result.metrics}"


def test_either_signal_alone_is_enough_to_keep_a_cutout():
    """A dark item on dark wood: the grain is busier than the silhouette, so
    the edge ratio alone would refuse it — colour keeps it. Requiring BOTH
    signals to fail before refusing is what makes that recoverable, and this
    is the case that proves the two are not redundant."""
    support = _support(*_on_wood((74, 66, 60)))
    assert support["edge_ratio"] < matte.MIN_EDGE_RATIO, support
    assert support["colour_gap"] >= matte.MIN_COLOUR_GAP, support
    assert support["supported"], support

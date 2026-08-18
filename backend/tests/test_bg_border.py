"""Border solidification for background removal.

The failure these cover is the one sellers see first: the cutout's OUTLINE is
wrong. A matte comes back ragged — bites out of a hem, a fringe of the old
backdrop still attached, crumbs of nothing floating beside the item — and
compositing that straight onto white saws visible chunks out of the product.

_solidify_border closes the outline, drops the crumbs, and then snaps the
border onto the item's real edge using the photo itself. These tests feed
hand-built mattes straight to the refine pass, so no model runs and the
numbers are deterministic. Skipped where Pillow/numpy aren't installed (CI's
lint+unit job deliberately skips the heavy image stack).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from backend.services import images  # noqa: E402

SIZE = 600
BACKDROP = (250, 250, 250)
ITEM = (150, 168, 190)      # a light-wash denim blue — the low-contrast case
ITEM_BOX = (130, 90, 470, 520)
RADIUS = 30


def _rng(seed: int = 5):
    return np.random.default_rng(seed)


def _truth(box=ITEM_BOX) -> Image.Image:
    """The matte the item actually deserves."""
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=RADIUS, fill=255)
    return mask


def _photo(item=ITEM, box=ITEM_BOX, backdrop=BACKDROP, seed=5) -> Image.Image:
    """That item photographed on a sweep, with a little sensor noise so the
    colour tests can't rely on perfectly flat regions."""
    img = Image.new("RGB", (SIZE, SIZE), backdrop)
    ImageDraw.Draw(img).rounded_rectangle(box, radius=RADIUS, fill=item)
    arr = np.asarray(img).astype(np.int16) + _rng(seed).integers(-8, 9, (SIZE, SIZE, 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _bits(mask: Image.Image):
    return np.asarray(mask) >= 128


def _iou(mask: Image.Image, truth: Image.Image) -> float:
    a, t = _bits(mask), _bits(truth)
    return float((a & t).sum() / (a | t).sum())


def _missed(mask: Image.Image, truth: Image.Image) -> float:
    """Share of the item the mask fails to keep."""
    a, t = _bits(mask), _bits(truth)
    return float((~a & t).sum() / t.sum())


def _spill(mask: Image.Image, truth: Image.Image) -> float:
    """Share of the backdrop the mask wrongly keeps."""
    a, t = _bits(mask), _bits(truth)
    return float((a & ~t).sum() / (~t).sum())


def _chewed(truth: Image.Image, bites: int = 14, erode: int = 7,
            seed: int = 5) -> Image.Image:
    """A realistically bad matte: pulled in off the item's edge, with chunks
    bitten out of it."""
    rng, inside = _rng(seed), _bits(truth)
    mask = truth.filter(ImageFilter.MinFilter(erode))
    draw = ImageDraw.Draw(mask)
    for _ in range(bites):
        x, y = rng.integers(ITEM_BOX[0], ITEM_BOX[2]), rng.integers(ITEM_BOX[1], ITEM_BOX[3])
        if inside[y, x]:
            draw.ellipse((x - 16, y - 16, x + 16, y + 16), fill=0)
    return mask


def _refined(mask, photo=None):
    return images._refine_alpha(mask, source=photo)


def test_a_chewed_matte_gets_the_items_border_back():
    truth, photo = _truth(), _photo()
    chewed = _chewed(truth)
    before, after = _refined(chewed), _refined(chewed, photo)
    assert _missed(chewed, truth) > 0.08       # the matte really is bad
    assert _missed(after, truth) < 0.03        # and comes back nearly whole
    assert _iou(after, truth) > _iou(before, truth) + 0.08
    assert _spill(after, truth) < 0.01         # without grabbing the backdrop


def test_the_border_stops_at_the_items_edge_rather_than_running_on():
    """Growth is gated on the photo, so it can't spread across the backdrop."""
    truth, photo = _truth(), _photo()
    out = _refined(_chewed(truth), photo)
    grown_past = _bits(out) & ~_bits(truth.filter(ImageFilter.MaxFilter(9)))
    assert grown_past.sum() < 0.001 * grown_past.size


def test_leftover_backdrop_around_the_item_is_trimmed_off():
    """The opposite failure: a matte that overshoots, leaving a halo of the
    background it was supposed to remove."""
    truth, photo = _truth(), _photo()
    fringed = truth.filter(ImageFilter.MaxFilter(11))
    assert _spill(fringed, truth) > 0.02
    assert _spill(_refined(fringed, photo), truth) < 0.01


def test_floating_crumbs_are_dropped_and_the_item_is_not():
    truth, photo = _truth(), _photo()
    speckled, rng = truth.copy(), _rng(11)
    draw = ImageDraw.Draw(speckled)
    for _ in range(40):
        x, y = rng.integers(0, SIZE), rng.integers(0, SIZE)
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=255)
    out = _refined(speckled, photo)
    assert _spill(out, truth) < 0.005
    assert _missed(out, truth) < 0.02


def test_two_items_both_survive_and_the_gap_between_them_stays_open():
    """A pair of shoes is two blobs, not a speck and a subject — and the space
    between them is backdrop, which the closing pass must not bridge."""
    left, right = (60, 200, 240, 420), (360, 200, 540, 420)
    photo = Image.new("RGB", (SIZE, SIZE), BACKDROP)
    truth = Image.new("L", (SIZE, SIZE), 0)
    for box in (left, right):
        ImageDraw.Draw(photo).rounded_rectangle(box, radius=25, fill=(60, 70, 90))
        ImageDraw.Draw(truth).rounded_rectangle(box, radius=25, fill=255)
    out = _bits(_refined(truth.filter(ImageFilter.MinFilter(5)), photo))
    assert out[310, 100:200].all() and out[310, 400:500].all()  # both kept
    assert not out[310, 290:310].any()                         # gap still open


def test_a_see_through_gap_is_not_filled_in():
    """The hole in a doughnut (or a mug handle) shows the backdrop, so it has
    to stay a hole."""
    photo = Image.new("RGB", (SIZE, SIZE), BACKDROP)
    truth = Image.new("L", (SIZE, SIZE), 0)
    for img, colour, hole in ((photo, (190, 90, 70), BACKDROP), (truth, 255, 0)):
        ImageDraw.Draw(img).ellipse((150, 150, 450, 450), fill=colour)
        ImageDraw.Draw(img).ellipse((240, 240, 360, 360), fill=hole)
    out = _refined(truth, photo)
    assert np.asarray(out)[300, 300] < 128
    assert _iou(out, truth) > 0.97


def test_the_item_does_not_drag_its_shadow_onto_the_canvas():
    """A cast shadow is the backdrop with the light taken out — off-white, but
    still background."""
    truth = _truth()
    photo = Image.new("RGB", (SIZE, SIZE), BACKDROP)
    shadow = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        tuple(v + 14 for v in ITEM_BOX), radius=RADIUS, fill=255)
    photo.paste(Image.new("RGB", (SIZE, SIZE), (70, 70, 70)), (0, 0),
                shadow.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.55)))
    ImageDraw.Draw(photo).rounded_rectangle(ITEM_BOX, radius=RADIUS, fill=ITEM)
    out = _refined(_chewed(truth), photo)
    assert _missed(out, truth) < 0.03    # the item still comes back
    assert _spill(out, truth) < 0.02     # the shadow does not come with it


def test_a_dark_item_is_not_mistaken_for_shadow():
    """Black jeans on a white sweep are darker than the backdrop too — the
    shadow rule must not cost them their border."""
    truth = _truth()
    photo = _photo(item=(38, 42, 52))
    assert _missed(_refined(_chewed(truth), photo), truth) < 0.03


def test_a_cluttered_backdrop_only_trims_never_grows():
    """Against scenery, 'not the backdrop colour' stops meaning 'item', so the
    border must not expand into it."""
    rng = _rng(3)
    photo = Image.fromarray(
        np.clip(rng.integers(-60, 61, (SIZE, SIZE, 3)) + np.array([150, 120, 90]),
                0, 255).astype(np.uint8))
    truth = _truth()
    ImageDraw.Draw(photo).rounded_rectangle(ITEM_BOX, radius=RADIUS, fill=ITEM)
    out = _refined(_chewed(truth), photo)
    assert _spill(out, truth) < 0.01


def test_the_pass_can_be_switched_off():
    """BG_SOLIDIFY=off puts the old plain-erode behaviour back."""
    truth, photo = _truth(), _photo()
    chewed = _chewed(truth)
    on = _refined(chewed, photo)
    images_solidify = images._SOLIDIFY
    try:
        images._SOLIDIFY = False
        off = _refined(chewed, photo)
    finally:
        images._SOLIDIFY = images_solidify
    assert _iou(off, truth) < _iou(on, truth)
    assert np.array_equal(np.asarray(off), np.asarray(_refined(chewed)))


def test_a_matte_that_kept_nothing_is_left_alone():
    """Degenerate mattes fall through untouched — the caller's own guards
    decide whether to keep the original photo."""
    empty = Image.new("L", (SIZE, SIZE), 0)
    assert images._solidify_border(empty, _photo()) is None
    assert images._solidify_border(Image.new("L", (SIZE, SIZE), 255), _photo()) is None

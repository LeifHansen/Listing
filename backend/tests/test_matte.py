"""The mask-safety rules, tested on shapes with known answers.

These are the rules the cutout leans on to decide whether a refinement is an
improvement or a deletion, so they are tested directly rather than only
through the pipeline: when a real photo comes out wrong, the first question is
which rule misjudged it, and that is only answerable if each rule has its own
test.
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from backend.services import matte  # noqa: E402


def _rect(shape, y0, y1, x0, x1, value=True):
    arr = np.zeros(shape, dtype=bool) if value is True else np.zeros(shape, np.uint8)
    arr[y0:y1, x0:x1] = value
    return arr


# --- confident core ---------------------------------------------------------
def test_the_core_is_what_the_model_was_sure_about_not_its_soft_rim():
    alpha = np.zeros((200, 200), np.uint8)
    alpha[50:150, 50:150] = 255
    alpha[45:50, 50:150] = 120        # a soft rim the model was unsure about
    core = matte.confident_core(alpha)
    assert not core[46, 100]           # the unsure rim is not protected
    assert core[100, 100]              # the middle of the item is
    assert not core[50, 100]           # nor is the outermost confident row


def test_a_matte_with_no_confident_pixels_protects_nothing():
    alpha = np.full((100, 100), 130, np.uint8)  # all "maybe"
    assert not matte.confident_core(alpha).any()
    assert matte.core_loss(matte.confident_core(alpha), np.zeros((100, 100), bool)) == 0.0


# --- rim vs bite ------------------------------------------------------------
def test_trimming_an_even_rim_is_not_counted_as_losing_the_subject():
    """The distinction the whole design turns on. A halo trim removes a ring,
    which is contiguous and can be a large share of a thin item — so neither
    area nor connectivity can identify it. Thickness can."""
    core = _rect((300, 300), 60, 240, 60, 240)
    trimmed = _rect((300, 300), 66, 234, 66, 234)   # 6px off every side
    assert matte.core_loss(core, trimmed) > 0.10    # substantial by area...
    assert matte.structural_loss(core, trimmed) == 0.0  # ...but all of it rim


def test_a_bite_out_of_the_item_is_counted():
    core = _rect((300, 300), 60, 240, 60, 240)
    bitten = core.copy()
    bitten[100:160, 60:120] = False                  # a 60x60 chunk gone
    assert matte.structural_loss(core, bitten) > 0.05


def test_the_restored_bite_is_the_whole_bite_not_its_middle():
    core = _rect((300, 300), 60, 240, 60, 240)
    bitten = core.copy()
    bitten[100:160, 60:120] = False
    restored = matte.structural_loss_mask(core, bitten)
    # every pixel of the bite comes back, not just the part thick enough to
    # survive the detector's erosion
    assert restored[100:160, 60:120].all()


# --- components -------------------------------------------------------------
def test_a_dangling_tag_survives_speck_removal_and_a_crumb_does_not():
    mask = _rect((400, 400), 60, 340, 100, 300)      # the garment
    mask[360:390, 190:215] = True                    # a hanging tag
    mask[20:24, 20:24] = True                        # a JPEG crumb
    confidence = np.where(mask, 255, 0).astype(np.uint8)
    confidence[20:24, 20:24] = 90                    # the model wasn't sure
    kept = matte.keep_components(mask, confidence=confidence)
    assert kept[370, 200], "the tag was deleted"
    assert not kept[22, 22], "the crumb survived"


def test_a_detached_strap_survives_on_shape_alone():
    """No confidence signal at all — long and thin is enough."""
    mask = _rect((400, 400), 60, 300, 120, 280)
    mask[320:332, 60:340] = True                     # a 280x12 strap
    kept = matte.keep_components(mask, confidence=None)
    assert kept[326, 200], "the strap was deleted"


def test_both_shoes_of_a_pair_survive():
    mask = np.zeros((400, 400), bool)
    mask[120:280, 40:180] = True
    mask[120:280, 220:360] = True
    assert matte.keep_components(mask).sum() == mask.sum()


# --- thin structures --------------------------------------------------------
def test_thin_parts_are_found_and_thick_ones_are_not():
    mask = _rect((300, 300), 60, 240, 60, 240)
    mask[140:146, 240:290] = True                    # a 6px-wide lace
    thin = matte.thin_structures(mask, radius=4)
    assert thin[142, 270], "the lace was not recognised as thin"
    assert not thin[150, 150], "the middle of the body was called thin"


# --- palette ----------------------------------------------------------------
def test_a_printed_item_is_described_by_its_colours_not_their_average():
    """The bug this replaces: one median of red and blue is purple, and both
    the red and the blue then measure as 'not the item'."""
    rgb = np.zeros((100, 100, 3), np.float32)
    rgb[:, :50] = (200, 30, 40)
    rgb[:, 50:] = (20, 70, 190)
    mask = np.ones((100, 100), bool)
    colours = matte.palette(rgb, mask, size=4)
    def near(target):
        return any(np.abs(c - np.array(target)).max() < 12 for c in colours)
    assert near((200, 30, 40)) and near((20, 70, 190))
    assert not any(np.abs(c - np.array([110, 50, 115])).max() < 20 for c in colours), \
        "the palette averaged the two colours into one that is neither"


def test_the_dominant_colour_comes_first():
    rgb = np.zeros((100, 100, 3), np.float32)
    rgb[:, :] = (30, 30, 30)
    rgb[:, :10] = (240, 10, 10)
    colours = matte.palette(rgb, np.ones((100, 100), bool))
    assert np.abs(colours[0] - np.array([30, 30, 30])).max() < 12


def test_an_empty_mask_yields_an_empty_palette_rather_than_an_error():
    assert matte.palette(np.zeros((10, 10, 3), np.float32),
                         np.zeros((10, 10), bool)).shape[0] == 0


# --- trim caps --------------------------------------------------------------
def test_the_trim_is_dropped_whole_when_it_overshoots():
    base = _rect((200, 200), 40, 160, 40, 160)
    gutted = _rect((200, 200), 90, 110, 90, 110)
    out, capped = matte.cap_trim(base, gutted)
    assert capped and (out == base).all(), "a runaway trim was applied anyway"


def test_a_believable_trim_is_kept():
    base = _rect((200, 200), 40, 160, 40, 160)
    trimmed = _rect((200, 200), 42, 158, 42, 158)
    out, capped = matte.cap_trim(base, trimmed)
    assert not capped and (out == trimmed).all()


def test_a_part_that_would_lose_most_of_itself_is_handed_back():
    core = _rect((400, 400), 60, 340, 100, 300)
    core[360:390, 190:215] = True                    # a small tag
    kept = core.copy()
    kept[362:388, 192:213] = False                   # trim eats most of the tag
    starved = matte.undersized_parts(core, kept)
    assert starved[370, 200], "the tag was left half-eaten"
    assert not starved[200, 200], "the body was restored for no reason"


def test_a_part_that_only_loses_a_rim_is_left_alone():
    core = _rect((400, 400), 60, 340, 100, 300)
    kept = _rect((400, 400), 66, 334, 106, 294)
    assert not matte.undersized_parts(core, kept).any()


# --- result type ------------------------------------------------------------
def test_kept_original_is_not_a_failure_and_needs_review_is_usable():
    assert not matte.CutoutResult(status=matte.KEPT_ORIGINAL).ok
    assert not matte.CutoutResult(status=matte.FAILED).ok
    assert matte.CutoutResult(status=matte.NEEDS_REVIEW).ok
    assert matte.CutoutResult(status=matte.APPLIED).ok


def test_warnings_do_not_repeat_themselves():
    r = matte.CutoutResult()
    r.warn("check the edges")
    r.warn("check the edges")
    r.warn("")
    assert r.warnings == ["check the edges"]


def test_the_result_serialises_every_field_the_api_promises():
    r = matte.CutoutResult(status=matte.NEEDS_REVIEW, engine="rembg:isnet")
    r.warn("hm")
    r.metrics["core_retention"] = 0.99
    assert set(r.as_dict()) == {"status", "engine", "error_code", "warnings", "metrics"}
    assert r.as_dict()["status"] in matte.STATUSES

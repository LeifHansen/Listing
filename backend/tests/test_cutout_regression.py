"""Cutout regression suite — does the refinement chain keep the product?

Each case builds a photo, its exact silhouette, and a plausibly-wrong model
matte, runs the real refinement chain over it, and measures what survived.
`retention` and `worst_hole` are the ones that matter: a listing photo with a
rough edge is fine, a listing photo missing a strap is not.

Thresholds live in fixtures/cutout/manifest.json so tightening a guarantee is
a data change, and so the same file can describe a private set of real photos
(CUTOUT_FIXTURES_DIR) without touching this file at all.
"""
import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

from backend.services import images  # noqa: E402
from backend.tests import cutout_fixtures as fx  # noqa: E402

CASES = fx.load_manifest()


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_cutout_keeps_the_product(case):
    photo, truth, matte_in = fx.build_case(case)
    out = images._refine_alpha(matte_in, source=photo)

    kept = fx.retention(out, truth)
    hole = fx.worst_hole(out, truth)
    leaked = fx.spill(out, truth)
    detail = (f"{case['name']}: retention={kept:.3f} worst_hole={hole:.3f} "
              f"spill={leaked:.3f}")

    assert kept >= case["min_retention"], f"lost product — {detail}"
    assert hole <= case["max_hole"], f"a solid piece went missing — {detail}"
    assert leaked <= case["max_spill"], f"backdrop left behind — {detail}"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_refinement_never_loses_more_than_the_raw_matte(case):
    """The floor under every case: refinement may reshape the model's matte,
    but it may not end up keeping LESS of the product than the raw matte it
    started from. That is the regression that produced 'the background remover
    ate my item' — each pass individually defensible, the chain destructive.
    """
    photo, truth, matte_in = fx.build_case(case)
    refined = images._refine_alpha(matte_in, source=photo)
    # 2% slack: the raw matte's soft rim thresholds differently, and trimming
    # a genuine halo legitimately gives up a sliver of it.
    assert fx.retention(refined, truth) >= fx.retention(matte_in, truth) - 0.02, (
        f"{case['name']}: refinement kept less than the raw matte "
        f"({fx.retention(refined, truth):.3f} vs {fx.retention(matte_in, truth):.3f})")

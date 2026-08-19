"""Manual timing harness for the image pipeline (not collected by pytest).

Times the post-inference mask stages and a full no-AI optimize pass over
synthetic product photos, so a change to backend/services/images.py can be
judged in numbers before it ships:

    AUTO_ORIENT=off python backend/tests/perf_pipeline.py

Compare runs with the relevant knobs, e.g.:

    BG_SOLIDIFY=off        ... # is the border pass the cost?
    PHOTO_LOCAL_WORKERS=1  ... # what does the batch pool buy?

No network and no model: rembg inference is NOT exercised (the synthetic
matte stands in for its output), so the numbers isolate exactly the code
this repo owns. Requires numpy + scipy + Pillow.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("AUTO_ORIENT", "off")  # no vision calls from a bench

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from backend.services import images  # noqa: E402

W, H = 3200, 2400          # a MAX_WORK_SIDE-clamped phone photo
NATIVE = (1024, 768)       # what a REMBG_MAX_SIDE=1024 inference returns


def synth_photo(seed: int = 42) -> tuple[Image.Image, np.ndarray]:
    """A light-sweep 'product photo' with an irregular subject, plus the
    ground-truth subject mask."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = W * 0.52, H * 0.55
    ang = np.arctan2(yy - cy, xx - cx)
    base_r = min(W, H) * 0.33
    wobble = (np.sin(3 * ang) * 0.09 + np.sin(7 * ang + 1.3) * 0.05) * base_r
    subject = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) < (base_r + wobble)
    photo = np.empty((H, W, 3), np.float32)
    photo[...] = (236, 234, 230)
    photo -= ((yy / H) * 14)[..., None]
    photo[subject] = (86, 92, 140)
    photo += rng.normal(0, 3.0, (H, W, 1)).astype(np.float32)
    return Image.fromarray(np.clip(photo, 0, 255).astype(np.uint8), "RGB"), subject


def synth_matte(subject: np.ndarray, seed: int = 42) -> Image.Image:
    """A plausible raw model matte at inference resolution: soft edge, ragged
    bites along the rim, interior pinholes."""
    rng = np.random.default_rng(seed)
    sw, sh = NATIVE
    small = np.asarray(Image.fromarray(subject.astype(np.uint8) * 255, "L")
                       .resize(NATIVE, Image.BILINEAR)) > 128
    alpha = np.where(small, 255, 0).astype(np.int16)
    alpha = np.asarray(Image.fromarray(alpha.astype(np.uint8), "L")
                       .filter(ImageFilter.GaussianBlur(2))).astype(np.int16)
    bites = np.asarray(Image.fromarray(
        ((rng.random((sh, sw)) < 0.0006) * 255).astype(np.uint8), "L")
        .filter(ImageFilter.MaxFilter(5))) > 0
    alpha[(alpha > 40) & (alpha < 215) & bites] = 20
    holes = np.asarray(Image.fromarray(
        ((rng.random((sh, sw)) < 0.0001) * 255).astype(np.uint8), "L")
        .filter(ImageFilter.MaxFilter(7))) > 0
    alpha[holes & small] = 10
    return Image.fromarray(np.clip(alpha, 0, 255).astype(np.uint8), "L")


def timed(label: str, fn, n: int = 3):
    best, out = None, None
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    print(f"{label:44s} {best * 1000:8.1f} ms")
    return out


def iou(mask: Image.Image, truth: np.ndarray) -> float:
    m = np.asarray(mask) >= 128
    return float((m & truth).sum() / max(1, (m | truth).sum()))


def main() -> None:
    print(f"photo {W}x{H}; matte at {NATIVE[0]}x{NATIVE[1]}; "
          f"BG_SOLIDIFY={'on' if images._SOLIDIFY else 'off'}; "
          f"scan={images._SOLIDIFY_SCAN_SIDE}")
    rgb, truth = synth_photo()
    matte = synth_matte(truth)

    refined = timed("_refine_alpha (native matte -> full size)",
                    lambda: images._refine_alpha(matte, rgb, out_size=(W, H)))
    print(f"{'  mask IoU vs ground truth':44s} {iou(refined, truth):8.4f}")
    timed("_fill_interior_holes",
          lambda: images._fill_interior_holes(refined, rgb))
    timed("_compose_on_white (shadow + holes)",
          lambda: images._compose_on_white(rgb, refined))
    timed("_autocrop_borders", lambda: images._autocrop_borders(rgb))
    timed("_fill_square", lambda: images._fill_square(rgb))
    sized = rgb.resize((1600, 1600), Image.LANCZOS)
    timed("_enhance @1600", lambda: images._enhance(sized))

    # Full no-cutout batch through optimize_all (AUTO_ORIENT=off above keeps
    # it offline). Exercises the PHOTO_LOCAL_WORKERS pool.
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "orig", Path(td) / "opt"
        src.mkdir()
        for i in range(6):
            photo, _ = synth_photo(seed=i)
            photo.save(src / f"src_{i:03d}.jpg", "JPEG", quality=90)
        t0 = time.perf_counter()
        results = images.optimize_all(src, dst, remove_bg=False)
        dt = time.perf_counter() - t0
        errors = [r for r in results if r.get("error")]
        print(f"{'optimize_all 6 photos (no cutout)':44s} {dt * 1000:8.1f} ms"
              f"   ({len(results) - len(errors)} ok, "
              f"workers={images._LOCAL_BATCH_WORKERS})")


if __name__ == "__main__":
    main()

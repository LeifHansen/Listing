"""Fixtures for the cutout regression suite.

Every case is GENERATED, not committed: a seeded recipe produces the photo,
the ground-truth silhouette, and a plausible model matte for it. That buys
three things a folder of JPEGs does not — the repo stays small, the ground
truth is exact rather than hand-traced, and CI can run the whole suite on a
machine that cannot load a segmentation model at all.

What it does NOT buy is realism, and the suite is honest about that: these
exercise the REFINEMENT chain (despeckle, close, snap, trim, guards), which is
where subject loss actually happens and where the rules in services/matte.py
live. To regression-test the model itself you need real photos, so the loader
below also accepts a private directory of them — see load_manifest().

Cases mirror the ways a cutout goes wrong on a real listing: black item on a
dark floor, white item on a white sweep, a printed tee whose colours are
nothing like each other, straps and laces and dangling tags, a pair of shoes,
an item running off the frame, fringe, a specular highlight, a see-through
panel, a cluttered backdrop, and subjects that fill 6% or 80% of the frame.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

SIZE = 640
MANIFEST = Path(__file__).parent / "fixtures" / "cutout" / "manifest.json"

# Where a seller's own photos can live for a private regression run. Same
# manifest format; the directory is gitignored and never committed, because
# nobody's inventory photos belong in a public repo.
PRIVATE_DIR_ENV = "CUTOUT_FIXTURES_DIR"


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _noise(img: Image.Image, seed: int, amount: float = 4.0) -> Image.Image:
    """Sensor grain. Without it every region is perfectly flat and the
    backdrop-variance checks measure something no camera produces."""
    arr = np.asarray(img, dtype=np.float32)
    arr += _rng(seed).normal(0, amount, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), img.mode)


def _blank(colour: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (SIZE, SIZE), colour)


def _mask() -> Image.Image:
    return Image.new("L", (SIZE, SIZE), 0)


# --- case builders ----------------------------------------------------------
# Each returns (photo RGB, truth L). The matte the "model" would have produced
# is derived from truth by degrade() below, so every case shares one story
# about how mattes are wrong.

def _plain_item(item: tuple[int, int, int], backdrop: tuple[int, int, int],
                box=(150, 130, 490, 510), seed: int = 1):
    photo, truth = _blank(backdrop), _mask()
    ImageDraw.Draw(photo).rounded_rectangle(box, radius=30, fill=item)
    ImageDraw.Draw(truth).rounded_rectangle(box, radius=30, fill=255)
    return _noise(photo, seed), truth


def black_on_dark():
    """A black hoodie photographed on a dark wood floor — the case that makes
    a model call the ITEM background and keep only its printed logo."""
    return _plain_item((26, 26, 30), (58, 50, 44), seed=2)


def white_on_white():
    """A cream blouse on a white sweep. Barely any contrast to find, so the
    matte wanders around inside the garment."""
    return _plain_item((243, 241, 236), (252, 252, 252), seed=3)


def printed_multicolor():
    """A printed tee: four unrelated colours. The single-median description of
    "the item" lands between them and calls most of the shirt background."""
    photo, truth = _plain_item((235, 238, 240), (250, 250, 250), seed=4)
    draw = ImageDraw.Draw(photo)
    for i, colour in enumerate([(200, 30, 40), (20, 70, 190), (250, 200, 20),
                                (20, 150, 80)]):
        y = 180 + i * 70
        draw.rounded_rectangle((190, y, 450, y + 55), radius=12, fill=colour)
    return photo, truth


def thin_strap():
    """A shoulder bag. The body is unmissable; the strap is 8px of the same
    fabric and is what area-only speck removal deletes."""
    photo, truth = _blank((248, 248, 250)), _mask()
    for target, fill in ((photo, (70, 55, 45)), (truth, 255)):
        d = ImageDraw.Draw(target)
        d.rounded_rectangle((200, 330, 440, 520), radius=18, fill=fill)
        d.arc((205, 120, 435, 380), start=185, end=355, fill=fill, width=9)
    return _noise(photo, 5), truth


def shoe_pair():
    """Two shoes: two components, both real. Nothing here is a speck."""
    photo, truth = _blank((250, 250, 250)), _mask()
    for box in ((70, 230, 300, 430), (350, 230, 580, 430)):
        ImageDraw.Draw(photo).rounded_rectangle(box, radius=40, fill=(40, 60, 110))
        ImageDraw.Draw(truth).rounded_rectangle(box, radius=40, fill=255)
    return _noise(photo, 6), truth


def hanging_tag():
    """A jacket with the size tag still on a string — detached, small, and
    absolutely part of the photo the seller framed."""
    photo, truth = _plain_item((60, 90, 140), (250, 250, 250),
                               box=(160, 120, 480, 430), seed=7)
    for target, fill in ((photo, (245, 245, 240)), (truth, 255)):
        ImageDraw.Draw(target).rectangle((300, 470, 342, 530), fill=fill)
    return photo, truth


def edge_touching():
    """A coat that runs off the bottom and right of the frame. There is no
    backdrop fringe to trim there, so trimming eats a gutter into the item."""
    return _plain_item((90, 40, 45), (249, 249, 251),
                       box=(120, 200, SIZE + 40, SIZE + 40), seed=8)


def fringe():
    """A fur collar / fringed hem: the boundary is genuinely ragged, so the
    matte's edge is soft over a wide band."""
    photo, truth = _plain_item((120, 100, 80), (250, 250, 250),
                               box=(160, 160, 480, 440), seed=9)
    rng, pd, td = _rng(19), ImageDraw.Draw(photo), ImageDraw.Draw(truth)
    for _ in range(260):
        x = int(rng.integers(160, 480))
        length = int(rng.integers(10, 34))
        pd.line((x, 440, x + int(rng.integers(-6, 6)), 440 + length),
                fill=(120, 100, 80), width=2)
        td.line((x, 440, x + int(rng.integers(-6, 6)), 440 + length),
                fill=255, width=2)
    return photo, truth


def reflective():
    """A patent leather bag with a blown-out specular band. The highlight is
    the backdrop's colour exactly, sitting in the middle of the item."""
    photo, truth = _plain_item((35, 35, 40), (250, 250, 250), seed=10)
    ImageDraw.Draw(photo).rounded_rectangle((190, 230, 450, 300), radius=20,
                                            fill=(250, 250, 252))
    return photo, truth


def transparent_panel():
    """A bottle / mesh panel: the backdrop shows straight through the middle of
    the product, which is still product."""
    photo, truth = _plain_item((70, 120, 110), (250, 250, 250), seed=11)
    ImageDraw.Draw(photo).ellipse((240, 240, 400, 400), fill=(238, 244, 242))
    return photo, truth


def cast_shadow():
    """The everyday shot: item on a table under one light, throwing a soft
    shadow. The shadow is the thing a model most often decides is part of the
    item, and it is what comes out the far side as a grey scrap on white."""
    photo, truth = _blank((250, 250, 250)), _mask()
    shadow = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(shadow).ellipse((190, 430, 560, 560), fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(22))
    photo = Image.composite(_blank((120, 118, 115)), photo, shadow)
    box = (150, 130, 470, 500)
    ImageDraw.Draw(photo).rounded_rectangle(box, radius=28, fill=(55, 70, 100))
    ImageDraw.Draw(truth).rounded_rectangle(box, radius=28, fill=255)
    return _noise(photo, 21), truth


def table_edge():
    """Shot on a worktop with the counter's edge running behind the item. The
    model keeps a wedge of counter attached to the product -- a scrap far too
    thick to be a halo, and nowhere near the item's colour."""
    photo, truth = _blank((238, 236, 232)), _mask()
    ImageDraw.Draw(photo).rectangle((0, 470, SIZE, SIZE), fill=(168, 140, 110))
    box = (170, 150, 470, 490)
    ImageDraw.Draw(photo).rounded_rectangle(box, radius=26, fill=(200, 60, 55))
    ImageDraw.Draw(truth).rounded_rectangle(box, radius=26, fill=255)
    return _noise(photo, 22), truth


def busy_background():
    """Shot on a rug, on a bed, in a room. "Not the backdrop colour" stops
    meaning "item", which is exactly when growing the border is dangerous."""
    photo, truth = _blank((190, 170, 150)), _mask()
    rng, draw = _rng(12), ImageDraw.Draw(photo)
    for _ in range(220):
        x, y = int(rng.integers(0, SIZE)), int(rng.integers(0, SIZE))
        r = int(rng.integers(8, 46))
        draw.ellipse((x - r, y - r, x + r, y + r),
                     fill=tuple(int(v) for v in rng.integers(90, 235, 3)))
    box = (180, 180, 470, 470)
    draw.rounded_rectangle(box, radius=24, fill=(30, 60, 90))
    ImageDraw.Draw(truth).rounded_rectangle(box, radius=24, fill=255)
    return _noise(photo, 13), truth


def small_subject():
    """A ring / a pin: a few percent of the frame, right where a coverage
    guard tuned for garments would call it 'subject erased'."""
    return _plain_item((180, 150, 60), (250, 250, 250),
                       box=(270, 270, 380, 380), seed=14)


def large_subject():
    """A rug filling the frame: almost no backdrop left to sample."""
    return _plain_item((120, 60, 60), (250, 250, 250),
                       box=(30, 30, 610, 610), seed=15)


BUILDERS: dict[str, Callable[[], tuple[Image.Image, Image.Image]]] = {
    "black_on_dark": black_on_dark,
    "white_on_white": white_on_white,
    "printed_multicolor": printed_multicolor,
    "thin_strap": thin_strap,
    "shoe_pair": shoe_pair,
    "hanging_tag": hanging_tag,
    "edge_touching": edge_touching,
    "fringe": fringe,
    "reflective": reflective,
    "transparent_panel": transparent_panel,
    "cast_shadow": cast_shadow,
    "table_edge": table_edge,
    "busy_background": busy_background,
    "small_subject": small_subject,
    "large_subject": large_subject,
}


# --- how a real matte is wrong ----------------------------------------------
def degrade(truth: Image.Image, mode: str = "chewed", seed: int = 5) -> Image.Image:
    """Turn ground truth into something a model would plausibly have returned.

    Three shapes of wrong, because refinement has to survive all of them:
    `chewed` pulls the edge inside the item and bites lumps out of it,
    `fringed` overshoots and leaves a halo of backdrop, and `soft` keeps the
    outline but blurs it into the mid-alpha mush that hides a ghost.
    """
    if mode == "fringed":
        return truth.filter(ImageFilter.MaxFilter(9)).filter(
            ImageFilter.GaussianBlur(1.5))
    if mode == "soft":
        return truth.filter(ImageFilter.GaussianBlur(4.0))
    if mode == "scrappy":
        # What sellers actually report: not an even fringe, but a LUMP of
        # background still attached -- a shadow, a wedge of table, a bit of
        # the hand that was holding it. Thick enough that no rim-depth trim
        # will ever reach it, and carrying the mid alpha of a model that was
        # not sure, which is the handle for removing it safely.
        rng = _rng(seed + 3)
        scrap = Image.new("L", truth.size, 0)
        draw = ImageDraw.Draw(scrap)
        box = truth.getbbox() or (0, 0, SIZE, SIZE)
        for _ in range(3):
            x = int(rng.integers(box[0], max(box[0] + 1, box[2])))
            y = int(rng.integers(max(0, box[3] - 30), min(SIZE, box[3] + 70)))
            r = int(rng.integers(45, 85))
            draw.ellipse((x - r, y - r, x + r, y + r), fill=160)
        scrap = scrap.filter(ImageFilter.GaussianBlur(6))
        return ImageChops.lighter(truth, scrap)
    rng = _rng(seed)
    inside = np.asarray(truth, dtype=np.uint8) >= 128
    mask = truth.filter(ImageFilter.MinFilter(5))
    draw = ImageDraw.Draw(mask)
    for _ in range(12):
        x, y = int(rng.integers(0, SIZE)), int(rng.integers(0, SIZE))
        if inside[y, x]:
            draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill=0)
    return mask.filter(ImageFilter.GaussianBlur(1.0))


# --- metrics ----------------------------------------------------------------
def _bits(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.uint8) >= 128


def retention(result: Image.Image, truth: Image.Image) -> float:
    """Fraction of the real product the cutout kept. THE number: everything
    else is cosmetic next to "is the item still all there"."""
    t = _bits(truth)
    if not t.any():
        return 1.0
    return float((_bits(result) & t).sum()) / float(t.sum())


def spill(result: Image.Image, truth: Image.Image) -> float:
    """Fraction of the backdrop the cutout wrongly kept — leftover halo."""
    t = _bits(truth)
    outside = ~t
    if not outside.any():
        return 0.0
    return float((_bits(result) & outside).sum()) / float(outside.sum())


def worst_hole(result: Image.Image, truth: Image.Image) -> float:
    """Largest solid chunk of the product that went missing, as a fraction of
    the product. Catches the failure an average cannot: a cutout can retain
    97% of a jacket and still be missing one whole sleeve."""
    from backend.services import matte
    t, a = _bits(truth), _bits(result)
    lost = t & ~a
    if not lost.any():
        return 0.0
    seed = matte.erode(lost, 4)
    if not seed.any():
        return 0.0
    return float(matte.reconstruct(seed, lost).sum()) / float(max(1, t.sum()))


# --- manifest ---------------------------------------------------------------
def load_manifest(path: Optional[Path] = None) -> list[dict]:
    """The regression cases to run.

    Defaults to the generated suite in fixtures/cutout/manifest.json. Point
    CUTOUT_FIXTURES_DIR at a private directory holding its own manifest.json
    to run the same checks against real photos: entries there carry `photo`
    and `truth` file names instead of a `builder`, and the thresholds mean the
    same thing. That directory is gitignored — real seller photos are not
    ours to commit, and a regression suite is not a reason to try.
    """
    private = os.getenv(PRIVATE_DIR_ENV, "").strip()
    if private and path is None:
        candidate = Path(private) / "manifest.json"
        if candidate.exists():
            cases = json.loads(candidate.read_text())
            for case in cases:
                case["_dir"] = str(Path(private))
            return cases
    return json.loads((path or MANIFEST).read_text())


def build_case(case: dict) -> tuple[Image.Image, Image.Image, Image.Image]:
    """(photo, truth, matte) for one manifest entry."""
    directory = case.get("_dir")
    if directory and case.get("photo"):
        photo = Image.open(Path(directory) / case["photo"]).convert("RGB")
        truth = Image.open(Path(directory) / case["truth"]).convert("L")
        if truth.size != photo.size:
            truth = truth.resize(photo.size, Image.NEAREST)
    else:
        photo, truth = BUILDERS[case["builder"]]()
    return photo, truth, degrade(truth, case.get("matte", "chewed"),
                                 seed=case.get("seed", 5))

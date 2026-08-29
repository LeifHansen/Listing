"""Safety rules for background-removal mattes.

Pure PIL + NumPy (SciPy optional). No rembg, no onnxruntime, no network — so
every rule that decides whether a cutout is safe to ship can be exercised in
CI on a machine that cannot run the segmentation model, against synthetic
mattes with known ground truth (see tests/test_matte.py).

The rules exist because the failure that matters is not a rough edge, it is a
cutout that quietly deletes part of the product: a bag's strap, one shoe of a
pair, a hanging size tag, the dark half of a two-tone jacket. Those all come
out of the same three habits in a naive refinement chain:

  * area-only speck removal, which cannot tell a 1%-of-the-mask lace from a
    1%-of-the-mask JPEG crumb;
  * describing "the item" with ONE colour, which is false for anything
    printed, patterned or two-tone, and makes parts of the item measure as
    backdrop;
  * trimming the silhouette inward with no cap, so a bad colour reading eats
    real product instead of a halo.

So: keep the model's own matte as the reference, measure every refinement
against it, and roll back the ones that take meaningful, high-confidence
subject away. A slightly rough cutout is recoverable; a deleted strap is not.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# --- tuning -----------------------------------------------------------------
# Alpha at or above this is the model saying "this is definitely the product".
# Refinements are allowed to reshape the uncertain rim; they are not allowed to
# delete what the model was sure about.
CONFIDENT_ALPHA = int(os.getenv("CUTOUT_CONFIDENT_ALPHA", "200") or 200)
# How much of a detached component has to be confident before it counts as a
# real part of the product rather than a crumb.
CONFIDENT_SHARE = float(os.getenv("CUTOUT_CONFIDENT_SHARE", "0.25") or 0.25)
# How far inside its own boundary a confident pixel has to be before losing it
# counts as losing SUBJECT rather than trimming a rim. Trimming a 1-2px halo is
# the whole point of the refinement; eating 10px into a sleeve is not.
CORE_INSET_PX = int(os.getenv("CUTOUT_CORE_INSET", "3") or 3)
# Fraction of that confident core a refinement may drop as STRUCTURE — see
# structural_loss(). Deliberately small: structural loss means chunks thick
# enough to be part of the product, and there is no innocent reason to lose
# 1% of the item in lumps.
MAX_CORE_LOSS = float(os.getenv("CUTOUT_MAX_CORE_LOSS", "0.01") or 0.01)
# Half-thickness that separates "trimmed a rim" from "took a bite". A halo the
# trim is meant to remove is a few px of uniform ring; a bite out of a hem, a
# cuff or a heel is a blob. Erode the lost region by this and a ring vanishes
# while a bite survives — which is the whole discrimination.
STRUCTURAL_RADIUS_PX = int(os.getenv("CUTOUT_STRUCTURAL_RADIUS", "4") or 4)
# How deep, as a fraction of the scan's short side, the inward trim may reach.
# This is the important cap and it is a cap on DEPTH, not area: a halo is a rim
# a few px thick, so bounding the depth bounds the damage without punishing a
# thin item, whose rim is a large share of its area precisely because it is
# thin. An area-only cap gets that backwards and refuses to clean up exactly
# the straps and laces that need it most.
MAX_TRIM_DEPTH_FRACTION = float(os.getenv("CUTOUT_MAX_TRIM_DEPTH", "0.02") or 0.02)
MIN_TRIM_DEPTH_PX = 3
# Area backstop for the pathological case. With the depth cap in place this
# should never fire on a real photo; it is here so that "the colour maths went
# completely wrong" has a floor under it rather than a slope.
MAX_TRIM_FRACTION = float(os.getenv("CUTOUT_MAX_TRIM", "0.25") or 0.25)
# A detached blob smaller than this fraction of the mask is a crumb UNLESS one
# of the protections below applies.
SPECK_FRACTION = float(os.getenv("CUTOUT_SPECK_FRAC", "0.02") or 0.02)
# ...and this is the floor those protections still have to clear, as a fraction
# of the whole frame. Below it there is nothing recognisable to protect.
MIN_PART_FRAME_FRACTION = float(
    os.getenv("CUTOUT_MIN_PART_FRAME_FRAC", "0.0015") or 0.0015)
# How much of itself each confident part has to survive the inward trim. Below
# this the trim is not cleaning that part up, it is removing it.
MIN_PART_RETENTION = float(os.getenv("CUTOUT_MIN_PART_RETENTION", "0.5") or 0.5)
# A component at least this much longer than it is thick reads as a strap, a
# lace, a chain or a hanging tag rather than as a speck.
ELONGATION = float(os.getenv("CUTOUT_ELONGATION", "3.5") or 3.5)
# Radius (at scan scale) below which a structure counts as "thin". Thin parts
# of the CONFIDENT matte are protected from the inward trim: a halo is never
# confident, but a shoelace always is.
THIN_RADIUS_PX = int(os.getenv("CUTOUT_THIN_RADIUS", "3") or 3)
# Colours used to describe the item. One is the bug; a handful covers a
# printed tee, a two-tone sneaker or a patterned dress without pretending the
# item is a single flat swatch.
ITEM_PALETTE_SIZE = int(os.getenv("CUTOUT_ITEM_PALETTE", "6") or 6)


# --- result type ------------------------------------------------------------
# Statuses are a closed set, and every one of them is a different thing to tell
# the seller. "failed" is not "kept_original": one means we could not run, the
# other means we ran and decided the original was the better photo.
APPLIED = "applied"              # cutout ran, passed every check, use it
NEEDS_REVIEW = "needs_review"    # cutout ran but a check flagged it — show it,
                                 # say why, let the seller keep or revert
KEPT_ORIGINAL = "kept_original"  # cutout ran and lost, original photo stands
FAILED = "failed"                # cutout could not run (engine/timeout/etc.)

STATUSES = (APPLIED, NEEDS_REVIEW, KEPT_ORIGINAL, FAILED)

# Stable, greppable codes. These go in logs and API responses; the human
# wording can change freely, these cannot.
ERR_SUBJECT_ERASED = "subject_erased"
ERR_DARK_SUBJECT = "dark_subject_removed"


@dataclass
class CutoutResult:
    """What one background removal did, and why.

    Deliberately separate from the image itself: callers keep the pixels, this
    carries the decision. `warnings` is seller-facing prose; `error_code` is
    the machine-readable reason and is "" on success.
    """

    status: str = FAILED
    engine: str = ""
    error_code: str = ""
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when the cutout is usable (even if it wants a look)."""
        return self.status in (APPLIED, NEEDS_REVIEW)

    def warn(self, message: str) -> None:
        if message and message not in self.warnings:
            self.warnings.append(message)

    @property
    def reason(self) -> str:
        """Why this cutout was refused, in the seller's own words — "" when it
        was not refused.

        The LAST warning, not the first, and only when `error_code` says the
        cutout actually lost. A cutout collects ADVISORY warnings as it is
        refined ("some background may still show around the edges"), and those
        are appended before any guard gets to speak; the warning that ended it
        is the one added at the point it gave up. Taking warnings[0] instead
        reports a cosmetic note as the cause of a failure — which is the same
        class of bug as reporting no cause at all.
        """
        if not self.error_code:
            return ""
        return self.warnings[-1] if self.warnings else ""

    def as_dict(self) -> dict:
        return {"status": self.status, "engine": self.engine,
                "error_code": self.error_code, "warnings": list(self.warnings),
                "metrics": dict(self.metrics)}


# --- morphology helpers -----------------------------------------------------
def _ndimage():
    """SciPy's ndimage, or None. It rides in with onnxruntime, but every
    caller here degrades to 'do nothing' rather than assume it."""
    try:
        from scipy import ndimage
        return ndimage
    except Exception:  # noqa: BLE001 - optional dependency
        return None


def _disk(radius: int) -> np.ndarray:
    r = max(1, int(radius))
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


def erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """Shrink `mask` by `radius` px. Identity without SciPy."""
    nd = _ndimage()
    if nd is None or radius <= 0:
        return mask
    return nd.binary_erosion(mask, _disk(radius), border_value=1)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grow `mask` by `radius` px. Identity without SciPy."""
    nd = _ndimage()
    if nd is None or radius <= 0:
        return mask
    return nd.binary_dilation(mask, _disk(radius))


# --- the confident core -----------------------------------------------------
def confident_core(raw_alpha: np.ndarray, inset: int = CORE_INSET_PX,
                   threshold: int = CONFIDENT_ALPHA) -> np.ndarray:
    """The part of the raw matte a refinement must not delete.

    Two conditions, both needed. The model has to have been confident (alpha
    at/above `threshold` — the soft rim where it was guessing is exactly what
    refinement is for), and the pixel has to sit `inset` px inside that
    confident region, so trimming a rim registers as trimming a rim.
    """
    core = raw_alpha >= threshold
    if not core.any():
        return core
    return erode(core, inset)


def core_loss(core: np.ndarray, candidate: np.ndarray) -> float:
    """Fraction of the confident core `candidate` fails to cover (0.0-1.0).

    0.0 when there is no core to lose — an empty core means the model was
    never confident about anything, and there is nothing to protect.
    """
    total = int(core.sum())
    if total <= 0:
        return 0.0
    return float((core & ~candidate).sum()) / float(total)


def reconstruct(seed: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Every connected part of `allowed` that `seed` touches.

    Binary reconstruction. Used to grow a detected bite back to its full
    extent: the detector finds the thick middle of the bite, this recovers the
    whole thing so what gets restored is a shape, not a core with a moat.
    """
    nd = _ndimage()
    if nd is None:
        return seed
    return nd.binary_propagation(seed, mask=allowed)


def structural_loss_mask(core: np.ndarray, candidate: np.ndarray,
                         radius: int = STRUCTURAL_RADIUS_PX) -> np.ndarray:
    """The parts of the confident core `candidate` drops that are BITES, not
    rim.

    This distinction is the whole difficulty of judging a refinement. Trimming
    a halo removes a thin uniform ring all the way around the item — which,
    measured as raw area or even as one connected blob, looks exactly like
    deleting a strap: a ring is contiguous too. What separates them is
    thickness. Erode the lost region: a few px of ring cannot survive it,
    while a bite out of a hem, a cuff or a heel has a thick middle that does.
    Whatever survives is then grown back to its full shape.

    Empty result means the refinement only touched rim, and should be kept.
    """
    lost = core & ~candidate
    if not lost.any():
        return lost
    seed = erode(lost, radius)
    if not seed.any():
        return np.zeros_like(lost)
    return reconstruct(seed, lost)


def structural_loss(core: np.ndarray, candidate: np.ndarray,
                    radius: int = STRUCTURAL_RADIUS_PX) -> float:
    """structural_loss_mask as a fraction of the confident core."""
    total = int(core.sum())
    if total <= 0:
        return 0.0
    return float(structural_loss_mask(core, candidate, radius).sum()) / float(total)


# --- components -------------------------------------------------------------
def keep_components(mask: np.ndarray, confidence: Optional[np.ndarray] = None,
                    speck_frac: float = SPECK_FRACTION,
                    frame_frac: float = MIN_PART_FRAME_FRACTION,
                    elongation: float = ELONGATION) -> np.ndarray:
    """Drop detached crumbs, keep detached PARTS.

    Area alone cannot tell those apart, and the naive version of this rule
    ("smaller than 2% of the mask, delete it") is how a shoelace, a hanging
    size tag, a bag's shoulder strap or the second shoe of a pair disappear
    from a listing photo. A component survives if any of these hold:

      * it is a substantial share of the mask (the original rule);
      * the model was confident about it and it is big enough to see;
      * it is long and thin — a strap, a lace, a chain, a dangling tag.

    Without SciPy there is no labelling, so nothing is dropped: keeping a few
    crumbs beats deleting a strap.
    """
    nd = _ndimage()
    if nd is None or not mask.any():
        return mask
    labels, count = nd.label(mask)
    if count <= 1:
        return mask
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    mask_total = float(max(1, int(mask.sum())))
    frame_total = float(mask.size)
    floor_px = frame_frac * frame_total
    boxes = nd.find_objects(labels)

    keep = np.zeros(sizes.shape[0], dtype=bool)
    for idx in range(1, sizes.shape[0]):
        area = float(sizes[idx])
        if area <= 0:
            continue
        if area / mask_total >= speck_frac:
            keep[idx] = True
            continue
        if area < floor_px:
            continue  # too small for any protection to be meaningful
        if confidence is not None:
            part = labels == idx
            # Judged by how much of the part is confident, not by its average.
            # A soft matte feathers every edge, and on something small — a
            # size tag, a lace — the feather is most of the pixels, so the
            # mean lands below the bar while the middle of it is solid 255.
            # Averaging is how a real part reads as a crumb.
            if float((confidence[part] >= CONFIDENT_ALPHA).mean()) >= CONFIDENT_SHARE:
                keep[idx] = True
                continue
        sl = boxes[idx - 1]
        if sl is None:
            continue
        height = sl[0].stop - sl[0].start
        width = sl[1].stop - sl[1].start
        long_side, short_side = max(height, width), max(1, min(height, width))
        # Thickness from area rather than the bounding box: a diagonal lace
        # has a near-square box but almost no fill.
        thickness = area / max(1.0, float(long_side))
        if long_side / short_side >= elongation or long_side / max(1.0, thickness) >= elongation:
            keep[idx] = True
    return keep[labels]


def thin_structures(mask: np.ndarray, radius: int = THIN_RADIUS_PX) -> np.ndarray:
    """Pixels belonging to parts of `mask` thinner than about 2*radius.

    Used to shield straps, laces and tags from the inward trim. The trim's
    real target — a halo of leftover backdrop — is thin too, which is why this
    is only ever applied to the CONFIDENT matte: a halo is never confident.
    """
    if not mask.any():
        return mask
    opened = dilate(erode(mask, radius), radius)
    return mask & ~opened


# --- colour -----------------------------------------------------------------
def palette(rgb: np.ndarray, mask: np.ndarray, size: int = ITEM_PALETTE_SIZE,
            bits: int = 4) -> np.ndarray:
    """Up to `size` representative colours of the masked region, (k, 3) float.

    Replaces "the item is one colour" — which a global median asserts and
    almost no real product honours. A printed tee measured by its median comes
    out somewhere between the shirt and the print, so BOTH read as
    "not the item", and the trim then eats whichever one also happens to sit
    near the backdrop's colour.

    Coarse histogram rather than k-means: quantise to `bits` per channel, take
    the busiest bins, and return the true mean of each. Fast, deterministic,
    and quite enough — the caller only needs "is this pixel near ANY colour the
    item actually has".

    Returns the colours busiest-first, so [0] is the item's dominant colour.
    """
    empty = np.zeros((0, 3), dtype=np.float32)
    if not mask.any():
        return empty
    pixels = rgb[mask].astype(np.float32)
    if pixels.shape[0] == 0:
        return empty
    shift = max(1, 8 - int(bits))
    levels = 1 << int(bits)
    quantised = np.clip(pixels, 0, 255).astype(np.uint8) >> shift
    keys = (quantised[:, 0].astype(np.int32) * levels + quantised[:, 1]) * levels \
        + quantised[:, 2]
    counts = np.bincount(keys, minlength=levels ** 3)
    order = np.argsort(counts)[::-1]
    out = []
    for key in order[:max(1, int(size))]:
        if counts[key] == 0:
            break
        out.append(pixels[keys == key].mean(axis=0))
    if not out:
        return pixels.mean(axis=0, keepdims=True).astype(np.float32)
    return np.asarray(out, dtype=np.float32)


def nearest_gap(gap_fn, palette_colours: np.ndarray) -> np.ndarray:
    """Distance to the CLOSEST palette colour, as a full-size array.

    `gap_fn(colour)` returns the per-pixel distance to one colour; this folds
    the palette down to a single "how far is this pixel from anything the item
    is made of" field.
    """
    if palette_colours.shape[0] == 0:
        raise ValueError("empty palette")
    out = gap_fn(palette_colours[0])
    for colour in palette_colours[1:]:
        out = np.minimum(out, gap_fn(colour))
    return out


# --- trimming ---------------------------------------------------------------
def cap_trim(base: np.ndarray, trimmed: np.ndarray,
             max_fraction: float = MAX_TRIM_FRACTION) -> tuple[np.ndarray, bool]:
    """Hold the inward trim to `max_fraction` of the silhouette.

    Returns (mask, capped). When the trim overshoots, the whole trim is
    dropped rather than partially applied: a trim that ate 30% of the item got
    its colour reading wrong, and there is no reason to trust the first 6% of
    the same reading. Leaving a halo is a cosmetic problem the seller can see
    and fix; deleting the product is not.
    """
    base_area = float(max(1, int(base.sum())))
    removed = float((base & ~trimmed).sum())
    if removed / base_area <= max_fraction:
        return trimmed, False
    return base, True


def undersized_parts(core: np.ndarray, kept: np.ndarray,
                     min_keep: float = MIN_PART_RETENTION) -> np.ndarray:
    """Confident components that `kept` has eaten more than its share of.

    A trim depth is a rim on a coat and most of a size tag: 13px off a 400px
    jacket is 6% of it, 13px off a 42px tag is 80%. An absolute depth cap
    cannot tell those apart, so this asks the question per part — did each
    connected piece of the confident matte keep at least `min_keep` of itself?
    — and hands back the ones that did not, whole, to be restored.
    """
    nd = _ndimage()
    if nd is None or not core.any():
        return np.zeros_like(core)
    labels, count = nd.label(core)
    if count == 0:
        return np.zeros_like(core)
    total = np.bincount(labels.ravel(), minlength=count + 1)
    survived = np.bincount(labels[kept].ravel(), minlength=count + 1)
    ratio = np.divide(survived, np.maximum(total, 1), dtype=np.float64)
    losing = (ratio < min_keep)
    losing[0] = False
    return losing[labels]


# A leftover scrap has to be at least this thick (half-width, at scan scale)
# before it is removed wholesale. Below it we are looking at fur, fringe, a
# lace or an anti-aliased edge — all low-confidence, all genuinely the item.
SCRAP_RADIUS_PX = int(os.getenv("CUTOUT_SCRAP_RADIUS", "5") or 5)
# How much anti-aliased skirt to keep outside the confident subject, so the
# cutout's edge stays soft rather than cut with scissors.
EDGE_KEEP_PX = int(os.getenv("CUTOUT_EDGE_KEEP", "5") or 5)


def uncertain_scraps(raw_alpha: np.ndarray, mask: np.ndarray,
                     core: np.ndarray, radius: int = SCRAP_RADIUS_PX,
                     edge_keep: int = EDGE_KEEP_PX) -> np.ndarray:
    """Lumps of leftover background still attached to the subject.

    This is the failure sellers actually report, and it is not a halo: it is a
    cast shadow, a wedge of the table, a bit of the hand that was holding the
    item — swallowed whole by the matte. Depth-capped trimming can never reach
    it, precisely because the cap exists to stop a trim eating a sleeve.

    So depth is the wrong question. Confidence is the right one. A scrap is
    somewhere the MODEL WAS UNSURE, thick enough to be a lump rather than an
    edge, and reachable from outside the mask. All three matter:

      * unsure, so it can never take the product (the confident core, plus a
        skirt of anti-aliasing around it, is excluded outright);
      * thick, so fur, fringe, laces and soft edges — low-confidence and
        genuinely the item — are left alone;
      * reachable from outside, so an uncertain patch in the MIDDLE of the
        product (a specular panel, a printed graphic) is not punched out.

    Returns the pixels to remove.
    """
    empty = np.zeros_like(mask)
    nd = _ndimage()
    if nd is None or not mask.any():
        return empty
    candidate = mask & (raw_alpha < CONFIDENT_ALPHA) & ~dilate(core, edge_keep)
    if not candidate.any():
        return empty
    thick = erode(candidate, radius)
    if not thick.any():
        return empty
    lumps = reconstruct(thick, candidate)
    # ...and only the lumps that reach the outside.
    touching = reconstruct(candidate & dilate(~mask, 1), candidate)
    return lumps & touching


def rim(mask: np.ndarray, depth: int) -> np.ndarray:
    """The outer `depth` px of `mask` — everything the inward trim is allowed
    to touch. Anything deeper than this is the item, not a halo."""
    if depth <= 0 or not mask.any():
        return np.zeros_like(mask)
    return mask & ~erode(mask, depth)


def trim_depth_for(short_side: int) -> int:
    """Trim depth in px for a mask being worked at `short_side` resolution."""
    return max(MIN_TRIM_DEPTH_PX, int(round(short_side * MAX_TRIM_DEPTH_FRACTION)))


def protect(mask: np.ndarray, protected: np.ndarray) -> np.ndarray:
    """Put `protected` back into `mask` — the rollback primitive."""
    return mask | protected


# --- is the cut a real boundary? --------------------------------------------
# The dark-subject guard used to ask "is the region we removed large and
# dark?" and throw the cutout away when it was. That question has a false
# premise — that real backdrops are white or neutral — and sellers who shoot
# on a dark floor, a slate worktop, black card or asphalt fail it with a
# PERFECT cutout, so background removal silently does nothing for them and
# there is no clue why (see tests/test_dark_backdrop.py).
#
# The failure the guard is actually for is narrower: the model cut THROUGH a
# continuous dark object — a black hoodie filling the frame — and kept only
# its bright printed graphic. What separates that from a dark backdrop is not
# brightness, it is whether the photo contains a boundary where the matte
# claims one. So ask the photo:
#
#   * edge_ratio  — is the gradient along the silhouette stronger than the
#     texture of the region being removed? A cut across one continuous
#     material rides at the same level as that material's own grain. Taking
#     it as a RATIO against that grain is what lets a grainy concrete floor
#     and a seamless white sweep be judged on one scale.
#   * colour_gap  — do the pixels just inside the cut actually look different
#     from the pixels just outside it? "The item looked like the background"
#     is the claim; this measures it directly instead of via a proxy.
#
# Only when BOTH say there is nothing there is the cutout refused. Either one
# alone is enough to keep it: the cost of a wrong refusal (removal appears
# broken) is paid on every photo the seller takes, the cost of a wrong accept
# is one photo they can see and revert.
BOUNDARY_BAND_PX = int(os.getenv("CUTOUT_BOUNDARY_BAND", "4") or 4)
MIN_EDGE_RATIO = float(os.getenv("CUTOUT_MIN_EDGE_RATIO", "1.35") or 1.35)
MIN_COLOUR_GAP = float(os.getenv("CUTOUT_MIN_COLOUR_GAP", "10.0") or 10.0)
# Brightness is damped for the same reason _colour_gap damps it: a contact
# shadow is the backdrop's own colour with the light taken out, and at full
# weight it reads as a different material.
BOUNDARY_LUMA_WEIGHT = float(os.getenv("CUTOUT_BOUNDARY_LUMA_WEIGHT", "0.35") or 0.35)


def grow(mask: np.ndarray, width: int) -> np.ndarray:
    """Dilate `mask` by `width` px, 4-connected, in plain NumPy.

    Deliberately not matte.dilate: that one degrades to the identity without
    SciPy, which is the safe default for a refinement but would silently
    collapse the sampling bands below to nothing. A rule that decides whether
    to ship a cutout has to give the same answer on every machine.
    """
    out = mask.copy()
    for _ in range(max(0, int(width))):
        nxt = out.copy()
        nxt[1:] |= out[:-1]
        nxt[:-1] |= out[1:]
        nxt[:, 1:] |= out[:, :-1]
        nxt[:, :-1] |= out[:, 1:]
        if nxt.sum() == out.sum():
            break
        out = nxt
    return out


def boundary_support(rgb: np.ndarray, edges: np.ndarray, kept: np.ndarray,
                     band: int = BOUNDARY_BAND_PX,
                     luma_weight: float = BOUNDARY_LUMA_WEIGHT) -> dict:
    """Evidence, from the photo itself, that `kept`'s outline is a real edge.

    `rgb` is float (h, w, 3), `edges` the photo's per-pixel gradient magnitude
    (h, w), `kept` the boolean subject mask — all at the same scale.

    Returns the two measurements plus `supported`. `supported` is True when
    the answer is unknowable (no cut in frame, nothing to compare against):
    an inconclusive test must not be the thing that deletes a good cutout.
    """
    metrics = {"edge_ratio": None, "colour_gap": None, "supported": True}
    removed = ~kept
    if not kept.any() or not removed.any():
        return metrics
    # The two sides of the cut, and the removed region's own grain well away
    # from it (2*band, so the cut's own gradient can't contaminate the
    # baseline it is being measured against).
    inner = kept & grow(removed, band)
    outer = removed & grow(kept, band)
    ambient = removed & ~grow(kept, 2 * band)
    if not inner.any() or not outer.any():
        return metrics

    cut = inner | outer
    if ambient.any():
        base = float(edges[ambient].mean())
        # A perfectly flat backdrop has no grain to measure; any real edge
        # beats it, so floor the baseline rather than dividing by ~0.
        ratio = float(edges[cut].mean()) / max(base, 1e-3)
        metrics["edge_ratio"] = round(ratio, 3)

    in_mean = rgb[inner].mean(axis=0)
    out_mean = rgb[outer].mean(axis=0)
    in_luma, out_luma = float(in_mean.mean()), float(out_mean.mean())
    in_chroma, out_chroma = in_mean - in_luma, out_mean - out_luma
    gap = float(np.sqrt(luma_weight * (in_luma - out_luma) ** 2
                        + float(((in_chroma - out_chroma) ** 2).sum())))
    metrics["colour_gap"] = round(gap, 2)

    if metrics["edge_ratio"] is None:
        return metrics  # nothing to measure the silhouette against — keep it
    metrics["supported"] = bool(metrics["edge_ratio"] >= MIN_EDGE_RATIO
                                or gap >= MIN_COLOUR_GAP)
    return metrics

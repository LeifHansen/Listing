"""Image optimization tuned for eBay listing photos.

eBay recommends square-ish images with the longest side >= 1600px for zoom,
clean framing, and good lighting.

The pipeline (per photo): auto-orient -> background removal + studio effect
(the cutout composited on white with our soft contact shadow, then the studio
enhancement pass) -> subject-aware square crop -> resize to target -> save.

Whatever engine produces the matte, the local one's silhouette is solidified
before anything is composited (_solidify_border): the border is closed into one
continuous outline, floating crumbs are dropped, and the outline is then snapped
onto the item's real edge using the photo itself. A cutout is only ever as good
as that boundary — every visible failure (bites out of a hem, a fringe of the
old backdrop, an item shedding fragments) lives there.

Which remover runs is config.bg_engine_chain() (BG_ENGINE): by default the
budget Pixian.ai API when its keys are present, otherwise the in-house rembg
model — so the module works with no external service at all. The pricey
engines (Photoroom, and Adobe's Lightroom-preset + Photoshop-cutout combo)
run only when BG_ENGINE explicitly selects them.
"""
from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path
from typing import Optional

from PIL import (Image, ImageEnhance, ImageFile, ImageFilter, ImageOps,
                 ImageStat)

from .. import config
from ..config import log
from ..storage import natural_key
from . import orient

# Phone uploads over flaky connections arrive missing their last few bytes
# surprisingly often ("image file is truncated (N bytes not processed)").
# Decode what's there instead of raising — a photo missing a sliver beats a
# failed batch, and unreadable files are still skipped by their callers.
ImageFile.LOAD_TRUNCATED_IMAGES = True

# iPhone/Mac photos are HEIC by default; register the decoder if available so
# uploads don't fail. Falls back gracefully if the package isn't installed.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # noqa: BLE001
    pass

TARGET_SIZE = 1600  # px, longest side per eBay zoom recommendation
JPEG_QUALITY = 88
CANVAS_COLOR = (248, 248, 248)  # near-white, looks clean on eBay
WHITE = (255, 255, 255)  # pure white when the user asks to strip the background
# Downscale anything larger than this (longest side) up front: a 150MP phone
# panorama is under the 20MB byte cap but makes several full-res RGB copies
# (~1GB+) during autocrop/pad and can OOM a small machine. We only ever output
# TARGET_SIZE, so working above ~2x that buys nothing.
MAX_WORK_SIDE = 3200


def _flatten(img: Image.Image) -> Image.Image:
    """Return an RGB image, compositing any alpha onto the near-white canvas
    instead of dropping it. A plain `.convert("RGB")` discards transparency and
    leaves formerly-transparent pixels black; product cutout PNGs need the
    alpha composited so the background reads as the clean canvas, not black."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, CANVAS_COLOR + (255,))
        canvas.alpha_composite(rgba)
        return canvas.convert("RGB")
    return img.convert("RGB")

# In-house background removal — rembg (ONNX U^2-Net family) running in-process,
# no external service. rembg loads its model on first use; keep the session
# process-global so we pay that cost once, and only when background removal is
# actually used.
#
# Model choice is a hard memory ceiling. Empirically:
#   - "u2netp" (~4MB) is light and stable even on a 2GB machine, but weak on
#     dark/black or low-contrast items (can mangle them). It's the code
#     default so dev machines and small boxes always work.
#   - "u2net" (full, ~176MB) and "isnet-general-use" (1024x1024) need a 4GB+
#     machine — on 2GB they OOM-kill the box. isnet has the best edges and is
#     what production runs (fly.toml sets REMBG_MODEL=isnet-general-use with a
#     4GB VM) now that the local model is the default no-per-image-cost engine.
_REMBG_MODEL = os.getenv("REMBG_MODEL", "u2netp").strip() or "u2netp"
_rembg_session = None

# Harden the soft matte into a near-binary mask so the background is fully
# gone, not faintly visible. Alpha below _ALPHA_LOW is forced to background,
# above _ALPHA_HIGH to solid subject; the thin band between keeps a 1-2px
# anti-aliased edge. A soft matte (mid-alpha everywhere) is what leaves a
# "ghost" of the old background compositing through as gray fuzz.
_ALPHA_LOW = 90
_ALPHA_HIGH = 170
# If the cutout keeps less than this fraction of the frame as opaque subject,
# the model ate the item (dark denim, close-up textures, low contrast) — keep
# the original photo rather than saving a destroyed one.
_MIN_FG_COVERAGE = 0.045
# When the removed (background) region is both large (>50% of the frame) and
# darker than this mean luminance, it's almost certainly a dark ITEM the model
# mistook for background (not a real backdrop, which is white/neutral) — keep
# the original. Tunable via DARK_BG_LUMA.
_DARK_BG_LUMA = int(os.getenv("DARK_BG_LUMA", "70") or "70")
# Cap the resolution the background model actually runs at. isnet on a full
# 1600px image thrashes memory on a 2GB machine and can hang a bulk job for
# minutes; running on a smaller copy is fast and light, and the resulting mask
# upscales cleanly (product cutouts don't need pixel-perfect edges). Override
# with REMBG_MAX_SIDE.
_REMBG_MAX_SIDE = int(os.getenv("REMBG_MAX_SIDE", "640") or "640")
# The photo-studio 'Remove background' can run the matte at a higher resolution
# for crisper edges — BUT a 1024px isnet inference peaks well over what the 2GB
# machine has, so the worker gets OOM-killed (the 502 users hit) before any
# Python-level fallback can run. Default to the memory-safe bulk size; raise
# REMBG_STUDIO_MAX_SIDE (e.g. to 1024) only on a machine with more RAM (4GB+).
_STUDIO_MAX_SIDE = int(os.getenv("REMBG_STUDIO_MAX_SIDE", str(_REMBG_MAX_SIDE))
                       or str(_REMBG_MAX_SIDE))
# Serialize model inference: two isnet runs at once (e.g. a studio cutout during
# a bulk background-removal batch) would double peak memory and OOM the machine.
_INFER_LOCK = threading.Lock()
# Draw a soft contact shadow under the cut-out subject so the white-background
# result still reads like a studio product shot. Pure Pillow, no external API.
# Disable with BG_SHADOW=off.
_BG_SHADOW = os.getenv("BG_SHADOW", "on").strip().lower() not in (
    "off", "0", "false", "no", "none", "")
# Re-attach interior holes the matte punches through the middle of the item
# (see _fill_interior_holes). Off switch: BG_FILL_HOLES=off.
_FILL_HOLES = os.getenv("BG_FILL_HOLES", "on").strip().lower() not in (
    "off", "0", "false", "no", "none", "")
# How far (RGB euclidean distance) an enclosed removed region has to sit from
# the backdrop we actually removed before we call it item rather than a real
# see-through gap. ~26 per channel: comfortably above JPEG noise and backdrop
# vignetting, well below a printed graphic or a glossy panel.
_HOLE_BG_TOL = float(os.getenv("BG_HOLE_TOLERANCE", "45") or "45")
# Hole analysis runs on a downscaled copy: the regions we're looking for are
# blobs, not hairlines, and 512px keeps a 1600px photo at a few milliseconds.
# Scaling down can only bridge a thin subject wall (which makes a hole read as
# ordinary background and simply not get filled), so it never invents a fill.
_HOLE_SCAN_SIDE = 512
# Safety valve on the flood: each sweep propagates in straight lines, so a
# region needing more than this many direction changes to reach the frame is
# treated as enclosed. Real photos converge in a handful.
_FLOOD_SWEEPS = 24

# --- border solidification --------------------------------------------------
# Before anything gets composited, the raw matte's boundary is consolidated
# into one solid, continuous silhouette that follows the item's real edge (see
# _solidify_border). This is what stops the "chewed" cutouts: denim hems, thin
# straps and low-contrast edges come back from the model as a ragged, pitted
# outline, and compositing that straight onto white saws visible bites out of
# the product. Off switch: BG_SOLIDIFY=off (falls back to the old plain erode).
_SOLIDIFY = os.getenv("BG_SOLIDIFY", "on").strip().lower() not in (
    "off", "0", "false", "no", "none", "")
# Border work runs on a downscaled copy — the decisions are shape-scale, not
# pixel-scale, and 768px keeps a 1600px photo at a few milliseconds. The mask
# comes back up bilinearly, which also gives the edge its anti-aliasing.
_SOLIDIFY_SCAN_SIDE = int(os.getenv("BG_SOLIDIFY_SCAN_SIDE", "768") or "768")
# Gap-bridging radius as a fraction of the short side: a morphological close
# with this sigma seals notches and pinholes up to ~2x its width and smooths
# the staircase left by upscaling a small matte. 0.6% of 1600px ~ 10px, which
# is a frayed hem — well under any real gap between, say, two shoes.
_EDGE_CLOSE_FRAC = float(os.getenv("BG_EDGE_CLOSE", "0.006") or "0.006")
# How far the border may travel, in or out, as a fraction of the short side
# (4% of 1600px = 64px). Every step is gated on the photo's own colours, so
# this is a ceiling on how much of a mangled edge can be rebuilt, not a
# distance the border will actually move.
_EDGE_SNAP_FRAC = float(os.getenv("BG_EDGE_SNAP", "0.04") or "0.04")
# Colour distance from the measured backdrop at which a pixel stops being
# backdrop and starts being item, in the luma-damped space below.
_EDGE_SNAP_TOL = float(os.getenv("BG_EDGE_TOLERANCE", "24") or "24")
# Brightness counts less than colour when deciding backdrop vs item: a contact
# shadow is the backdrop's own colour turned down, so weighting luma equally
# would read it as "not the backdrop" and grow the item into its own shadow.
_LUMA_WEIGHT = float(os.getenv("BG_LUMA_WEIGHT", "0.35") or "0.35")
# Refuse a snap that inflates the silhouette by more than this share of itself
# — that is the item leaking into scenery, not a border being recovered.
_MAX_GROWTH = float(os.getenv("BG_MAX_GROWTH", "0.35") or "0.35")
# Growth stops at any edge in the photo stronger than this share of the
# backdrop-to-item contrast. The item's own outline is the strongest edge
# around it, so the border lands on it instead of wandering past — and the
# soft edge where a cast shadow meets the item stops the border there too,
# which is what keeps the item from dragging its shadow onto the white canvas.
_EDGE_BLOCK_FRAC = float(os.getenv("BG_EDGE_BLOCK", "0.4") or "0.4")
# How many pixels the border may reclaim through backdrop-coloured shade.
_SHADE_REACH = int(os.getenv("BG_SHADE_REACH", "2") or "2")
# ...and how dim a pixel can get before it's too dark to be the backdrop in
# shadow at all, as a fraction of the backdrop's brightness. Below this it's
# treated as item, so black jeans on a white sweep still get their border back.
_SHADE_FLOOR = float(os.getenv("BG_SHADE_FLOOR", "0.5") or "0.5")
# A backdrop this varied (mean per-channel std) is a cluttered scene, not a
# sweep — there the "off-backdrop means item" inference doesn't hold, so the
# border is only trimmed inward, never grown.
_BUSY_BACKDROP_STD = float(os.getenv("BG_BUSY_BACKDROP", "34") or "34")
# Detached crumbs smaller than this share of the biggest kept region are matte
# noise (dust, a shadow edge, a speck of backdrop texture), not a second item.
# Generous by design: a shoe next to its pair, or a tag beside a garment, is
# tens of percent of the main blob and always survives.
_SPECK_FRAC = float(os.getenv("BG_SPECK_FRAC", "0.02") or "0.02")


def _blur_threshold(mask: Image.Image, sigma: float, cut: int) -> Image.Image:
    """Binary morphology via blur + threshold: cut < 128 dilates the mask by
    about `sigma`, cut > 128 erodes it by about the same, and both smooth the
    contour on the way. Far cheaper than a rank filter at these radii, and the
    radius is a float so it can scale with the photo."""
    if sigma <= 0:
        return mask
    return mask.filter(ImageFilter.GaussianBlur(sigma)).point(
        lambda a: 255 if a >= cut else 0)


def _close_mask(mask: Image.Image, sigma: float) -> Image.Image:
    """Morphological close: grow then shrink, which fills gaps, notches and
    pinholes up to ~2*sigma across while leaving the silhouette's size and its
    convex corners essentially where they were."""
    return _blur_threshold(_blur_threshold(mask, sigma, 40), sigma, 215)


def _drop_specks(arr):
    """Delete detached crumbs from a boolean subject mask, keeping every region
    big enough to be a real part of the listing. No-op without scipy (it rides
    in with rembg) or when there's only one region."""
    import numpy as np

    try:
        from scipy import ndimage
    except Exception:  # noqa: BLE001 - cleanup is a bonus, never a requirement
        return arr
    labels, count = ndimage.label(arr)
    if count <= 1:
        return arr
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    keep = sizes >= max(_SPECK_FRAC * sizes.max(), 0.0008 * arr.size)
    keep[0] = False
    return keep[labels]


def _colour_gap(luma, chroma, reference, luma_weight: Optional[float] = None):
    """Distance from every pixel to one reference colour, given the pixels
    already split into brightness and colour (see _colour_split), with
    brightness damped by `luma_weight` (_LUMA_WEIGHT by default; 0 compares
    colour alone, ignoring how light or dark the pixel is).

    Plain RGB distance can't tell a shadow from a different material: a contact
    shadow is the backdrop's own colour with the light taken out, and at full
    luma weight it reads as far from the backdrop as the item does. Comparing
    mostly on colour and only a little on brightness keeps shadows, vignetting
    and uneven lighting on the backdrop's side of the line."""
    import numpy as np

    weight = _LUMA_WEIGHT if luma_weight is None else luma_weight
    ref_luma = float(np.mean(reference))
    ref_chroma = np.asarray(reference, dtype=np.float32) - ref_luma
    return np.sqrt(weight * (luma - ref_luma) ** 2
                   + ((chroma - ref_chroma) ** 2).sum(axis=-1))


def _colour_split(rgb):
    """Split pixels into (brightness, colour-without-brightness) once, so the
    several comparisons a border decision needs don't each redo it."""
    luma = rgb.mean(axis=-1)
    return luma, rgb - luma[..., None]


def _edge_strength(src: Image.Image):
    """Per-pixel edge magnitude of a photo, mildly blurred first so fabric
    weave and sensor noise don't register as boundaries."""
    import numpy as np

    sm = np.asarray(src.filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32)
    gy, gx = np.zeros_like(sm), np.zeros_like(sm)
    gy[1:-1] = (sm[2:] - sm[:-2]) * 0.5
    gx[:, 1:-1] = (sm[:, 2:] - sm[:, :-2]) * 0.5
    return np.sqrt((gx ** 2 + gy ** 2).sum(axis=-1))


def _grow_within(seed, allowed, reach: int):
    """Flood `seed` outward through `allowed` for at most `reach` pixels — a
    constrained dilation, so the silhouette can only expand across pixels the
    photo agrees are item, and only near where it already was.

    scipy does this in C; the numpy fallback is the same 4-connected expansion
    written out, which at scan resolution is a few milliseconds."""
    import numpy as np

    try:
        from scipy import ndimage
        return ndimage.binary_dilation(seed, np.ones((3, 3), bool),
                                       iterations=reach, mask=allowed)
    except Exception:  # noqa: BLE001 - fall back to plain numpy
        cur = seed.copy()
        for _ in range(reach):
            nxt = cur.copy()
            nxt[1:, :] |= cur[:-1, :]
            nxt[:-1, :] |= cur[1:, :]
            nxt[:, 1:] |= cur[:, :-1]
            nxt[:, :-1] |= cur[:, 1:]
            nxt &= allowed
            nxt |= seed
            if nxt.sum() == cur.sum():
                break
            cur = nxt
        return cur


def _solidify_border(alpha: Image.Image, source: Image.Image,
                     out_size: Optional[tuple[int, int]] = None,
                     ) -> Optional[Image.Image]:
    """Consolidate a raw matte into one solid silhouette whose border sits on
    the item's actual edge. Returns None when it can't run (disabled, no numpy,
    degenerate mask) so the caller falls back to the plain erode.

    Three passes, in order:

    1. **Close** the mask: bridge the notches, pinholes and hairline splits the
       model leaves along fabric edges, and smooth the staircase that upscaling
       a 640-1024px matte to 1600px bakes into the contour. This alone is most
       of the "solid border" — a cutout can only look clean if its outline is
       continuous.
    2. **Drop detached crumbs** so the result is the item, not the item plus a
       confetti of speckles floating on the white canvas.
    3. **Snap the border to the photo.** The matte's edge is only approximate,
       so measure the two colours that actually meet there — the backdrop (well
       outside the mask) and the item (well inside it) — and let the border
       travel, in both directions, through whichever pixels resemble which.
       Item the matte shaved off comes back; backdrop fringe it left behind is
       eaten away. This is what fixes a light-wash garment on a white sweep,
       where the model's edge wanders around inside the item and the old
       blanket 1px erode only ever pushed it further in.

    Growing is skipped on a cluttered backdrop, where "not the backdrop colour"
    stops meaning "item"; trimming still runs. Growth also stops at real edges
    in the photo and is capped in total, and if any of it somehow eats the
    subject we return the merely-closed mask rather than a mangled one."""
    if not _SOLIDIFY:
        return None
    try:
        import numpy as np
    except Exception:  # noqa: BLE001 - numpy rides in with onnxruntime
        return None

    out_size = out_size or alpha.size
    w, h = alpha.size
    if min(w, h) < 32:
        return None
    scale = min(1.0, _SOLIDIFY_SCAN_SIDE / max(w, h))
    sw, sh = max(16, round(w * scale)), max(16, round(h * scale))
    small = alpha if (sw, sh) == (w, h) else alpha.resize((sw, sh), Image.BILINEAR)
    side = min(sw, sh)

    closed = _close_mask(small.point(lambda a: 255 if a >= 128 else 0),
                         max(1.0, side * _EDGE_CLOSE_FRAC))
    arr = np.asarray(closed, dtype=np.uint8) >= 128
    if not arr.any() or arr.all():
        return None  # nothing kept, or nothing removed — no border to solidify
    arr = _drop_specks(arr)
    if not arr.any():
        return None

    def _out(mask) -> Image.Image:
        img = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), "L")
        # BILINEAR back up: the ramp it leaves across the edge IS the cutout's
        # anti-aliasing, and unlike LANCZOS it can't overshoot a halo back in.
        return img if img.size == out_size else img.resize(out_size, Image.BILINEAR)

    reach = max(2, round(side * _EDGE_SNAP_FRAC))
    solid = Image.fromarray(np.where(arr, 255, 0).astype(np.uint8), "L")
    outer = (np.asarray(_blur_threshold(solid, reach, 40), dtype=np.uint8) >= 128) & ~arr
    far_bg = ~arr & ~outer  # backdrop well clear of the edge, so it is backdrop
    if arr.sum() < 64 or far_bg.sum() < 64:
        return _out(arr)  # subject fills the frame — no backdrop to compare to

    src = source.convert("RGB")
    if src.size != (sw, sh):
        src = src.resize((sw, sh), Image.BILINEAR)
    rgb = np.asarray(src, dtype=np.float32)
    luma, chroma = _colour_split(rgb)
    backdrop = np.median(rgb[far_bg], axis=0)
    # Sample the item well inside its own edge. Reading it at the rim looks
    # tempting (it is the most local sample) but a matte that overshoots puts
    # leftover backdrop there, and the item would end up "measured" as the very
    # colour we're removing — after which nothing gets trimmed. Step in far
    # enough to clear that overshoot, and back off on a thin item that erodes
    # away at that depth.
    core = arr
    for inset in (reach, reach * 0.5):
        eroded = (np.asarray(_blur_threshold(solid, max(2.0, inset), 215),
                             dtype=np.uint8) >= 128)
        if eroded.sum() > max(64, 0.02 * arr.sum()):
            core = eroded
            break
    item = np.median(rgb[core], axis=0)
    d_bg = _colour_gap(luma, chroma, backdrop)
    d_item = _colour_gap(luma, chroma, item)
    busy = float(rgb[far_bg].std(axis=0).mean())

    # How far apart those two colours actually are decides how picky we can
    # be: on a strong separation, demand a clear difference from the backdrop;
    # on a pale item against a white sweep there is no clear difference to be
    # had, so scale the bar down rather than give up on the border entirely.
    item_luma, item_chroma = _colour_split(item.reshape(1, 1, 3))
    contrast = float(_colour_gap(item_luma, item_chroma, backdrop)[0, 0])
    tol = max(10.0, min(_EDGE_SNAP_TOL, 0.35 * contrast))
    if busy > _BUSY_BACKDROP_STD:
        # A cluttered scene: "not the backdrop colour" no longer means "item",
        # so don't grow at all. Trimming below still runs.
        log.info("bg-removal: backdrop too varied (std=%.0f) to grow the border "
                 "into — trimming only", busy)
        grown = arr
    else:
        # Item, per the photo: clearly off the backdrop's colour, at least as
        # close to the item's, and not on the far side of a real edge. The
        # border only ever advances through these, which is what keeps
        # recovering a hem from turning into a land-grab across the whole
        # photo. (Built only on this branch — the busy-backdrop path never
        # grows, so it never needs the edge field.)
        itemish = ((d_bg > tol) & (d_bg * 1.15 > d_item)
                   & (_edge_strength(src) < max(12.0, _EDGE_BLOCK_FRAC * contrast)))
        # Shade: the backdrop's own colour with some light taken out — same
        # hue, dimmer, but not dramatically dimmer (a cast shadow keeps most of
        # the backdrop's brightness; a genuinely dark item does not). The
        # border may creep a couple of pixels into shade, since a grey item
        # reads the same way and still needs its edge back, but never far
        # enough to drag a whole cast shadow onto the white canvas.
        bg_luma = float(np.mean(backdrop))
        shade = ((_colour_gap(luma, chroma, backdrop, luma_weight=0.0)
                  < 0.3 * contrast)
                 & (luma < bg_luma) & (luma > _SHADE_FLOOR * bg_luma))
        room = outer | arr
        grown = _grow_within(arr, itemish & ~shade & room, reach)
        grown = _grow_within(grown, (itemish & room) | grown, _SHADE_REACH)
        if grown.sum() > arr.sum() * (1.0 + _MAX_GROWTH):
            log.warning("bg-removal: border snap tried to grow the subject by "
                        "%.0f%% — leaking into the scene, keeping the plain mask",
                        100.0 * (grown.sum() / max(1, arr.sum()) - 1))
            grown = arr
    # Trim the other way, symmetrically: let the backdrop eat back into the
    # silhouette wherever the mask is still covering backdrop-coloured pixels
    # that connect to the outside. That clears the halo of old background a
    # generous matte leaves around the item — the job the old unconditional 1px
    # erode was doing, except it shaved the item everywhere, fringe or not.
    backdropish = (d_bg <= tol) & (d_bg < d_item)
    outside = ~grown
    eaten = _grow_within(outside, backdropish | outside, reach) & grown
    snapped = grown & ~eaten
    if snapped.sum() < 0.5 * arr.sum():
        log.warning("bg-removal: border snap would have cut the subject in half "
                    "— keeping the plain solid mask")
        return _out(arr)
    # A last hairline close so the snap's per-pixel decisions read as one edge
    # rather than a dotted one.
    return _out(np.asarray(
        _close_mask(Image.fromarray(np.where(snapped, 255, 0).astype(np.uint8), "L"),
                    max(1.0, side * _EDGE_CLOSE_FRAC * 0.5)), dtype=np.uint8) >= 128)


def _alpha_mask(img_rgb: Image.Image, max_side: Optional[int] = None,
                upscale: bool = True) -> Image.Image:
    """rembg subject alpha (mode L), computed on a copy capped to `max_side`
    (defaults to the fast bulk size) then upscaled back to img_rgb's size.
    A larger max_side feeds the model more detail = crisper edges.

    `upscale=False` returns the matte at the resolution the model actually
    produced. The refinement passes make their decisions at or below that
    scale anyway, so working on the pre-upscale matte does the same work on
    ~10x fewer pixels; _refine_alpha upscales once at the end instead."""
    global _rembg_session
    from rembg import new_session, remove

    cap = max_side or _REMBG_MAX_SIDE
    scale = min(1.0, cap / max(img_rgb.size))
    small = (img_rgb.resize((max(1, round(img_rgb.width * scale)),
                             max(1, round(img_rgb.height * scale))), Image.LANCZOS)
             if scale < 1 else img_rgb)
    # One inference at a time — concurrent runs would stack peak memory and OOM.
    with _INFER_LOCK:
        if _rembg_session is None:
            _rembg_session = new_session(_REMBG_MODEL)
        alpha = remove(small, session=_rembg_session, only_mask=True).convert("L")
    if upscale and alpha.size != img_rgb.size:
        # BILINEAR (not LANCZOS) to upscale the mask: LANCZOS overshoots at
        # high-contrast edges, ringing a faint halo of the old background back
        # in — exactly the "ghost background" the cutout is meant to remove.
        alpha = alpha.resize(img_rgb.size, Image.BILINEAR)
    return alpha


def _refine_alpha(alpha: Image.Image,
                  source: Optional[Image.Image] = None,
                  out_size: Optional[tuple[int, int]] = None) -> Image.Image:
    """Clean the matte into a natural cutout: despeckle, kill the faint
    background ghost, solidify the item's border against the photo, then
    lightly feather the edge so it blends instead of looking stickered-on.

    `source` is the photo the matte came from. With it, the border is
    consolidated and snapped onto the item's real edge (_solidify_border) —
    which is what a background remover has to get right, since every visible
    failure (bites out of a hem, a fringe of old backdrop, floating crumbs)
    lives on that boundary. Without it we fall back to the old blanket 1px
    erode, which trims background fringe at the cost of shaving the item.

    Border-aware: where the subject runs off the frame it has no background
    fringe to trim, so eroding + feathering there just eats a white gutter into
    the item (the 'bleeds off frame' artifact). We restore the hardened,
    un-feathered alpha in a thin band along the frame so an edge-touching
    subject stays solid to the border. Where the subject doesn't reach an edge
    the hardened alpha is 0 there, so those borders are unchanged.

    `out_size` is the size the refined matte must come back at (defaults to
    alpha's own size). Passing the matte at the model's native resolution with
    out_size set to the photo's size runs the despeckle/harden work on ~10x
    fewer pixels; the final edge is the same BILINEAR ramp the full-size path
    produced, since that upscale always happened somewhere in the chain."""
    out_size = out_size or alpha.size
    w, h = out_size
    alpha = alpha.filter(ImageFilter.MedianFilter(3))
    span = max(1, _ALPHA_HIGH - _ALPHA_LOW)
    hardened = alpha.point(lambda a: 0 if a < _ALPHA_LOW
                           else 255 if a > _ALPHA_HIGH
                           else round((a - _ALPHA_LOW) * 255 / span))
    solid = (_solidify_border(hardened, source, out_size=out_size)
             if source is not None else None)
    if solid is not None:
        # The solidified border already decided, per pixel, where the item
        # stops — eroding on top of that would only walk it back inside again.
        hardened = solid
        refined = hardened.filter(ImageFilter.GaussianBlur(0.6))
    else:
        if hardened.size != out_size:
            # Upscale BEFORE the erode so the fallback trims the same ~1px at
            # output scale it always has, not 1px at matte scale (~3px here).
            hardened = hardened.resize(out_size, Image.BILINEAR)
        # Erode 1px to cut the residual background fringe, then a sub-pixel
        # feather so the composited edge is smooth rather than jagged.
        refined = hardened.filter(ImageFilter.MinFilter(3)).filter(
            ImageFilter.GaussianBlur(0.6))
    band = 3
    if w > 2 * band and h > 2 * band:
        refined.paste(hardened.crop((0, 0, w, band)), (0, 0))               # top
        refined.paste(hardened.crop((0, h - band, w, h)), (0, h - band))    # bottom
        refined.paste(hardened.crop((0, 0, band, h)), (0, 0))               # left
        refined.paste(hardened.crop((w - band, 0, w, h)), (w - band, 0))    # right
    return refined


def _flood_sweep(bg, reached, axis: int):
    """One straight-line propagation pass of `reached` through `bg` along
    `axis` (1 = across rows, 0 = down columns).

    Every uninterrupted run of background pixels is one segment; a segment that
    already contains a reached pixel becomes reached end to end. Done as two
    whole-image numpy passes rather than a per-pixel flood, which is what keeps
    this affordable on every photo."""
    import numpy as np

    b = bg if axis == 1 else bg.T
    r = reached if axis == 1 else reached.T
    rows, cols = b.shape
    # A sentinel non-background column so runs can't wrap from one row's end
    # into the next row's start once flattened.
    stop = np.zeros((rows, 1), dtype=bool)
    flat_bg = np.concatenate([b, stop], axis=1).ravel()
    flat_reached = np.concatenate([r, stop], axis=1).ravel()
    seg = np.cumsum(~flat_bg)  # constant within a run of background
    hit = np.zeros(int(seg[-1]) + 2, dtype=bool)
    hit[seg[flat_reached]] = True
    out = (flat_bg & hit[seg]).reshape(rows, cols + 1)[:, :cols]
    return out if axis == 1 else out.T


def _border_connected(bg):
    """Background pixels reachable from the frame border (4-connected) — i.e.
    the real backdrop, as opposed to background-labelled regions sealed inside
    the subject."""
    import numpy as np

    reached = np.zeros_like(bg)
    reached[0, :] = bg[0, :]
    reached[-1, :] = bg[-1, :]
    reached[:, 0] = bg[:, 0]
    reached[:, -1] = bg[:, -1]
    for _ in range(_FLOOD_SWEEPS):
        before = int(reached.sum())
        reached = _flood_sweep(bg, reached, axis=1)
        reached = _flood_sweep(bg, reached, axis=0)
        if int(reached.sum()) == before:
            break
    return reached


def _interior_fill_mask(alpha: Image.Image,
                        source: Image.Image) -> Optional[Image.Image]:
    """Mask (mode L) of the pixels an alpha matte punched out of the middle of
    the subject and that should be put back — None when there's nothing to fix.

    This is the failure sellers describe as "it removed the inside of my item":
    the model calls a printed graphic, a bright panel, a glossy patch or a
    reflective face INSIDE the product 'background', so the composite shows
    white holes straight through the item.

    A removed region completely sealed in by subject is one of exactly two
    things:
      * a genuine see-through gap (a mug handle, the space between shoe
        straps, the hole in a doughnut) — which shows the SAME backdrop we just
        removed, or
      * item the matte ate — which does not.
    So: flood the background inward from the frame border, and put back every
    stranded leftover whose pixels don't look like the backdrop. Keeping the
    backdrop-coloured ones removed is what stops mug handles filling in — and
    when the call is wrong there, the region matched white-ish backdrop anyway,
    so on the white canvas it's invisible either way."""
    if not _FILL_HOLES:
        return None
    try:
        import numpy as np
    except Exception:  # noqa: BLE001 - numpy rides in with onnxruntime; if it's
        # somehow absent, skip the repair rather than fail a photo.
        return None

    w, h = alpha.size
    if w < 8 or h < 8:
        return None
    scale = min(1.0, _HOLE_SCAN_SIDE / max(w, h))
    sw, sh = max(4, round(w * scale)), max(4, round(h * scale))
    small = alpha if (sw, sh) == (w, h) else alpha.resize((sw, sh), Image.BILINEAR)
    bg = np.asarray(small, dtype=np.uint8) < 128
    if not bg.any() or bg.all():
        return None  # nothing removed, or nothing kept — no interior to repair
    reached = _border_connected(bg)
    holes = bg & ~reached
    if not holes.any():
        return None

    src = source.convert("RGB")
    if src.size != (sw, sh):
        src = src.resize((sw, sh), Image.BILINEAR)
    rgb = np.asarray(src, dtype=np.int16)
    # The backdrop we actually removed, not a guess: the median colour of the
    # border-connected background (median, so a dark table edge or a shadow in
    # the corner doesn't drag the reference).
    backdrop = (np.median(rgb[reached], axis=0) if reached.any()
                else np.array([255, 255, 255], dtype=np.int16))
    off_backdrop = np.sqrt(((rgb - backdrop) ** 2).sum(axis=2))
    keep = holes & (off_backdrop > _HOLE_BG_TOL)
    if not keep.any():
        return None

    fill = Image.fromarray(np.where(keep, 255, 0).astype(np.uint8), "L")
    # Median: drop isolated speckle so we restore regions, not confetti.
    # MaxFilter: grow a pixel outward so the restored region meets the
    # surrounding subject instead of leaving a hairline seam at the hole's rim.
    fill = fill.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.MaxFilter(3))
    grown = (np.asarray(fill, dtype=np.uint8) >= 128) & ~reached  # never spill
    if not grown.any():                                          # into the backdrop
        return None
    log.info("bg-removal: restored %.1f%% of the frame the matte had punched out "
             "of the item's interior", 100.0 * grown.sum() / grown.size)
    out = Image.fromarray(np.where(grown, 255, 0).astype(np.uint8), "L")
    return out if out.size == alpha.size else out.resize(alpha.size, Image.BILINEAR)


def _fill_interior_holes(alpha: Image.Image,
                         source: Image.Image) -> tuple[Image.Image, Optional[Image.Image]]:
    """Alpha with interior holes re-attached, plus the mask of what was put
    back (None when the matte had no interior holes worth fixing) so callers
    can source those pixels from the original photo."""
    added = _interior_fill_mask(alpha, source)
    if added is None:
        return alpha, None
    from PIL import ImageChops
    return ImageChops.lighter(alpha, added), added


def _compose_on_white(rgb: Image.Image, alpha: Image.Image,
                      shadow: bool = True, source: Optional[Image.Image] = None,
                      fill_holes: bool = True) -> Image.Image:
    """Composite the subject (given by `alpha`) onto pure white, optionally
    with a soft drop shadow drawn from the subject silhouette.

    Interior holes in `alpha` are repaired first (see _fill_interior_holes)
    unless the caller already did it. `source` is the original photo, used both
    to judge those holes and to supply their pixels — a remote engine's cutout
    can carry anything under its transparent areas, so restored regions come
    from the photo itself. Pass fill_holes=False when the alpha is already
    repaired."""
    if fill_holes:
        src = source if source is not None else rgb
        alpha, added = _fill_interior_holes(alpha, src)
        if added is not None and source is not None and source is not rgb:
            patch = source.convert("RGB")
            if patch.size != rgb.size:
                patch = patch.resize(rgb.size, Image.LANCZOS)
            rgb = rgb.copy()
            rgb.paste(patch, (0, 0), added)
    w, h = rgb.size
    canvas = Image.new("RGB", (w, h), WHITE)
    if shadow:
        # Shadow = the silhouette, blurred, nudged down/right, at low opacity —
        # a subtle contact shadow that grounds the item on the white backdrop.
        # It's low-frequency by construction (a ~1.5%-of-frame blur), so render
        # it on a small copy and upscale — visually identical at ~1/10 the cost
        # of blurring the full-size mask.
        blur = max(4, round(min(w, h) * 0.015))
        off = max(2, round(min(w, h) * 0.010))
        scale = min(1.0, 800 / max(w, h))
        if scale < 1.0:
            sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
            shadow_a = alpha.resize((sw, sh), Image.BILINEAR).filter(
                ImageFilter.GaussianBlur(max(1.0, blur * scale))).point(
                lambda a: int(a * 0.40)).resize((w, h), Image.BILINEAR)
        else:
            shadow_a = alpha.filter(ImageFilter.GaussianBlur(blur)).point(
                lambda a: int(a * 0.40))
        shifted = Image.new("L", (w, h), 0)
        shifted.paste(shadow_a, (off, off))
        canvas.paste(Image.new("RGB", (w, h), (55, 55, 55)), (0, 0), shifted)
    canvas.paste(rgb, (0, 0), alpha)
    return canvas


def _cutout_on_white(img: Image.Image, max_side: Optional[int] = None,
                     dark_guard: bool = True) -> Optional[Image.Image]:
    """In-house rembg cutout composited on pure white (with a soft drop shadow
    unless BG_SHADOW=off) — or None when the result is clearly a failure
    (subject erased), so callers keep the original photo instead of saving a
    destroyed one. `max_side` caps the matte resolution (higher = crisper).

    `dark_guard` catches the classic 'item bleeds off frame' failure (see below)
    by bailing to None — right for the automatic path, which would otherwise
    silently save a mangled cutout. The photo studio passes dark_guard=False:
    the seller is reviewing the result and can Revert, so it's better to SHOW
    the cutout than to hard-fail with an error."""
    from PIL import ImageStat

    rgb = img.convert("RGB")
    # Refine the matte at the resolution the model produced it (upscale=False):
    # every decision in _refine_alpha/_solidify_border happens at or below that
    # scale anyway, so the old full-size median/harden was ~10x the pixels for
    # the same answer. out_size brings the finished matte back to photo size.
    alpha = _refine_alpha(_alpha_mask(rgb, max_side=max_side, upscale=False),
                          source=rgb, out_size=rgb.size)
    # Put back anything the matte punched out of the item's middle BEFORE the
    # guards below: a matte that ate the interior and left a rim reads as
    # "subject nearly erased" (or as a big dark removed area), and bailing out
    # there would throw away a cutout that's fine once repaired.
    alpha, _ = _fill_interior_holes(alpha, rgb)
    total = max(1, alpha.width * alpha.height)
    opaque = sum(alpha.histogram()[128:])
    if opaque / total < _MIN_FG_COVERAGE:
        log.warning("bg-removal: subject nearly erased (%.1f%% kept) — keeping original",
                    100 * opaque / total)
        return None
    # Dark-background guard: a dark garment that fills the frame gets called
    # 'background', leaving only its bright printed graphic. A removed region
    # that's both LARGE and DARK is almost never a real backdrop (those are
    # white/neutral), so keep the original rather than a garment reduced to its
    # logo. A normal white/neutral backdrop is light, so this never fires on
    # good cutouts.
    if dark_guard and (total - opaque) / total > 0.5:
        bg_mask = alpha.point(lambda a: 255 if a < 128 else 0)
        stat = ImageStat.Stat(rgb.convert("L"), mask=bg_mask)
        if stat.count[0] and stat.mean[0] < _DARK_BG_LUMA:
            log.warning("bg-removal: removed area large & dark (mean L=%.0f) — likely ate a "
                        "dark item, keeping original", stat.mean[0])
            return None
    return _compose_on_white(rgb, alpha, shadow=_BG_SHADOW, fill_holes=False)


def _apply_studio(img: Image.Image) -> tuple[Image.Image, bool, Optional[str]]:
    """Lightroom "studio" develop preset via the Adobe API.

    Returns (image, applied, error). Keep-the-photo guarantee: any failure
    returns the ORIGINAL image plus the reason, so a batch never loses a shot
    to a network blip or an out-of-credits account.
    """
    try:
        from . import adobe
        return adobe.apply_studio(img), True, None
    except ValueError as exc:  # AdobeError — carries the user-facing reason
        log.warning("lightroom studio: %s — continuing with the unedited photo", exc)
        return img, False, str(exc)
    except Exception as exc:  # noqa: BLE001 - a photo must never fail optimize()
        log.warning("lightroom studio: unexpected error (%s) — continuing with "
                    "the unedited photo", exc)
        return img, False, f"Studio preset failed: {exc}"


def _adobe_cutout(img: Image.Image) -> Optional[Image.Image]:
    """Photoshop Remove Background cutout composited on white (with our soft
    shadow). Returns None only when Adobe isn't ready; when it is, a failure
    raises AdobeError with the actual reason — same loud-failure contract as
    Photoroom below."""
    if not config.adobe_ready():
        return None
    from . import adobe
    cut = adobe.remove_background(img)
    alpha = cut.split()[3]
    if alpha.getbbox() is None:  # nothing kept — no subject found
        raise adobe.AdobeError("Adobe couldn't find a subject in this photo.")
    return _compose_on_white(cut.convert("RGB"), alpha, shadow=_BG_SHADOW, source=img)


class PhotoroomError(ValueError):
    """Photoroom is configured but the call failed — carries a user-facing
    reason. Subclasses ValueError so the studio route's error mapping (422 +
    message in a toast) surfaces the real cause instead of a silent fallback."""


class PixianError(ValueError):
    """Pixian is configured but the call failed — carries a user-facing
    reason. A ValueError for the same reason PhotoroomError is."""


# Cap what we send a remote engine: the final output is always TARGET_SIZE, so
# a full 3200px working copy just wastes upload time and RAM — and on a
# 250-photo batch those add up to minutes and hundreds of MB. 2048px keeps a
# comfortable margin above the 1600px output.
_REMOTE_MAX_SIDE = int(os.getenv("BG_API_MAX_SIDE",
                                 os.getenv("PHOTOROOM_MAX_SIDE", "2048"))
                       or "2048")
# Total attempts per photo. Big batches WILL trip an API's per-minute rate
# limit and the odd network blip; those retry with backoff instead of failing
# the photo. Hard failures (bad key, out of credits) never retry — they'd fail
# identically every time.
_REMOTE_TRIES = int(os.getenv("BG_API_TRIES",
                              os.getenv("PHOTOROOM_TRIES", "4")) or "4")


def _api_backoff(resp, attempt: int) -> float:
    """Seconds to wait before retrying: the service's own Retry-After when it
    sends one, else 2s/4s/8s..., capped so a stuck batch photo can't stall a
    worker slot for long."""
    retry_after = (resp.headers.get("retry-after") or "").strip() if resp is not None else ""
    if retry_after.isdigit():
        return min(30.0, max(1.0, float(retry_after)))
    return min(15.0, float(2 ** attempt))


def _post_with_retries(name: str, send, exc_cls: type):
    """Run `send()` (an httpx POST) with retries on 429/5xx/network errors.
    Returns the final response; raises `exc_cls` when the service stayed
    unreachable through every attempt."""
    resp = None
    for attempt in range(1, _REMOTE_TRIES + 1):
        try:
            resp = send()
        except Exception as exc:  # noqa: BLE001 - network/timeout
            if attempt == _REMOTE_TRIES:
                raise exc_cls(f"Couldn't reach {name}: {exc}") from exc
            wait = _api_backoff(None, attempt)
            log.info("%s: network error (%s) — retry %d/%d in %.0fs",
                     name, exc, attempt, _REMOTE_TRIES - 1, wait)
            time.sleep(wait)
            continue
        if (resp.status_code == 429 or resp.status_code >= 500) \
                and attempt < _REMOTE_TRIES:
            wait = _api_backoff(resp, attempt)
            log.info("%s: HTTP %d — retry %d/%d in %.0fs",
                     name, resp.status_code, attempt, _REMOTE_TRIES - 1, wait)
            time.sleep(wait)
            continue
        break
    return resp


def _jpeg_payload(img: Image.Image) -> bytes:
    """The JPEG bytes a remote engine gets, capped to _REMOTE_MAX_SIDE."""
    from io import BytesIO
    rgb = img.convert("RGB")
    if max(rgb.size) > _REMOTE_MAX_SIDE:
        rgb.thumbnail((_REMOTE_MAX_SIDE, _REMOTE_MAX_SIDE), Image.LANCZOS)
    buf = BytesIO()
    rgb.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _pixian_cutout(img: Image.Image) -> Optional[Image.Image]:
    """Cut out the subject via Pixian.ai — the budget engine, around a tenth
    of Photoroom's per-image price — and composite it on white with our soft
    shadow. Returns None only when no credentials are configured; a real
    failure raises PixianError with the actual reason."""
    if not config.pixian_ready():
        return None
    from io import BytesIO
    import httpx
    payload = _jpeg_payload(img)
    resp = _post_with_retries("pixian", lambda: httpx.post(
        "https://api.pixian.ai/api/v2/remove-background",
        auth=(config.PIXIAN_API_ID, config.PIXIAN_API_SECRET),
        files={"image": ("image.jpg", payload, "image/jpeg")},
        data={"test": "true"} if config.PIXIAN_TEST else {},
        timeout=60,
    ), PixianError)
    if resp.status_code in (401, 403):
        raise PixianError(
            "Pixian rejected the API credentials — check the PIXIAN_API_ID and "
            "PIXIAN_API_SECRET secrets on the server.")
    if resp.status_code == 402:
        raise PixianError(
            "The Pixian account is out of credits — top it up at pixian.ai.")
    if resp.status_code == 429:
        raise PixianError(
            "Pixian kept rate-limiting us even after several retries — "
            "try again in a minute.")
    if resp.status_code != 200:
        raise PixianError(f"Pixian error {resp.status_code}: {resp.text[:160]}")
    try:
        cut = Image.open(BytesIO(resp.content)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise PixianError("Pixian returned an unreadable image.") from exc
    alpha = cut.split()[3]
    if alpha.getbbox() is None:  # nothing kept — no subject found
        raise PixianError("Pixian couldn't find a subject in this photo.")
    return _compose_on_white(cut.convert("RGB"), alpha, shadow=_BG_SHADOW, source=img)


def _photoroom_cutout(img: Image.Image) -> Optional[Image.Image]:
    """Cut out the subject via Photoroom's API and composite it on white (with
    our soft shadow). Returns None only when no key is configured; when a key
    IS set, a failure raises PhotoroomError with the actual reason — silent
    fallbacks to the weak local model are exactly how mangled photos kept
    getting saved without anyone knowing why."""
    if not config.photoroom_ready():
        return None
    from io import BytesIO
    import httpx
    payload = _jpeg_payload(img)
    resp = _post_with_retries("photoroom", lambda: httpx.post(
        "https://sdk.photoroom.com/v1/segment",
        headers={"x-api-key": config.PHOTOROOM_API_KEY},
        files={"image_file": ("image.jpg", payload, "image/jpeg")},
        data={"format": "png"},
        timeout=60,
    ), PhotoroomError)
    if resp.status_code in (401, 403):
        raise PhotoroomError(
            "Photoroom rejected the API key — check the PHOTOROOM_API_KEY "
            "secret on the server (it may be missing, mistyped, or a sandbox key).")
    if resp.status_code == 402:
        raise PhotoroomError(
            "The Photoroom account is out of credits — top it up at photoroom.com.")
    if resp.status_code == 429:
        raise PhotoroomError(
            "Photoroom kept rate-limiting us even after several retries — "
            "try again in a minute.")
    if resp.status_code != 200:
        raise PhotoroomError(
            f"Photoroom error {resp.status_code}: {resp.text[:160]}")
    try:
        cut = Image.open(BytesIO(resp.content)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise PhotoroomError("Photoroom returned an unreadable image.") from exc
    alpha = cut.split()[3]
    if alpha.getbbox() is None:  # nothing kept — no subject found
        raise PhotoroomError("Photoroom couldn't find a subject in this photo.")
    return _compose_on_white(cut.convert("RGB"), alpha, shadow=_BG_SHADOW, source=img)


# The remote engines a chain entry can name (adobe and local are special-cased
# where the chain is walked: adobe needs its studio-preset pass, local runs
# in-process).
_REMOTE_CUTOUTS = {"pixian": _pixian_cutout, "photoroom": _photoroom_cutout}


def _studio_and_cutout(
        img: Image.Image) -> tuple[Image.Image, str, Optional[str], bool, Optional[str]]:
    """Background removal + studio treatment for the automatic upload path.
    Returns (image, engine, error, studio_applied, studio_error) where engine
    is the chain member that produced the cutout ('pixian', 'photoroom',
    'adobe', 'local') or 'none' (kept the original).

    The engines and their order come from config.bg_engine_chain() (BG_ENGINE):
    by default the budget Pixian API when configured (with the local model
    behind it), otherwise local alone — the pricey Photoroom/Adobe engines run
    only when BG_ENGINE explicitly picks them. Each engine's cutout goes on
    white with our soft shadow; the caller's enhancement pass supplies the
    studio polish (Adobe additionally runs its Lightroom preset first).

    When every engine in the chain fails, keep the ORIGINAL photo and surface
    the last reason — a busy background is always better than a shredded item."""
    last_err: Optional[str] = None
    studio_applied, studio_error = False, None
    for engine in config.bg_engine_chain():
        if engine == "local":
            try:
                out = _cutout_on_white(img)
            except Exception as exc:  # noqa: BLE001 - bulk keeps going without the cutout
                log.warning("bg-removal: local cutout failed (%s)", exc)
                out = None
            if out is not None:
                return out, "local", None, studio_applied, studio_error
            last_err = last_err or "cutout failed"
        elif engine == "adobe":
            if not config.adobe_ready():
                continue
            # Lightroom studio preset first so the cutout works on a
            # well-exposed image (keep-the-photo guarantee inside _apply_studio).
            img, studio_applied, studio_error = _apply_studio(img)
            try:
                out = _adobe_cutout(img)
                if out is not None:
                    return out, "adobe", None, studio_applied, studio_error
            except ValueError as exc:  # AdobeError
                log.warning("adobe bg-removal: %s", exc)
                last_err = str(exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("adobe bg-removal: unexpected error (%s)", exc)
                last_err = f"Background removal failed: {exc}"
        else:
            try:
                out = _REMOTE_CUTOUTS[engine](img)
                if out is not None:
                    return out, engine, None, False, None
            except ValueError as exc:  # PixianError/PhotoroomError — mapped reason
                log.warning("%s: %s", engine, exc)
                last_err = str(exc)
            except Exception as exc:  # noqa: BLE001 - keep-original must hold for
                # ANY failure, not just mapped API errors — a photo must never
                # fail optimize().
                log.warning("%s: unexpected error (%s)", engine, exc)
                last_err = f"Background removal failed: {exc}"
    log.warning("bg-removal: keeping the original photo (%s)",
                last_err or "no engine produced a cutout")
    return (_flatten(img), "none", last_err or "background removal failed",
            studio_applied, studio_error)


def remove_background_white(img: Image.Image) -> tuple[Image.Image, str]:
    """Photo-studio 'Remove background'. Returns (image, engine) so the editor
    can name the remover that actually ran. Raises when the cutout genuinely
    fails so the editor can tell the user instead of silently doing nothing.

    Walks the same config.bg_engine_chain() as the automatic path. A failure
    on the chain's LAST engine RAISES (PixianError/PhotoroomError/AdobeError
    are ValueErrors → the editor shows the real reason as a toast) instead of
    silently degrading — a silent fallback is how mangled cutouts kept
    appearing with no clue why. The local model runs at the higher studio
    matte resolution, falling back to the fast size if a big inference runs
    out of memory."""
    chain = config.bg_engine_chain()
    for i, engine in enumerate(chain):
        final = i == len(chain) - 1
        if engine == "local":
            for side in (_STUDIO_MAX_SIDE, _REMBG_MAX_SIDE):
                try:
                    # dark_guard off: the seller reviews the result and can
                    # Revert, so show the cutout rather than hard-failing on a
                    # borderline shot.
                    out = _cutout_on_white(img, max_side=side, dark_guard=False)
                except Exception as exc:  # noqa: BLE001 - retry smaller on OOM/model error
                    log.warning("bg-removal: matte at %dpx failed (%s) — retrying smaller",
                                side, exc)
                    continue
                if out is not None:
                    return out, "local"
                break  # a clean run that erased the subject won't improve when smaller
            break  # nothing worked — fall through to the raise below
        try:
            out = (_adobe_cutout(img) if engine == "adobe"
                   else _REMOTE_CUTOUTS[engine](img))
            if out is not None:
                return out, engine
        except ValueError as exc:  # engine's own user-facing reason
            if final:
                raise
            log.warning("%s: %s — trying the next engine", engine, exc)
    raise ValueError(
        "Couldn't cleanly separate this photo from its background — it's "
        "likely a close-up, dark, or low-contrast shot. Try cropping in "
        "tighter, or paint the background out with the white brush.")


def warm() -> None:
    """Pre-load the rembg model + trigger numba's JIT on a tiny image.

    The very first background removal otherwise pays a ~60-70s one-time cost
    (importing scipy/numba/onnxruntime + JIT). Called in a background thread at
    startup so the machine is reachable immediately while this warms up, and
    real uploads hit a warm (~1s) path."""
    try:
        # Warm the LOCAL model directly (the default engine, and it powers the
        # highlight/subject masks either way) — never via _studio_and_cutout,
        # which would burn a paid API credit on every boot when a remote
        # engine is configured. Warm the mask pipeline only, not the full
        # cutout: a synthetic solid-color square has no subject to keep, and
        # the cutout's failure guard rightly cried "subject nearly erased
        # (0.0% kept)" on it every single boot.
        # A small subject on a backdrop, not a flat tile: the border pass only
        # engages when there's an edge to work on, and warming it here is what
        # keeps scipy's import off the first real photo.
        tile = Image.new("RGB", (64, 64), (235, 235, 235))
        tile.paste(Image.new("RGB", (30, 30), (200, 100, 50)), (17, 17))
        _refine_alpha(_alpha_mask(tile), source=tile)
        log.info("images: background-removal model warmed")
    except Exception as exc:  # noqa: BLE001 - warmup is best-effort
        log.warning(f"images: warmup failed (will lazy-load on first use): {exc}")


def _autocrop_borders(img: Image.Image) -> Image.Image:
    """Trim near-uniform borders (e.g. plain background) around the subject."""
    rgb = _flatten(img)
    # Compare against the top-left corner color as the assumed background.
    bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    from PIL import ImageChops

    diff = ImageChops.difference(rgb, bg)
    bbox = diff.getbbox()
    if not bbox:
        return img
    # Add a small margin so we don't crop too tight.
    left, top, right, bottom = bbox
    margin_x = int((right - left) * 0.03)
    margin_y = int((bottom - top) * 0.03)
    left = max(0, left - margin_x)
    top = max(0, top - margin_y)
    right = min(img.size[0], right + margin_x)
    bottom = min(img.size[1], bottom + margin_y)
    # Only crop if it meaningfully reduces the image.
    if (right - left) < img.size[0] * 0.55 and (bottom - top) < img.size[1] * 0.55:
        return img  # too aggressive; likely not a plain background
    return img.crop((left, top, right, bottom))


def _subject_box(img_rgb: Image.Image) -> Optional[tuple[int, int, int, int]]:
    """Best-effort subject bounding box via a cheap corner-color difference.

    Works well on plain backgrounds and on cutouts-on-white (the corner is the
    background color). Returns None when the box is basically the whole frame
    (busy/textured background) or a tiny speck, so callers fall back to a plain
    center crop instead of a bad guess. No model — fast enough for every photo.
    """
    from PIL import ImageChops

    bg = Image.new("RGB", img_rgb.size, img_rgb.getpixel((0, 0)))
    diff = ImageChops.difference(img_rgb, bg).convert("L")
    mask = diff.point(lambda v: 255 if v > 32 else 0)
    box = mask.getbbox()
    if not box:
        return None
    w, h = img_rgb.size
    bw, bh = box[2] - box[0], box[3] - box[1]
    if (bw > w * 0.92 and bh > h * 0.92) or bw < w * 0.05 or bh < h * 0.05:
        return None  # whole frame (textured bg) or a speck — not a clean subject
    return box


def _border_color(rgb: Image.Image) -> tuple:
    """The image's own backdrop color, averaged from its four corners — pure
    white on a cleaned photo, approximately the table/backdrop otherwise."""
    corners = [rgb.getpixel((x, y)) for x, y in
               ((0, 0), (rgb.width - 1, 0), (0, rgb.height - 1),
                (rgb.width - 1, rgb.height - 1))]
    return tuple(sum(c[i] for c in corners) // 4 for i in range(3))


def _pad_square(crop: Image.Image, pad_color: tuple) -> Image.Image:
    """Center `crop` on a square canvas of its longest side."""
    side = max(crop.size)
    canvas = Image.new("RGB", (side, side), pad_color)
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    return canvas


# A subject longer than this ratio (long side / short side) cannot fit a
# square window without getting chopped — think train-set boxes, skis, bats.
_ELONGATED = 1.35


def _fill_square(img: Image.Image) -> Image.Image:
    """Square-frame the photo around its subject (eBay's recommended shape).

    Normal subjects get a square crop that tightly frames them (plus margin)
    so the item fills the photo — no letterbox bars. ELONGATED subjects are
    the exception: a square window can't contain a long flat box without
    cutting it off (the 'cropping way too much' bug), so those are cropped as
    a rectangle around the whole subject and padded to square with the
    photo's own backdrop color. Whole item beats full frame."""
    rgb = _flatten(img)
    w, h = rgb.size
    box = _subject_box(rgb)
    if box:
        left, top, right, bottom = box
        bw, bh = right - left, bottom - top
        if max(bw, bh) / max(1, min(bw, bh)) > _ELONGATED:
            mx, my = max(6, int(bw * 0.05)), max(6, int(bh * 0.05))
            crop = rgb.crop((max(0, left - mx), max(0, top - my),
                             min(w, right + mx), min(h, bottom + my)))
            return _pad_square(crop, _border_color(rgb))
        cx, cy = (left + right) // 2, (top + bottom) // 2
        # A square big enough for the subject + ~30% breathing room, but never
        # larger than the frame nor a tiny over-zoom.
        want = int(max(bw, bh) * 1.3)
        side = max(min(w, h) // 2, min(want, w, h))
    else:
        cx, cy = w // 2, h // 2
        side = min(w, h)
    # Clamp the window so it stays fully inside the image (still no padding).
    left = min(max(0, cx - side // 2), w - side)
    top = min(max(0, cy - side // 2), h - side)
    return rgb.crop((left, top, left + side, top + side))


def _auto_tone(img: Image.Image) -> Image.Image:
    """Lightroom-style auto tone, computed locally (no API): neutralize the
    color cast using the photo's border — the backdrop/table, which should be
    neutral — as the gray reference, then stretch levels adaptively.

    Conservative by design: white balance only runs when the border looks like
    a plausible neutral backdrop (bright-ish, mild cast), per-channel gains are
    clamped, and the levels stretch clips 0.5% outliers with a capped gain —
    so a colorful item filling the frame is never washed out. Fixes the
    classic indoor yellow/warm cast on listing photos."""
    rgb = img.convert("RGB")
    small = rgb.resize((64, 64), Image.BILINEAR)

    # --- white balance from the border ring (outer ~12%) ---
    px = list(small.getdata())
    ring = [px[y * 64 + x] for y in range(64) for x in range(64)
            if x < 8 or x >= 56 or y < 8 or y >= 56]
    n = max(1, len(ring))
    means = [sum(p[c] for p in ring) / n for c in range(3)]
    mx, mn = max(means), max(1.0, min(means))
    out = rgb
    if mx > 90 and mx / mn < 1.6:  # bright-ish border with at most a mild cast
        target = sum(means) / 3
        gains = [min(1.3, max(0.8, target / max(1.0, m))) for m in means]
        if any(abs(g - 1) > 0.02 for g in gains):
            luts = []
            for g in gains:
                luts.extend(min(255, round(v * g)) for v in range(256))
            out = out.point(luts)

    # --- tone: white point, midtones, and a capped black point, in one LUT ---
    hist = out.convert("L").histogram()
    total = max(1, sum(hist))

    # White point. The clip used to be 0.5%, which on a real photo is often
    # just a specular glint or a sliver of window — one blown highlight put
    # `hi` at 255 and the whole lift switched itself off, which is why auto
    # tone looked like it did nothing on most photos. 1.5% steps past that.
    hi = _percentile(hist, total, 0.985)
    gain = min(1.4, 255 / hi) if 60 < hi < 250 else 1.0

    # Midtones, measured on the SUBJECT rather than the frame: a white
    # backdrop (or a background-removal cutout, which is pure white by
    # construction) dominates the histogram and would otherwise drag the
    # median up and make us darken the very item being sold.
    median = _subject_median(hist, total)
    gamma = 1.0
    if not 104 <= median <= 150:
        gamma = math.log(124 / 255) / math.log(max(2, median) / 255)
        gamma = min(1.30, max(0.72, gamma))

    # Black point, but only for photos that are actually hazy — a lifted floor
    # (nothing anywhere near black) means flat light, not a dark item. The
    # shift stays small and the gate stays high, because on a product shot the
    # dark end IS the item and stretching from there crushes its colors.
    lo = _percentile(hist, total, 0.005)
    black = min(12, lo - 6) if lo > 20 else 0

    if black or gain != 1.0 or gamma != 1.0:
        lut = []
        denom = max(1, 255 - black)
        for v in range(256):
            x = max(0.0, (v - black) / denom)
            if gamma != 1.0:
                x = x ** gamma
            lut.append(min(255, max(0, round(x * gain * 255))))
        out = out.point(lut * 3)
    return out


def _percentile(hist: list[int], total: int, frac: float) -> int:
    """Luminance level with `frac` of the histogram's mass at or below it."""
    want = total * frac
    acc = 0
    for v in range(256):
        acc += hist[v]
        if acc >= want:
            return v
    return 255


def _subject_median(hist: list[int], total: int) -> int:
    """Median luminance ignoring near-white and near-black pixels, so backdrop
    and cutout white don't decide the exposure of the item."""
    lo, hi = 6, 249
    sub = sum(hist[lo:hi + 1])
    if sub < total * 0.10:  # item fills the frame edge to edge — use it all
        lo, hi, sub = 0, 255, total
    acc, half = 0, max(1, sub) / 2
    for v in range(lo, hi + 1):
        acc += hist[v]
        if acc >= half:
            return v
    return 128


def _enhance(img: Image.Image) -> Image.Image:
    img = _auto_tone(img)
    # Scale the finishing nudges to how flat the photo actually is. These used
    # to be fixed at 4-8% for every photo, which is invisible on a dull one and
    # unnecessary on a punchy one — the other half of "auto levels does very
    # little". stddev ~58 is a contrasty photo, ~25 is flat and dingy.
    spread = ImageStat.Stat(img.convert("L")).stddev[0] or 0.0
    t = min(1.0, max(0.0, (58 - spread) / 33))
    img = ImageEnhance.Brightness(img).enhance(1.02 + 0.04 * t)
    img = ImageEnhance.Contrast(img).enhance(1.06 + 0.16 * t)
    img = ImageEnhance.Color(img).enhance(1.04 + 0.12 * t)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    return img


# Clockwise degrees -> the exact (lossless) Pillow transpose for it.
_CW_TRANSPOSE = {
    90: Image.Transpose.ROTATE_270,   # Pillow names rotations counter-clockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}
CW_TRANSPOSE = _CW_TRANSPOSE  # public alias (used by orient.py's verify pass)


def optimize(src: Path, dst: Path, remove_bg: bool = False,
             rotate: int = 0) -> dict:
    """Optimize a single image. Returns metadata about what was done.

    `rotate` is clockwise degrees (0/90/180/270) applied right after the EXIF
    fix — see services/orient. It has to happen BEFORE the cutout and square
    crop so the subject detection and framing work on an upright photo.
    """
    # Ignore anything that isn't a clean quarter turn, so a bad value can't
    # resample (and blur) the photo — or get reported as a rotation that
    # didn't happen.
    turn = rotate % 360 if rotate % 360 in _CW_TRANSPOSE else 0
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw)  # honor camera rotation
        # ...then straighten the ITEM, which EXIF knows nothing about.
        if turn:
            img = img.transpose(_CW_TRANSPOSE[turn])
        original_size = img.size

        # Downscale oversized inputs before the memory-heavy passes below.
        if max(img.size) > MAX_WORK_SIDE:
            img.thumbnail((MAX_WORK_SIDE, MAX_WORK_SIDE), Image.LANCZOS)

        studio_applied, studio_error = False, None
        bg_removed = False
        bg_engine, bg_error = None, None
        if remove_bg:
            # Engine order comes from BG_ENGINE — see _studio_and_cutout.
            img, bg_engine, bg_error, studio_applied, studio_error = \
                _studio_and_cutout(img)
            bg_removed = bg_engine not in (None, "none")
        else:
            img = _autocrop_borders(img)
        # Fill the square frame by cropping to the subject instead of padding
        # with white bars (which looked terrible on portrait photos).
        img = _fill_square(img)

        if img.size[0] != TARGET_SIZE:
            img = img.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)

        if studio_applied:
            # Lightroom already set tone/color — re-cooking it with the local
            # brightness/contrast boost would double-process. Just a light
            # sharpen to crisp up the post-resize pixels.
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
        else:
            img = _enhance(img)

        dst = dst.with_suffix(".jpg")
        img.save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True)

    out = {
        "file": dst.name,
        "original_size": original_size,
        "output_size": (TARGET_SIZE, TARGET_SIZE),
        "background_removed": bg_removed,
    }
    if turn:
        out["rotated"] = turn  # auto-straightened; the UI reports the count
    if studio_applied:
        out["studio"] = "lightroom"
    if studio_error:
        out["studio_error"] = studio_error  # why the preset didn't run
    if bg_engine:
        out["bg_engine"] = bg_engine
    if bg_error:
        out["bg_error"] = bg_error  # why the original was kept instead
    return out


def thumb_jpeg(path: Path, side: int = 512) -> bytes:
    """Small JPEG bytes for AI grouping calls — keeps a 40-photo request light."""
    from io import BytesIO
    with Image.open(path) as img:
        img = _flatten(ImageOps.exif_transpose(img))
        img.thumbnail((side, side), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=72)
        return buf.getvalue()


# The size vision calls send. Claude reads images in 28px patches and never
# downscales anything up to ~1092px on the long side (⌈1092/28⌉² = 1521 visual
# tokens); the full 1600px listing photo costs ⌈1600/28⌉² = 3364 tokens on
# high-resolution models AND double the upload bytes, for no extra detail the
# identify prompts actually use. Tag close-ups keep cropping from the full
# photo — this is only the whole-frame payload size.
VISION_SIDE = int(os.getenv("VISION_IMAGE_SIDE", "1092") or "1092")


def vision_copy(path: Path, side: int = 0) -> Path:
    """A cached, right-sized JPEG copy of an optimized photo for vision calls.

    Lives in the session's vision/ dir (a sibling of optimized/, invisible to
    the image list and the R2 mirror) and is regenerated whenever the source
    file is newer — photo edits rewrite the optimized file, so staleness is
    just an mtime comparison. Returns `path` unchanged for anything that isn't
    a session's optimized photo, so callers can pass any path safely."""
    if path.parent.name != "optimized":
        return path
    side = side or VISION_SIDE
    dst = path.parent.parent / "vision" / path.name
    try:
        if dst.is_file() and dst.stat().st_mtime >= path.stat().st_mtime:
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as img:
            img = _flatten(img)
            img.thumbnail((side, side), Image.LANCZOS)
            tmp = dst.with_name(dst.name + ".tmp")
            img.save(tmp, "JPEG", quality=85)
            os.replace(tmp, dst)  # atomic: a racing reader never sees a torn file
        return dst
    except Exception as exc:  # noqa: BLE001 - a copy is an optimization only
        log.info("vision copy skipped for %s: %s", path.name, exc)
        return path


# How many photos to run through a remote engine (Pixian/Photoroom/Adobe) at
# once. The per-photo work there is mostly waiting on the API, so the pool
# turns a 250-photo bulk batch from ~15 minutes of serial waiting into
# overlapping waves — while keeping peak memory (a few 3200px working copies
# plus the capped upload payloads) tolerable. Rate limits are handled
# per-call with retry/backoff, so a bigger pool degrades to pacing, not
# failures.
_PHOTO_BATCH_WORKERS = int(os.getenv("PHOTO_BATCH_WORKERS", "8") or "8")
# Concurrency when the LOCAL engine (or no cutout at all) does the work.
# Model inference stays single-flight behind _INFER_LOCK regardless, but
# everything around it — mask refinement, hole fill, compose, crop, tone,
# encode, roughly half of each photo's wall clock — is ordinary Pillow/numpy
# work that releases the GIL. A couple of photos in flight let one photo's
# post-processing overlap the next photo's inference. Kept small because each
# in-flight photo holds a few full-size working buffers; PHOTO_LOCAL_WORKERS=1
# restores the old strictly-serial behavior.
_LOCAL_BATCH_WORKERS = int(os.getenv("PHOTO_LOCAL_WORKERS", "2") or "2")


def optimize_all(src_dir: Path, dst_dir: Path, remove_bg: bool = False,
                 progress=None) -> list[dict]:
    """Optimize every image in src_dir. `progress(done, total)` (optional) is
    called after each photo so long bulk jobs can show a live count."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    exts = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".gif",
            ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif"}
    # Natural sort: past 99 files a lexicographic sort puts src_100 before
    # src_20, scrambling the shooting order that bulk grouping relies on.
    jobs = [(i, src) for i, src
            in enumerate(sorted(src_dir.iterdir(), key=lambda p: natural_key(p.name)))
            if src.suffix.lower() in exts]
    # One batched vision pass over the whole set decides which photos were shot
    # with the ITEM lying sideways or upside-down — something EXIF can't tell
    # us. Done up front so each photo is straightened before its cutout and
    # square crop, not after.
    rotations = orient.detect_rotations([src for _i, src in jobs])
    return optimize_batch(
        [(src, dst_dir / f"img_{i:03d}.jpg", rotations.get(src.name, 0))
         for i, src in jobs],
        remove_bg=remove_bg, progress=progress)


def optimize_batch(jobs: list[tuple[Path, Path, int]], remove_bg: bool = False,
                   progress=None) -> list[dict]:
    """Optimize (src, dst, clockwise-rotation) jobs, pooled to fit the engine.

    A chain led by a remote API (Pixian/Photoroom/Adobe) gets a wide pool —
    each photo is mostly a network call we wait on. The local engine (and the
    no-cutout path) gets a small pool: inference is serialized by _INFER_LOCK
    either way, but the pipeline around it overlaps. Results come back in job
    order; a failed photo yields {"file", "error"} instead of raising."""
    total = len(jobs)
    done = 0
    done_lock = threading.Lock()

    def _tick() -> None:
        nonlocal done
        with done_lock:
            done += 1
            count = done
        if progress:
            try:
                progress(count, total)
            except Exception:  # noqa: BLE001 - progress is display-only
                pass

    def _one(job: tuple[Path, Path, int]) -> dict:
        src, dst, rotate = job
        try:
            result = optimize(src, dst, remove_bg, rotate=rotate)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad image
            result = {"file": src.name, "error": str(exc)}
        _tick()
        return result

    chain = config.bg_engine_chain() if remove_bg else []
    remote_first = bool(chain) and chain[0] != "local"
    if remote_first:
        workers = min(_PHOTO_BATCH_WORKERS, total)
    elif remove_bg:
        # Local cutouts: model inference releases the GIL (and is serialized
        # by _INFER_LOCK anyway), so a second photo's mask/crop/encode work
        # genuinely overlaps it.
        workers = min(_LOCAL_BATCH_WORKERS, total)
    else:
        # No cutout = pure Pillow CPU with no waits to overlap; measured with
        # the perf harness, a 2-thread pool is ~15% SLOWER than serial here
        # (GIL + memory-bandwidth contention), so plain optimizes stay serial.
        workers = 1
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            # map() preserves job order, so results line up with filenames.
            return list(pool.map(_one, jobs))
    return [_one(job) for job in jobs]


# ---------------------------------------------------------------------------
# Photo studio: AI subject detection powering smart crop, leftover-background
# highlighting, and one-tap auto clean-up in the in-browser editor.
# ---------------------------------------------------------------------------

# How far a channel may fall below pure white and still count as "clean".
RESIDUE_WHITE_TOL = 22
# The subject's soft edge is grown by this many px before looking for residue,
# so anti-aliased borders aren't flagged.
SUBJECT_GROW_PX = 9


def _subject_mask(img: Image.Image) -> Image.Image:
    """Soft alpha mask (mode L, same size as img) of the detected subject,
    with interior holes re-attached — otherwise a graphic or a bright panel the
    model mistakes for background makes auto-clean whiten a chunk out of the
    middle of the item (and makes the residue highlight flag the item's own
    interior as leftover background)."""
    rgb = img.convert("RGB")
    mask, _ = _fill_interior_holes(_alpha_mask(rgb), rgb)
    return mask


def analyze_cleanup(img: Image.Image) -> dict:
    """Re-check the item's borders: find the subject and any non-white
    leftovers outside it (the bits a background removal missed).

    Returns the subject bbox, a binary residue mask (mode L), and how much of
    the frame that residue covers.
    """
    from PIL import ImageChops

    rgb = _flatten(img)
    mask = _subject_mask(rgb)
    subject = mask.point(lambda a: 255 if a >= 96 else 0)
    bbox = subject.getbbox()

    # Grow the subject so its soft edge isn't counted as residue.
    grown = subject.filter(ImageFilter.MaxFilter(SUBJECT_GROW_PX))
    # Residue = meaningfully non-white pixels outside the (grown) subject.
    white = Image.new("RGB", rgb.size, WHITE)
    nonwhite = ImageChops.difference(rgb, white).convert("L").point(
        lambda d: 255 if d > RESIDUE_WHITE_TOL else 0)
    residue = ImageChops.subtract(nonwhite, grown)
    # Knock out 1-2px speckle so the highlight shows real leftovers, not noise.
    residue = residue.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))

    total = rgb.size[0] * rgb.size[1]
    residue_px = sum(residue.histogram()[128:])
    return {
        "residue_mask": residue,
        "bbox": list(bbox) if bbox else None,
        "residue_pct": round(100.0 * residue_px / total, 2),
    }


def auto_clean(img: Image.Image) -> Image.Image:
    """Re-detect the item's borders and whiten everything outside them.

    The mask edge is grown slightly then feathered so we never eat into the
    subject and the transition stays soft.
    """
    rgb = _flatten(img)
    mask = _subject_mask(rgb)
    mask = mask.point(lambda a: 255 if a >= 96 else 0)
    mask = mask.filter(ImageFilter.MaxFilter(7))
    mask = mask.filter(ImageFilter.GaussianBlur(2))
    white = Image.new("RGB", rgb.size, WHITE)
    return Image.composite(rgb, white, mask)


def smart_crop(img: Image.Image, margin: float = 0.05) -> Optional[Image.Image]:
    """Crop to the detected subject (plus a margin), re-padded to a square.

    Returns None when there's no confident subject or the frame is already
    tight, so the caller can say "nothing to crop" instead of degrading the
    photo.
    """
    rgb = _flatten(img)
    mask = _subject_mask(rgb)
    bbox = mask.point(lambda a: 255 if a >= 96 else 0).getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox
    mx = int((right - left) * margin)
    my = int((bottom - top) * margin)
    left = max(0, left - mx)
    top = max(0, top - my)
    right = min(rgb.width, right + mx)
    bottom = min(rgb.height, bottom + my)
    # Already tight? Don't churn the image for a <8% trim.
    if (right - left) * (bottom - top) > 0.92 * rgb.width * rgb.height:
        return None

    crop = rgb.crop((left, top, right, bottom))
    # Pad back to a square using the image's own border color (pure white on a
    # cleaned photo; approximates the backdrop otherwise).
    canvas = _pad_square(crop, _border_color(rgb))
    if canvas.width != TARGET_SIZE:
        canvas = canvas.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    return canvas

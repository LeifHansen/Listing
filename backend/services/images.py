"""Local image optimization tuned for eBay listing photos.

eBay recommends square-ish images with the longest side >= 1600px for zoom,
clean framing, and good lighting. This module does that without any external
service: auto-orient, trim borders, pad to square on a near-white canvas,
upscale to target, and apply mild brightness/contrast/sharpness enhancement.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from ..config import log

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

# rembg loads an ONNX model on first use; keep the session process-global so we
# pay that cost once, and only when background removal is actually used.
# Default to the lightweight "u2netp" model (~4.7MB): the full "u2net" (176MB)
# is too slow to download and too memory-hungry for a shared-cpu-1x machine.
# Override with REMBG_MODEL if the box ever gets a bigger VM.
_REMBG_MODEL = os.getenv("REMBG_MODEL", "u2netp").strip() or "u2netp"
_rembg_session = None
# Bulk jobs run in a daemon thread while uploads hit the request threadpool —
# without a lock, two first-users would double-load the ONNX model (memory
# spike on a shared-cpu-1x box) and race the session init.
_rembg_lock = threading.Lock()


def warm() -> None:
    """Pre-import rembg and trigger numba's JIT on a tiny image.

    The very first background removal otherwise pays a ~60-70s one-time cost
    (importing scipy/numba/opencv + JIT compilation). Called in a background
    thread at startup so the machine is reachable immediately while this warms
    up, and real uploads hit a warm (~1s) path.
    """
    try:
        _remove_background(Image.new("RGB", (32, 32), (200, 100, 50)))
        log.info("images: background-removal model warmed")
    except Exception as exc:  # noqa: BLE001 - warmup is best-effort
        log.warning(f"images: warmup failed (will lazy-load on first use): {exc}")


def _remove_background(img: Image.Image, add_shadow: bool = False) -> Image.Image:
    """Cut out the subject and composite it onto a pure-white canvas.

    Uses rembg (U^2-Net) for the raw subject mask, then refines it: stray
    background blobs the model mistook for subject are dropped, and
    background showing THROUGH the item (a pitcher handle's opening, a mug
    handle, a strap loop) is punched out by color.

    add_shadow: drop a soft studio contact shadow beneath the item, cast
    opposite the detected light direction, so the cutout doesn't look like it
    floats on flat white.
    """
    rgb = img.convert("RGB")
    alpha = _refined_alpha(rgb)
    canvas = Image.new("RGB", rgb.size, WHITE)
    if add_shadow:
        canvas = _cast_shadow(canvas, rgb, alpha)
    return Image.composite(rgb, canvas, alpha)


def _light_offset(rgb: Image.Image, alpha: Image.Image) -> tuple[int, int]:
    """Where the drop shadow should fall, in px: opposite the light source
    (inferred from which side of the item is brighter), with a downward bias
    so it reads as a shadow resting on a surface."""
    import numpy as np

    a = np.asarray(alpha) >= 128
    if not a.any():
        return (0, int(0.03 * rgb.size[1]))
    lum = np.asarray(rgb.convert("L"), dtype=np.float32)
    ys, xs = np.nonzero(a)
    cx, cy = xs.mean(), ys.mean()
    w = lum[ys, xs]
    wsum = w.sum() or 1.0
    # Brightness-weighted centroid vs geometric centroid → points toward light.
    lx = (xs * w).sum() / wsum - cx
    ly = (ys * w).sum() / wsum - cy
    span = max(1.0, np.hypot(np.ptp(xs), np.ptp(ys)))
    scale = 0.10 * span  # shadow throw ≈ 10% of the item's diagonal
    n = np.hypot(lx, ly) or 1.0
    # Opposite the light horizontally; always downward (gravity) vertically.
    dx = -lx / n * scale
    dy = abs(ly / n) * scale + 0.05 * span
    return (int(round(dx)), int(round(dy)))


def _subject_touches_edge(alpha: Image.Image, frac: float = 0.06) -> bool:
    """True when the subject runs off the frame (a close-up crop). A drop
    shadow makes no physical sense there — and offsetting the silhouette of a
    clipped subject paints obvious artifacts."""
    import numpy as np

    a = np.asarray(alpha) >= 128
    h, w = a.shape
    if not a.any():
        return False
    border = (a[0, :].sum() + a[-1, :].sum() + a[:, 0].sum() + a[:, -1].sum())
    return border >= frac * 2 * (h + w)


def _cast_shadow(canvas: Image.Image, rgb: Image.Image,
                 alpha: Image.Image) -> Image.Image:
    """Paint a soft grey shadow onto the white canvas beneath the subject."""
    if _subject_touches_edge(alpha):
        return canvas
    dx, dy = _light_offset(rgb, alpha)
    blur = max(6, int(0.02 * max(rgb.size)))
    shadow_mask = alpha.point(lambda v: int(v * 0.42))  # max ~42% opacity
    # Shift WITHOUT wrapping: ImageChops.offset wraps around the frame, so a
    # subject near the bottom edge used to cast a floating shadow bar at the
    # TOP of the photo. Paste into a blank mask instead — pixels shifted past
    # the edge just disappear.
    shifted = Image.new("L", shadow_mask.size, 0)
    shifted.paste(shadow_mask, (dx, dy))
    shadow_mask = shifted.filter(ImageFilter.GaussianBlur(blur))
    grey = Image.new("RGB", canvas.size, (150, 150, 150))
    return Image.composite(grey, canvas, shadow_mask)


# --- mask refinement --------------------------------------------------------
# The lightweight u2netp model leaves two classes of artifacts:
#   1. detached background blobs it thought were salient, and
#   2. enclosed background regions inside the item's outline (handle holes).
# Both are fixed on the mask itself so every consumer (upload-time removal,
# Auto clean, the studio's border re-check) benefits.

MIN_COMPONENT_FRACTION = 0.08   # keep subject blobs >= 8% of the largest
MIN_HOLE_PX = 24                # ignore speckle "holes" smaller than this
MAX_HOLE_FRACTION = 0.15        # a see-through hole is SMALL vs the item
HOLE_UNIFORMITY_STD = 14.0      # background through a hole is uniform
MIN_REFINED_KEEP = 0.55         # refinement may not gut the model's subject
BG_TOL_MIN, BG_TOL_MAX = 16, 34 # per-channel background color tolerance


def _refined_alpha(rgb: Image.Image) -> Image.Image:
    """A cleaned-up, feathered subject alpha (mode L) for compositing.

    Refinement is deliberately CONSERVATIVE: on items with bright/glossy
    patches (a white-highlighted painted statue), aggressive color-based hole
    punching used to carve visible chunks out of the subject. Every cut now
    has to look like a genuine see-through hole — small relative to the item
    and showing a uniform patch of the background color — and if refinement
    would remove a large share of what the model called subject, we fall back
    to the model's own mask untouched."""
    import numpy as np
    from scipy import ndimage

    raw = _subject_mask(rgb)
    raw_m = np.asarray(raw) >= 96
    if not raw_m.any():
        # No subject found: keep the whole image rather than blanking it.
        return Image.new("L", rgb.size, 255)
    m = raw_m.copy()
    arr = np.asarray(rgb, dtype=np.int16)

    # 1. Drop stray blobs: keep components comparable to the largest one
    # (multi-item photos keep every real item; distant specks vanish). Smaller
    # parts that overlap the main item's bounding box (a statue's paw, a
    # strap) are kept too — they're almost always part of the item.
    labels, n = ndimage.label(m)
    if n > 1:
        sizes = ndimage.sum(m, labels, range(1, n + 1))
        keep = sizes >= max(sizes.max() * MIN_COMPONENT_FRACTION, 64)
        largest = int(sizes.argmax()) + 1
        ys, xs = np.nonzero(labels == largest)
        pady = int(0.05 * (ys.max() - ys.min() + 1))
        padx = int(0.05 * (xs.max() - xs.min() + 1))
        y0, y1 = ys.min() - pady, ys.max() + pady
        x0, x1 = xs.min() - padx, xs.max() + padx
        objs = ndimage.find_objects(labels)
        for i, sl in enumerate(objs):
            if keep[i] or sl is None or sizes[i] < 256:
                continue
            if (sl[0].start <= y1 and sl[0].stop >= y0
                    and sl[1].start <= x1 and sl[1].stop >= x0):
                keep[i] = True
        m = np.isin(labels, np.nonzero(keep)[0] + 1)

    # 2. Punch background regions showing THROUGH the item (handle holes).
    outside = ~m
    if outside.sum() > 500:
        bg = np.median(arr[outside], axis=0)
        spread = np.abs(arr[outside] - bg).mean(axis=0).max()
        tol = float(np.clip(2.5 * spread, BG_TOL_MIN, BG_TOL_MAX))
        subj_med = np.median(arr[m], axis=0)
        # Guard: if the item itself is background-colored (white-on-white),
        # color can't separate them — don't punch anything.
        if np.abs(subj_med - bg).max() > tol * 1.5:
            near_bg = (np.abs(arr - bg) <= tol).all(axis=2)
            holes = m & near_bg
            hlabels, hn = ndimage.label(holes)
            if 0 < hn <= 400:  # speckle storm -> refinement untrustworthy
                hsizes = ndimage.sum(holes, hlabels, range(1, hn + 1))
                max_hole = MAX_HOLE_FRACTION * m.sum()
                for i in range(1, hn + 1):
                    sz = hsizes[i - 1]
                    # Too big to be a see-through hole = likely the item's own
                    # bright patch (glossy highlight); leave it alone.
                    if sz < MIN_HOLE_PX or sz > max_hole:
                        continue
                    region = hlabels == i
                    # Background through a real hole is uniform; the item's
                    # highlights carry texture and gradients.
                    if arr[region].std(axis=0).max() > HOLE_UNIFORMITY_STD:
                        continue
                    m &= ~region

    # Catastrophe guard: if refinement removed a big share of the model's
    # subject, it went wrong (multicoloured item, busy background) — trust
    # the model over our heuristics.
    if m.sum() < MIN_REFINED_KEEP * raw_m.sum():
        m = raw_m

    # Slight grow to protect the item's edge, then feather the cut.
    mask_img = Image.fromarray((m * 255).astype("uint8"), "L")
    mask_img = mask_img.filter(ImageFilter.MaxFilter(5))
    return mask_img.filter(ImageFilter.GaussianBlur(1.5))


def _autocrop_borders(img: Image.Image, tolerance: int = 18) -> Image.Image:
    """Trim near-uniform borders (e.g. plain background) around the subject."""
    rgb = _flatten(img)
    # Compare against the top-left corner color as the assumed background.
    bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))

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


def _crop_to_content(img: Image.Image, margin: float = 0.05,
                     tol: int = 8) -> Image.Image:
    """Tighten the frame around non-white content ("crop to fit"). Used after
    background removal, where everything but the subject (and its shadow) is
    pure white — without this, a small item floats in a sea of white padding."""
    rgb = _flatten(img)
    bg = Image.new("RGB", rgb.size, WHITE)
    diff = ImageChops.difference(rgb, bg).convert("L").point(
        lambda v: 255 if v > tol else 0)
    bbox = diff.getbbox()
    if not bbox:
        return img
    left, top, right, bottom = bbox
    pad = int(margin * max(img.size))
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(img.size[0], right + pad)
    bottom = min(img.size[1], bottom + pad)
    # Skip trivial crops — not worth re-framing for a sliver.
    if (right - left) > 0.92 * img.size[0] and (bottom - top) > 0.92 * img.size[1]:
        return img
    return img.crop((left, top, right, bottom))


def _pad_to_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), CANVAS_COLOR)
    canvas.paste(_flatten(img), ((side - w) // 2, (side - h) // 2))
    return canvas


def _auto_levels(img: Image.Image) -> Image.Image:
    """Auto brightness + contrast: stretch the tonal range so under/over-exposed
    phone photos get an even, well-lit exposure before the rest of the pipeline.
    A small histogram cutoff ignores specular highlights and deep shadows so one
    hot pixel can't flatten the result; preserve_tone applies a single luminance
    LUT across channels to avoid colour casts (falls back on old Pillow)."""
    rgb = _flatten(img)
    try:
        return ImageOps.autocontrast(rgb, cutoff=1, preserve_tone=True)
    except TypeError:  # Pillow < 8.2 has no preserve_tone kwarg
        return ImageOps.autocontrast(rgb, cutoff=1)


def _enhance(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Brightness(img).enhance(1.04)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    return img


def rotate_saved(path: Path, degrees_cw: int) -> bool:
    """Rotate an already-optimized JPEG in place by 90/180/270° clockwise
    (used to auto-correct sideways/upside-down photos). Returns True on change."""
    import os
    d = int(degrees_cw) % 360
    if d not in (90, 180, 270):
        return False
    with Image.open(path) as im:
        flat = _flatten(ImageOps.exif_transpose(im))
    out = flat.rotate(-d, expand=True)  # PIL rotate() is CCW; negate for CW
    tmp = path.with_name(path.name + ".tmp")
    out.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True)
    os.replace(tmp, path)
    return True


def optimize(src: Path, dst: Path, remove_bg: bool = False,
             add_shadow: bool = False) -> dict:
    """Optimize a single image. Returns metadata about what was done."""
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw)  # honor camera rotation
        original_size = img.size

        # Downscale oversized inputs before the memory-heavy passes below.
        if max(img.size) > MAX_WORK_SIDE:
            img.thumbnail((MAX_WORK_SIDE, MAX_WORK_SIDE), Image.LANCZOS)

        # Even out exposure first, so background removal keys off a clean,
        # well-lit image and the final photo looks studio-lit.
        img = _auto_levels(img)

        bg_removed = False
        if remove_bg:
            img = _remove_background(img, add_shadow=add_shadow)
            # Crop to fit: tighten around the subject (+ its shadow) so the
            # item fills the frame instead of floating in white padding.
            img = _crop_to_content(img)
            bg_removed = True
        else:
            img = _autocrop_borders(img)
        img = _pad_to_square(img)

        if img.size[0] != TARGET_SIZE:
            img = img.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)

        img = _enhance(img)

        dst = dst.with_suffix(".jpg")
        img.save(dst, "JPEG", quality=JPEG_QUALITY, optimize=True)

    return {
        "file": dst.name,
        "original_size": original_size,
        "output_size": (TARGET_SIZE, TARGET_SIZE),
        "background_removed": bg_removed,
    }


def thumb_jpeg(path: Path, side: int = 512) -> bytes:
    """Small JPEG bytes for AI grouping calls — keeps a 40-photo request light."""
    from io import BytesIO
    with Image.open(path) as img:
        img = _flatten(ImageOps.exif_transpose(img))
        img.thumbnail((side, side), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=72)
        return buf.getvalue()


def optimize_all(src_dir: Path, dst_dir: Path, remove_bg: bool = False,
                 add_shadow: bool = False) -> list[dict]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    results = []
    exts = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".gif",
            ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif"}
    for i, src in enumerate(sorted(src_dir.iterdir())):
        if src.suffix.lower() not in exts:
            continue
        dst = dst_dir / f"img_{i:02d}.jpg"
        try:
            results.append(optimize(src, dst, remove_bg, add_shadow))
        except Exception as exc:  # noqa: BLE001 - keep going on a bad image
            results.append({"file": src.name, "error": str(exc)})
    return results


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
    """Soft alpha mask (mode L, same size as img) of the detected subject."""
    global _rembg_session
    from rembg import new_session, remove

    with _rembg_lock:
        if _rembg_session is None:
            _rembg_session = new_session(_REMBG_MODEL)
        # Serialize inference too: one shared ONNX session, and concurrent
        # inference on a 1-CPU box just thrashes memory without speedup.
        return remove(img.convert("RGB"), session=_rembg_session,
                      only_mask=True).convert("L")


def analyze_cleanup(img: Image.Image) -> dict:
    """Re-check the item's borders: find the subject and any non-white
    leftovers outside it (the bits a background removal missed).

    Returns the subject bbox, a binary residue mask (mode L), and how much of
    the frame that residue covers.
    """

    rgb = _flatten(img)
    subject = _refined_alpha(rgb).point(lambda a: 255 if a >= 128 else 0)
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
    white = Image.new("RGB", rgb.size, WHITE)
    return Image.composite(rgb, white, _refined_alpha(rgb))


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
    corners = [rgb.getpixel((x, y)) for x, y in
               ((0, 0), (rgb.width - 1, 0), (0, rgb.height - 1), (rgb.width - 1, rgb.height - 1))]
    pad_color = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
    side = max(crop.size)
    canvas = Image.new("RGB", (side, side), pad_color)
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    if side != TARGET_SIZE:
        canvas = canvas.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    return canvas

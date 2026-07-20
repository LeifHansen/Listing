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

from PIL import Image, ImageEnhance, ImageOps, ImageFilter

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

# In-house background removal — rembg (ONNX U^2-Net family) running in-process,
# no external service. rembg loads its model on first use; keep the session
# process-global so we pay that cost once, and only when background removal is
# actually used. "isnet-general-use" (~176MB) is rembg's best general-purpose
# model — far better than the tiny "u2netp" on clothing, dark items, and detail
# shots. It fits the shared-cpu-2x / 2GB machine (fly.toml). Set REMBG_MODEL to
# override (e.g. "u2netp" for a smaller/faster but lower-quality cut).
_REMBG_MODEL = os.getenv("REMBG_MODEL", "isnet-general-use").strip() or "isnet-general-use"
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


def _alpha_mask(img_rgb: Image.Image, max_side: Optional[int] = None) -> Image.Image:
    """rembg subject alpha (mode L, same size as img_rgb), computed on a copy
    capped to `max_side` (defaults to the fast bulk size) then upscaled back.
    A larger max_side feeds the model more detail = crisper edges."""
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
    if alpha.size != img_rgb.size:
        # BILINEAR (not LANCZOS) to upscale the mask: LANCZOS overshoots at
        # high-contrast edges, ringing a faint halo of the old background back
        # in — exactly the "ghost background" the cutout is meant to remove.
        alpha = alpha.resize(img_rgb.size, Image.BILINEAR)
    return alpha


def _refine_alpha(alpha: Image.Image) -> Image.Image:
    """Clean the matte into a natural cutout: despeckle, kill the faint
    background ghost, trim the ~1px background halo around the subject, then
    lightly feather the edge so it blends instead of looking stickered-on."""
    alpha = alpha.filter(ImageFilter.MedianFilter(3))
    span = max(1, _ALPHA_HIGH - _ALPHA_LOW)
    alpha = alpha.point(lambda a: 0 if a < _ALPHA_LOW
                        else 255 if a > _ALPHA_HIGH
                        else round((a - _ALPHA_LOW) * 255 / span))
    # Erode 1px to cut the residual background fringe, then a sub-pixel feather
    # so the composited edge is smooth rather than jagged.
    alpha = alpha.filter(ImageFilter.MinFilter(3))
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.6))
    return alpha


def _compose_on_white(rgb: Image.Image, alpha: Image.Image,
                      shadow: bool = True) -> Image.Image:
    """Composite the subject (given by `alpha`) onto pure white, optionally
    with a soft drop shadow drawn from the subject silhouette."""
    w, h = rgb.size
    canvas = Image.new("RGB", (w, h), WHITE)
    if shadow:
        # Shadow = the silhouette, blurred, nudged down/right, at low opacity —
        # a subtle contact shadow that grounds the item on the white backdrop.
        blur = max(4, round(min(w, h) * 0.015))
        off = max(2, round(min(w, h) * 0.010))
        shadow_a = alpha.filter(ImageFilter.GaussianBlur(blur)).point(
            lambda a: int(a * 0.40))
        shifted = Image.new("L", (w, h), 0)
        shifted.paste(shadow_a, (off, off))
        canvas.paste(Image.new("RGB", (w, h), (55, 55, 55)), (0, 0), shifted)
    canvas.paste(rgb, (0, 0), alpha)
    return canvas


def _cutout_on_white(img: Image.Image,
                     max_side: Optional[int] = None) -> Optional[Image.Image]:
    """In-house rembg cutout composited on pure white (with a soft drop shadow
    unless BG_SHADOW=off) — or None when the result is clearly a failure
    (subject erased), so callers keep the original photo instead of saving a
    destroyed one. `max_side` caps the matte resolution (higher = crisper)."""
    rgb = img.convert("RGB")
    alpha = _refine_alpha(_alpha_mask(rgb, max_side=max_side))
    opaque = sum(alpha.histogram()[128:])
    if opaque / max(1, alpha.width * alpha.height) < _MIN_FG_COVERAGE:
        log.warning("bg-removal: subject nearly erased (%.1f%% kept) — keeping original",
                    100 * opaque / max(1, alpha.width * alpha.height))
        return None
    return _compose_on_white(rgb, alpha, shadow=_BG_SHADOW)


def _remove_background(img: Image.Image) -> Image.Image:
    """Cut out the subject onto white for the upload path. Falls back to the
    original (flattened) on any failure — an auto/bulk upload must never die
    or silently save a destroyed photo because one cutout call failed."""
    try:
        out = _cutout_on_white(img)
    except Exception as exc:  # noqa: BLE001 - bulk keeps going without the cutout
        log.warning("bg-removal: cutout failed (%s) — keeping original", exc)
        out = None
    return out if out is not None else _flatten(img)


def remove_background_white(img: Image.Image) -> Image.Image:
    """Photo-studio 'Remove background'. Runs the matte at the higher studio
    resolution for crisper edges, falling back to the fast size if a big
    inference runs out of memory. Raises when the cutout genuinely fails so the
    editor can tell the user instead of silently doing nothing."""
    for side in (_STUDIO_MAX_SIDE, _REMBG_MAX_SIDE):
        try:
            out = _cutout_on_white(img, max_side=side)
        except Exception as exc:  # noqa: BLE001 - retry smaller on OOM/model error
            log.warning("bg-removal: matte at %dpx failed (%s) — retrying smaller",
                        side, exc)
            continue
        if out is not None:
            return out
        break  # a clean run that erased the subject won't improve when smaller
    raise ValueError(
        "Couldn't cleanly separate this photo from its background — it's "
        "likely a close-up, dark, or low-contrast shot. Try Auto clean, or "
        "paint the background out with the brush.")


def warm() -> None:
    """Pre-load the rembg model + trigger numba's JIT on a tiny image.

    The very first background removal otherwise pays a ~60-70s one-time cost
    (importing scipy/numba/onnxruntime + JIT). Called in a background thread at
    startup so the machine is reachable immediately while this warms up, and
    real uploads hit a warm (~1s) path."""
    try:
        _remove_background(Image.new("RGB", (32, 32), (200, 100, 50)))
        log.info("images: background-removal model warmed")
    except Exception as exc:  # noqa: BLE001 - warmup is best-effort
        log.warning(f"images: warmup failed (will lazy-load on first use): {exc}")


def _autocrop_borders(img: Image.Image, tolerance: int = 18) -> Image.Image:
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


def _fill_square(img: Image.Image) -> Image.Image:
    """Crop to a square that FILLS the frame — never pad with white bars.

    When a subject is detectable, crop a square that tightly frames it (plus
    margin) so the item fills the photo. Otherwise take the largest centered
    square. The result is always square (eBay's recommended shape) with no
    letterbox/pillarbox padding.
    """
    rgb = _flatten(img)
    w, h = rgb.size
    box = _subject_box(rgb)
    if box:
        left, top, right, bottom = box
        cx, cy = (left + right) // 2, (top + bottom) // 2
        # A square big enough for the subject + ~30% breathing room, but never
        # larger than the frame nor a tiny over-zoom.
        want = int(max(right - left, bottom - top) * 1.3)
        side = max(min(w, h) // 2, min(want, w, h))
    else:
        cx, cy = w // 2, h // 2
        side = min(w, h)
    # Clamp the window so it stays fully inside the image (still no padding).
    left = min(max(0, cx - side // 2), w - side)
    top = min(max(0, cy - side // 2), h - side)
    return rgb.crop((left, top, left + side, top + side))


def _enhance(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Brightness(img).enhance(1.04)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    return img


def optimize(src: Path, dst: Path, remove_bg: bool = False) -> dict:
    """Optimize a single image. Returns metadata about what was done."""
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw)  # honor camera rotation
        original_size = img.size

        # Downscale oversized inputs before the memory-heavy passes below.
        if max(img.size) > MAX_WORK_SIDE:
            img.thumbnail((MAX_WORK_SIDE, MAX_WORK_SIDE), Image.LANCZOS)

        bg_removed = False
        if remove_bg:
            img = _remove_background(img)
            bg_removed = True
        else:
            img = _autocrop_borders(img)
        # Fill the square frame by cropping to the subject instead of padding
        # with white bars (which looked terrible on portrait photos).
        img = _fill_square(img)

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
                 progress=None) -> list[dict]:
    """Optimize every image in src_dir. `progress(done, total)` (optional) is
    called after each photo so long bulk jobs can show a live count."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    results = []
    exts = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".gif",
            ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif"}
    entries = sorted(src_dir.iterdir())
    total = sum(1 for p in entries if p.suffix.lower() in exts)
    done = 0
    for i, src in enumerate(entries):
        if src.suffix.lower() not in exts:
            continue
        dst = dst_dir / f"img_{i:02d}.jpg"
        try:
            results.append(optimize(src, dst, remove_bg))
        except Exception as exc:  # noqa: BLE001 - keep going on a bad image
            results.append({"file": src.name, "error": str(exc)})
        done += 1
        if progress:
            try:
                progress(done, total)
            except Exception:  # noqa: BLE001 - progress is display-only
                pass
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
    return _alpha_mask(img.convert("RGB"))


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
    corners = [rgb.getpixel((x, y)) for x, y in
               ((0, 0), (rgb.width - 1, 0), (0, rgb.height - 1), (rgb.width - 1, rgb.height - 1))]
    pad_color = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
    side = max(crop.size)
    canvas = Image.new("RGB", (side, side), pad_color)
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    if side != TARGET_SIZE:
        canvas = canvas.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    return canvas

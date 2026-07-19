"""Local image optimization tuned for eBay listing photos.

eBay recommends square-ish images with the longest side >= 1600px for zoom,
clean framing, and good lighting. This module does that without any external
service: auto-orient, trim borders, pad to square on a near-white canvas,
upscale to target, and apply mild brightness/contrast/sharpness enhancement.
"""
from __future__ import annotations

import os
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

# rembg loads an ONNX model on first use; keep the session process-global so we
# pay that cost once, and only when background removal is actually used.
# Default to the lightweight "u2netp" model (~4.7MB): the full "u2net" (176MB)
# is too slow to download and too memory-hungry for a shared-cpu-1x machine.
# Override with REMBG_MODEL if the box ever gets a bigger VM.
_REMBG_MODEL = os.getenv("REMBG_MODEL", "u2netp").strip() or "u2netp"
_rembg_session = None


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


def _remove_background(img: Image.Image) -> Image.Image:
    """Cut out the subject and composite it onto a pure-white canvas.

    Uses rembg (U^2-Net). Imported lazily so the dependency/model is only
    loaded when a user actually checks the box.
    """
    global _rembg_session
    from rembg import new_session, remove

    if _rembg_session is None:
        _rembg_session = new_session(_REMBG_MODEL)
    cutout = remove(img.convert("RGBA"), session=_rembg_session)  # transparent bg
    canvas = Image.new("RGBA", cutout.size, WHITE + (255,))
    canvas.alpha_composite(cutout)
    return canvas.convert("RGB")


def remove_background_white(img: Image.Image) -> Image.Image:
    """Public wrapper for the photo studio's one-tap 'Remove background':
    rembg cutout composited onto pure white."""
    return _remove_background(img)


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


def _pad_to_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), CANVAS_COLOR)
    canvas.paste(_flatten(img), ((side - w) // 2, (side - h) // 2))
    return canvas


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


def optimize_all(src_dir: Path, dst_dir: Path, remove_bg: bool = False) -> list[dict]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    results = []
    exts = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".gif",
            ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif"}
    for i, src in enumerate(sorted(src_dir.iterdir())):
        if src.suffix.lower() not in exts:
            continue
        dst = dst_dir / f"img_{i:02d}.jpg"
        try:
            results.append(optimize(src, dst, remove_bg))
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

    if _rembg_session is None:
        _rembg_session = new_session(_REMBG_MODEL)
    return remove(img.convert("RGB"), session=_rembg_session, only_mask=True).convert("L")


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

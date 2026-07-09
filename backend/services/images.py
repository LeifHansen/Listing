"""Local image optimization tuned for eBay listing photos.

eBay recommends square-ish images with the longest side >= 1600px for zoom,
clean framing, and good lighting. This module does that without any external
service: auto-orient, trim borders, pad to square on a near-white canvas,
upscale to target, and apply mild brightness/contrast/sharpness enhancement.
"""
from __future__ import annotations

from pathlib import Path

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

# rembg loads an ONNX model on first use; keep the session process-global so we
# pay that cost once, and only when background removal is actually used.
# Default to the lightweight "u2netp" model (~4.7MB): the full "u2net" (176MB)
# is too slow to download and too memory-hungry for a shared-cpu-1x machine.
# Override with REMBG_MODEL if the box ever gets a bigger VM.
import os

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


def _autocrop_borders(img: Image.Image, tolerance: int = 18) -> Image.Image:
    """Trim near-uniform borders (e.g. plain background) around the subject."""
    rgb = img.convert("RGB")
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
    canvas.paste(img.convert("RGB"), ((side - w) // 2, (side - h) // 2))
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


def optimize_all(src_dir: Path, dst_dir: Path, remove_bg: bool = False) -> list[dict]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    results = []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".heic"}
    for i, src in enumerate(sorted(src_dir.iterdir())):
        if src.suffix.lower() not in exts:
            continue
        dst = dst_dir / f"img_{i:02d}.jpg"
        try:
            results.append(optimize(src, dst, remove_bg))
        except Exception as exc:  # noqa: BLE001 - keep going on a bad image
            results.append({"file": src.name, "error": str(exc)})
    return results

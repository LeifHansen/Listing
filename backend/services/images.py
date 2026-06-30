"""Local image optimization tuned for eBay listing photos.

eBay recommends square-ish images with the longest side >= 1600px for zoom,
clean framing, and good lighting. This module does that without any external
service: auto-orient, trim borders, pad to square on a near-white canvas,
upscale to target, and apply mild brightness/contrast/sharpness enhancement.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps, ImageFilter

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


def optimize(src: Path, dst: Path) -> dict:
    """Optimize a single image. Returns metadata about what was done."""
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw)  # honor camera rotation
        original_size = img.size

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
    }


def optimize_all(src_dir: Path, dst_dir: Path) -> list[dict]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    results = []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".heic"}
    for i, src in enumerate(sorted(src_dir.iterdir())):
        if src.suffix.lower() not in exts:
            continue
        dst = dst_dir / f"img_{i:02d}.jpg"
        try:
            results.append(optimize(src, dst))
        except Exception as exc:  # noqa: BLE001 - keep going on a bad image
            results.append({"file": src.name, "error": str(exc)})
    return results

"""Image optimization tuned for eBay listing photos.

eBay recommends square-ish images with the longest side >= 1600px for zoom,
clean framing, and good lighting.

The pipeline (per photo): auto-orient -> background removal + studio effect,
with PHOTOROOM as the default engine (its cutout composited on white with our
soft contact shadow, then the studio enhancement pass) and ADOBE as the backup
when Photoroom fails for any reason (the Lightroom API's "studio" develop
preset followed by Photoshop's Remove Background service) -> subject-aware
square crop -> resize to target -> save. The in-house rembg model runs only
when no pro engine is configured at all, so the module still works with no
external service.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from PIL import Image, ImageEnhance, ImageOps, ImageFilter

from .. import config
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
# actually used.
#
# Model choice is a hard memory ceiling on the 2GB machine. Empirically:
#   - "u2netp" (~4MB) is the ONLY model that runs reliably here. It's light and
#     stable, but weak on dark/black or low-contrast items (can mangle them).
#   - "u2net" (full, ~176MB) and "isnet-general-use" (1024x1024) both OOM-kill
#     the machine — u2net crashed it after a couple of cutouts, isnet on the
#     first. They need a 4GB+ machine.
# Default to the stable model. For better quality on dark items, bump the VM to
# 4GB and set REMBG_MODEL=u2net (or isnet-general-use for the best edges).
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
    lightly feather the edge so it blends instead of looking stickered-on.

    Border-aware: where the subject runs off the frame it has no background
    fringe to trim, so eroding + feathering there just eats a white gutter into
    the item (the 'bleeds off frame' artifact). We restore the hardened,
    un-eroded alpha in a thin band along the frame so an edge-touching subject
    stays solid to the border. Where the subject doesn't reach an edge the
    hardened alpha is 0 there, so those borders are unchanged."""
    w, h = alpha.size
    alpha = alpha.filter(ImageFilter.MedianFilter(3))
    span = max(1, _ALPHA_HIGH - _ALPHA_LOW)
    hardened = alpha.point(lambda a: 0 if a < _ALPHA_LOW
                           else 255 if a > _ALPHA_HIGH
                           else round((a - _ALPHA_LOW) * 255 / span))
    # Erode 1px to cut the residual background fringe, then a sub-pixel feather
    # so the composited edge is smooth rather than jagged.
    refined = hardened.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.6))
    band = 3
    if w > 2 * band and h > 2 * band:
        refined.paste(hardened.crop((0, 0, w, band)), (0, 0))               # top
        refined.paste(hardened.crop((0, h - band, w, h)), (0, h - band))    # bottom
        refined.paste(hardened.crop((0, 0, band, h)), (0, 0))               # left
        refined.paste(hardened.crop((w - band, 0, w, h)), (w - band, 0))    # right
    return refined


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
    alpha = _refine_alpha(_alpha_mask(rgb, max_side=max_side))
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
    return _compose_on_white(rgb, alpha, shadow=_BG_SHADOW)


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
    return _compose_on_white(cut.convert("RGB"), alpha, shadow=_BG_SHADOW)


class PhotoroomError(ValueError):
    """Photoroom is configured but the call failed — carries a user-facing
    reason. Subclasses ValueError so the studio route's error mapping (422 +
    message in a toast) surfaces the real cause instead of a silent fallback."""


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
    buf = BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=92)
    buf.seek(0)
    try:
        resp = httpx.post(
            "https://sdk.photoroom.com/v1/segment",
            headers={"x-api-key": config.PHOTOROOM_API_KEY},
            files={"image_file": ("image.jpg", buf, "image/jpeg")},
            data={"format": "png"},
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 - network/timeout
        raise PhotoroomError(f"Couldn't reach Photoroom: {exc}") from exc
    if resp.status_code in (401, 403):
        raise PhotoroomError(
            "Photoroom rejected the API key — check the PHOTOROOM_API_KEY "
            "secret on the server (it may be missing, mistyped, or a sandbox key).")
    if resp.status_code == 402:
        raise PhotoroomError(
            "The Photoroom account is out of credits — top it up at photoroom.com.")
    if resp.status_code == 429:
        raise PhotoroomError("Photoroom is rate-limiting us — try again in a minute.")
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
    return _compose_on_white(cut.convert("RGB"), alpha, shadow=_BG_SHADOW)


def _studio_and_cutout(
        img: Image.Image) -> tuple[Image.Image, str, Optional[str], bool, Optional[str]]:
    """Background removal + studio treatment for the automatic upload path.
    Returns (image, engine, error, studio_applied, studio_error) where engine
    is 'photoroom', 'adobe', 'local', or 'none' (kept the original).

    PHOTOROOM is the default engine: its cutout goes on white with our soft
    shadow, and the caller's enhancement pass supplies the studio polish.
    ADOBE is the backup when Photoroom fails for any reason: the Lightroom
    "studio" develop preset first, then Photoshop's Remove Background.

    When at least one pro engine is configured and every one of them fails,
    we keep the ORIGINAL photo rather than let the weak local model mangle
    it — a busy background is always better than a shredded item. The local
    model only runs when no pro engine is configured at all."""
    last_err: Optional[str] = None
    if config.photoroom_ready():
        try:
            out = _photoroom_cutout(img)
            if out is not None:
                return out, "photoroom", None, False, None
        except PhotoroomError as exc:
            log.warning("photoroom: %s%s", exc,
                        " — trying the Adobe backup" if config.adobe_ready() else "")
            last_err = str(exc)
        except Exception as exc:  # noqa: BLE001 - keep-original must hold for ANY
            # failure, not just mapped API errors — a photo must never fail
            # optimize().
            log.warning("photoroom: unexpected error (%s)", exc)
            last_err = f"Background removal failed: {exc}"
    studio_applied, studio_error = False, None
    if config.adobe_ready():
        # Backup path: Lightroom studio preset first so the cutout works on a
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
    if config.photoroom_ready() or config.adobe_ready():
        log.warning("bg-removal: keeping the original photo (local model is "
                    "disabled while a pro engine is configured)")
        return (_flatten(img), "none", last_err or "background removal failed",
                studio_applied, studio_error)
    try:
        out = _cutout_on_white(img)
    except Exception as exc:  # noqa: BLE001 - bulk keeps going without the cutout
        log.warning("bg-removal: cutout failed (%s) — keeping original", exc)
        out = None
    if out is not None:
        return out, "local", None, False, None
    return _flatten(img), "none", "cutout failed", False, None


def remove_background_white(img: Image.Image) -> tuple[Image.Image, str]:
    """Photo-studio 'Remove background'. Returns (image, engine) so the editor
    can name the remover that actually ran. Raises when the cutout genuinely
    fails so the editor can tell the user instead of silently doing nothing.

    Engine order: Photoroom is the default; Adobe's Photoshop Remove
    Background is the backup when Photoroom fails for any reason. A failure
    RAISES (PhotoroomError/AdobeError are ValueErrors → the editor shows the
    real reason as a toast) instead of silently degrading to the weak local
    model — that silent fallback is how mangled cutouts kept appearing with
    no clue why. The local model runs only when no pro engine is configured,
    at the higher studio matte resolution, falling back to the fast size if a
    big inference runs out of memory."""
    if config.photoroom_ready():
        try:
            pr = _photoroom_cutout(img)
            if pr is not None:
                return pr, "photoroom"
        except PhotoroomError as exc:
            if not config.adobe_ready():
                raise  # no backup engine — surface Photoroom's reason
            log.warning("photoroom: %s — trying the Adobe backup", exc)
    if config.adobe_ready():
        out = _adobe_cutout(img)  # raises AdobeError with the reason on failure
        if out is not None:
            return out, "adobe"
    for side in (_STUDIO_MAX_SIDE, _REMBG_MAX_SIDE):
        try:
            # dark_guard off: the seller reviews the result and can Revert, so
            # show the cutout rather than hard-failing on a borderline shot.
            out = _cutout_on_white(img, max_side=side, dark_guard=False)
        except Exception as exc:  # noqa: BLE001 - retry smaller on OOM/model error
            log.warning("bg-removal: matte at %dpx failed (%s) — retrying smaller",
                        side, exc)
            continue
        if out is not None:
            return out, "local"
        break  # a clean run that erased the subject won't improve when smaller
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
        # Warm the LOCAL model directly (it still powers the highlight/subject
        # masks) — never via _studio_and_cutout, which would burn a Photoroom/
        # Adobe API credit on every boot when a key is configured.
        _cutout_on_white(Image.new("RGB", (32, 32), (200, 100, 50)))
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

        studio_applied, studio_error = False, None
        bg_removed = False
        bg_engine, bg_error = None, None
        if remove_bg:
            # Photoroom default, Adobe backup (Lightroom studio preset +
            # Photoshop cutout) — see _studio_and_cutout.
            img, bg_engine, bg_error, studio_applied, studio_error = \
                _studio_and_cutout(img)
            bg_removed = bg_engine in ("photoroom", "adobe", "local")
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


# How many photos to run through a pro engine (Photoroom/Adobe) at once. The
# per-photo work there is mostly waiting on the remote API, so a small pool
# cuts a 40-photo pile from ~minutes of serial waiting to a few overlapping
# waves — while keeping peak memory (a few 3200px working copies) modest on a
# 2GB box.
_PHOTO_BATCH_WORKERS = int(os.getenv("PHOTO_BATCH_WORKERS", "4") or "4")


def optimize_all(src_dir: Path, dst_dir: Path, remove_bg: bool = False,
                 progress=None) -> list[dict]:
    """Optimize every image in src_dir. `progress(done, total)` (optional) is
    called after each photo so long bulk jobs can show a live count.

    With a pro engine configured (Photoroom or Adobe), the set is processed a
    few photos at a time (each one is a remote API call we mostly just wait
    on). Without one, photos run one at a time — all the work is local
    CPU/RAM then, and the 2GB box can't afford concurrent model inference."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    exts = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".gif",
            ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif"}
    jobs = [(i, src) for i, src in enumerate(sorted(src_dir.iterdir()))
            if src.suffix.lower() in exts]
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

    def _one(job: tuple[int, Path]) -> dict:
        i, src = job
        try:
            result = optimize(src, dst_dir / f"img_{i:02d}.jpg", remove_bg)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad image
            result = {"file": src.name, "error": str(exc)}
        _tick()
        return result

    # Pool only when photos will actually hit a remote engine; plain local
    # optimization (no cutout) is CPU/RAM-bound and must stay serial.
    pro_engine = remove_bg and (config.photoroom_ready() or config.adobe_ready())
    workers = min(_PHOTO_BATCH_WORKERS, total) if pro_engine else 1
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

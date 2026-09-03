"""Listing photos: as shot, or with the background taken off — and upright.

Per photo the pass does exactly three things. It honours the camera's EXIF
orientation. It takes the background off when the seller asked for that:
one run of the local model, the matte hardened a little, the item composited
on white. And it sizes the result for eBay — the longest side to 1600px,
never upscaled — saved as a JPEG that carries no metadata, so the GPS of the
seller's home never rides along to a public listing.

Deliberately nothing else. The pass used to be a pipeline: a two-round vision
call to guess whether the ITEM lay sideways, a matte refined by a border
solidifier, an interior-hole repair, a contact shadow, a square crop with
subject detection, a finishing sharpen, three paid cutout APIs as alternate
engines, and a guard for every way those could go wrong. Each was reasonable
on its own; together they made every photo cost a minute and every result a
surprise, and the seller asked for the photos as shot or cut out, and to do
the rest themselves in the editor. So: the model's own matte ships, the
frame the seller composed is the frame that ships, and a wrong cutout is one
tap of Revert.

One model inference runs at a time (_INFER_LOCK): two at once double peak
memory and kill a small machine. Callers queue for the slot with a deadline —
short for a person watching the studio's spinner, long for a photo in a batch
that has nobody to tell and no retry (see INFER_WAIT_SECONDS).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFile, ImageFilter, ImageOps

from ..config import log
from ..storage import natural_key

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

TARGET_SIZE = 1600  # px, longest side per eBay's zoom recommendation
# 90 is the knee: below it JPEG starts showing ringing on the hard
# subject/white edge a cutout creates, above it the file grows for detail
# eBay's own re-encode discards anyway.
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "90") or 90)
WHITE = (255, 255, 255)

# --- the local model --------------------------------------------------------
# u2netp (~4MB) runs on a 2GB machine; isnet-general-use (~176MB) has the
# better edges and is what production selects in fly.toml (it needs the 4GB
# VM). Both are baked into the image (see Dockerfile).
_REMBG_MODEL = os.getenv("REMBG_MODEL", "u2netp").strip() or "u2netp"
# The copy handed to the model. Not a speed dial: both models normalize their
# input to a fixed tensor first (isnet 1024x1024, u2netp 320x320), so a smaller
# copy is upscaled straight back before any convolution runs. 1024 matches
# isnet, so its matte is not a downscale that got upscaled.
_REMBG_MAX_SIDE = int(os.getenv("REMBG_MAX_SIDE", "1024") or "1024")
# Harden the soft matte so the background is gone rather than faintly there.
# Alpha below LOW is dropped, above HIGH is kept solid, and the band between is
# ramped into a soft edge. Erring toward keeping the item: with no border pass
# to rebuild an edge the thresholds shaved, a wide band costs a faint fringe
# where a narrow one costs a bite out of a hem.
_ALPHA_LOW = int(os.getenv("REMBG_ALPHA_LOW", "64") or 64)
_ALPHA_HIGH = int(os.getenv("REMBG_ALPHA_HIGH", "192") or 192)
# A matte that keeps less than this share of the frame found no item — a
# close-up texture, a dark item on a dark table — and shipping it would ship
# a white square. The photo is kept as shot instead, and says so.
_MIN_FG_COVERAGE = float(os.getenv("REMBG_MIN_COVERAGE", "0.02") or 0.02)

_INFER_LOCK = threading.Lock()
# How long a caller queues for the one inference slot. A person watching the
# photo studio's spinner wants a prompt "busy, try again"; a photo in a batch
# has nobody to tell and gets no retry — giving up would silently save it with
# its background still on — so it queues for as long as a real batch takes.
INFER_WAIT_SECONDS = float(os.getenv("REMBG_WAIT_SECONDS", "25") or 25)
BATCH_INFER_WAIT_SECONDS = float(
    os.getenv("REMBG_BATCH_WAIT_SECONDS", "300") or 300)
# One inference past this is reported as pathological. ONNX cannot be
# interrupted mid-run, so this aborts nothing; it makes a slow model visible.
INFER_SLOW_SECONDS = float(os.getenv("REMBG_SLOW_SECONDS", "20") or 20)

_rembg_session = None
_model_ready = False
_model_load_seconds: float = 0.0
_last_infer_seconds: float = 0.0


class CutoutBusy(RuntimeError):
    """The inference slot didn't free up in time. Retryable — 503, not 500:
    nothing is broken, the machine is just full."""


class Stopped(Exception):
    """Raised inside a photo batch that its caller called off mid-run. Nothing
    is left half-written: optimize() renames its result into place, so the
    photos already finished stay valid and a stopped run is still resumable."""


def engine_state() -> dict:
    """What the readiness probe needs to know about the local model."""
    return {"model": _REMBG_MODEL, "loaded": _model_ready,
            "busy": _INFER_LOCK.locked(),
            "last_inference_seconds": round(_last_infer_seconds, 2),
            "model_load_seconds": round(_model_load_seconds, 2)}


def _infer_threads() -> int:
    """Threads onnxruntime may use for one inference: one fewer than the CPUs
    we can see (REMBG_THREADS overrides). uvicorn still has to answer its
    health check while a batch runs, and a machine that misses those checks
    is replaced by the platform, which kills the batch."""
    forced = int(os.getenv("REMBG_THREADS", "0") or 0)
    if forced > 0:
        return forced
    try:
        cpus = len(os.sched_getaffinity(0))
    except AttributeError:  # not Linux
        cpus = os.cpu_count() or 1
    return max(1, cpus - 1)


def _mask(rgb: Image.Image, wait: Optional[float] = None) -> Image.Image:
    """The model's subject matte (mode L) at rgb's size.

    `wait` is how long to queue for the inference slot before giving up with
    CutoutBusy; it defaults to the batch deadline."""
    global _rembg_session, _model_ready, _model_load_seconds, _last_infer_seconds
    scale = min(1.0, _REMBG_MAX_SIDE / max(rgb.size))
    small = (rgb.resize((max(1, round(rgb.width * scale)),
                         max(1, round(rgb.height * scale))), Image.LANCZOS)
             if scale < 1 else rgb)
    if not _INFER_LOCK.acquire(
            timeout=BATCH_INFER_WAIT_SECONDS if wait is None else wait):
        raise CutoutBusy(
            "The background remover is working through another batch. "
            "Give it a moment and try again.")
    try:
        # Imported inside the lock, after the wait: a machine that is already
        # full answers "busy" without paying to pull in onnxruntime first.
        loading = time.monotonic()
        from rembg import new_session, remove
        if _rembg_session is None:
            # Set before the session is built: rembg reads this when it
            # constructs the SessionOptions and never looks again.
            os.environ.setdefault("OMP_NUM_THREADS", str(_infer_threads()))
            _rembg_session = new_session(_REMBG_MODEL)
            _model_ready = True
            _model_load_seconds = time.monotonic() - loading
            log.info("bg-removal: model %s ready in %.1fs (%s inference "
                     "thread(s), %dpx)", _REMBG_MODEL, _model_load_seconds,
                     os.environ.get("OMP_NUM_THREADS", "auto"), _REMBG_MAX_SIDE)
        # The clock starts here, not when the lock was taken: the load above
        # is paid once per process and is not what "inference" means to the
        # probe or to the slow-inference warning below.
        started = time.monotonic()
        try:
            alpha = remove(small, session=_rembg_session, only_mask=True).convert("L")
        finally:
            _last_infer_seconds = time.monotonic() - started
    finally:
        _INFER_LOCK.release()
    if _last_infer_seconds > INFER_SLOW_SECONDS:
        log.warning("bg-removal: inference took %.1fs (model=%s, %s threads)",
                    _last_infer_seconds, _REMBG_MODEL,
                    os.environ.get("OMP_NUM_THREADS", "auto"))
    if alpha.size != rgb.size:
        # BILINEAR, not LANCZOS: LANCZOS overshoots at a hard edge and rings a
        # faint halo of the old background back in.
        alpha = alpha.resize(rgb.size, Image.BILINEAR)
    return alpha


def _harden(alpha: Image.Image) -> Image.Image:
    """Drop the faint background, keep the item solid, ramp the band between."""
    low, high = _ALPHA_LOW, max(_ALPHA_HIGH, _ALPHA_LOW + 1)
    span = high - low
    return alpha.point([0 if a <= low else 255 if a >= high
                        else (a - low) * 255 // span for a in range(256)])


def cutout(rgb: Image.Image, wait: Optional[float] = None) -> Optional[Image.Image]:
    """The item on white, or None when the model found no item to keep.

    Raises CutoutBusy when the inference slot is taken for longer than
    `wait`; any other failure raises as itself so a caller can say why."""
    alpha = _harden(_mask(rgb, wait=wait))
    kept = alpha.point(lambda a: 255 if a >= 128 else 0)
    coverage = (sum(kept.histogram()[128:]) / (rgb.width * rgb.height))
    if coverage < _MIN_FG_COVERAGE:
        return None
    return Image.composite(rgb, Image.new("RGB", rgb.size, WHITE), alpha)


def _flatten(img: Image.Image) -> Image.Image:
    """Any transparency composited onto WHITE, as opaque RGB.

    A bare `.convert("RGB")` paints transparent pixels BLACK, so a PNG that
    arrived already cut out (an iPhone "lift subject" shot, another tool's
    export) would reach the model, and the listing, as an item on a black
    field."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, WHITE)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return img.convert("RGB") if img.mode != "RGB" else img


def _load(src: Path) -> tuple[Image.Image, tuple[int, int]]:
    """The photo as the camera meant it — upright per its EXIF, opaque, and no
    larger than TARGET_SIZE on its longest side — plus the size it was shot
    at. Nothing downstream needs more pixels than the output has, so a JPEG
    is asked to decode at a reduced scale up front (`draft`): a 12MP phone
    photo then never exists at full size in memory at all."""
    with Image.open(src) as raw:
        shot = raw.size
        # Orientation 5-8 mean the sensor was sideways, so the photo the
        # seller took is the stored one turned — report that size.
        if raw.getexif().get(274, 1) in (5, 6, 7, 8):
            shot = (shot[1], shot[0])
        w, h = raw.size
        scale = TARGET_SIZE / max(w, h)
        if scale < 1:
            raw.draft(None, (max(1, round(w * scale)), max(1, round(h * scale))))
        img = ImageOps.exif_transpose(raw)
        img.load()
    img = _flatten(img)
    if max(img.size) > TARGET_SIZE:
        img.thumbnail((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    return img, shot


def optimize(src: Path, dst: Path, remove_bg: bool = False) -> dict:
    """One photo: as shot, or cut out on white; sized for eBay; no EXIF.
    Returns what was done. A cutout that finds nothing keeps the photo as shot
    and says so in `bg_error`, so the caller can give the charge back."""
    img, shot = _load(src)
    bg_removed, bg_error = False, None
    if remove_bg:
        try:
            out = cutout(img)
        except Exception as exc:  # noqa: BLE001 - a photo must never fail for its cutout
            log.warning("bg-removal: keeping %s as shot (%s)", src.name, exc)
            out, bg_error = None, f"Background removal failed: {exc}"
        if out is not None:
            img, bg_removed = out, True
        elif not bg_error:
            bg_error = ("The background remover found no item in this photo "
                        "— it was kept as shot.")
    dst = dst.with_suffix(".jpg")
    # No exif= argument, on purpose: the saved file carries no metadata, so the
    # GPS coordinates of the seller's home never reach a public listing
    # (test_export_pipeline.py holds that line). Written via a temp file and
    # renamed: a photo that exists is a photo that is FINISHED. optimize_all
    # treats an existing output as done — that is what makes an interrupted
    # batch resumable — so a torn JPEG left by a machine that died mid-save
    # must never be where a resume would adopt it.
    tmp = dst.with_name(f".{dst.name}.{os.getpid():x}.tmp")
    try:
        img.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    out = {"file": dst.name, "original_size": shot, "output_size": img.size,
           "background_removed": bg_removed}
    if bg_removed:
        out["bg_engine"] = "local"
    if bg_error:
        out["bg_error"] = bg_error
    return out


_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".gif",
         ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif"}


def optimize_all(src_dir: Path, dst_dir: Path, remove_bg: bool = False,
                 progress=None, should_stop=None) -> list[dict]:
    """Every image in src_dir, in shooting order, as img_NNN.jpg in dst_dir.

    `progress(done, total)` is called after each photo. `should_stop()` is
    asked before each photo starts; True raises Stopped rather than working
    through the rest of the pile.

    Photos whose output already exists are left alone and reported as
    {"file", "reused"}: a batch that died halfway — a deploy, an OOM, a
    machine the platform replaced — starts again without redoing the cutouts
    it finished. Trusting an existing output is safe because optimize()
    renames its result into place. Positions come from the naturally sorted
    source list, so the same photo maps to the same output name every run."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(i, src) for i, src
            in enumerate(sorted(src_dir.iterdir(), key=lambda p: natural_key(p.name)))
            if src.suffix.lower() in _EXTS]
    todo = [(i, src, dst) for i, src in jobs
            if not (dst := dst_dir / f"img_{i:03d}.jpg").exists()]
    if len(todo) < len(jobs):
        log.info("images: %d of %d photo(s) already optimized — resuming",
                 len(jobs) - len(todo), len(jobs))
    pending = [i for i, _src, _dst in todo]
    pending_set = set(pending)
    results = {i: {"file": f"img_{i:03d}.jpg", "reused": True}
               for i, _src in jobs if i not in pending_set}
    if progress and not todo and jobs:
        # Nothing left to do, so nothing below will tick. Report the real
        # count once rather than leaving a resumed batch's bar reading zero.
        try:
            progress(len(jobs), len(jobs))
        except Exception:  # noqa: BLE001 - progress is display-only
            pass
    results.update(zip(pending, optimize_batch(
        [(src, dst) for _i, src, dst in todo], remove_bg=remove_bg,
        progress=progress, should_stop=should_stop,
        done_already=len(results), grand_total=len(jobs))))
    return [results[i] for i, _src in jobs]


def optimize_batch(jobs: list[tuple[Path, Path]], remove_bg: bool = False,
                   progress=None, done_already: int = 0,
                   grand_total: int = 0, should_stop=None) -> list[dict]:
    """(src, dst) jobs in order; a failed photo yields {"file", "error"}
    instead of raising. Serial on purpose: inference is single-flight, and
    what is left around it — a decode, a composite, an encode — is not worth
    a thread pool's memory.

    `done_already`/`grand_total` describe work this call is NOT doing — the
    photos a resumed batch found already finished — so `progress` keeps
    saying "38 of 40" across a restart instead of dropping to "0 of 2"."""
    total = grand_total or len(jobs)
    done = done_already
    results = []
    for src, dst in jobs:
        if should_stop is not None and should_stop():
            raise Stopped()
        try:
            result = optimize(src, dst, remove_bg)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad image
            result = {"file": src.name, "error": str(exc)}
        results.append(result)
        done += 1
        if progress:
            try:
                progress(done, total)
            except Exception:  # noqa: BLE001 - progress is display-only
                pass
    return results


def warm() -> None:
    """Pre-load the model on a tiny image, from a startup thread, so the first
    real photo does not pay for importing onnxruntime and reading a 176MB
    file (/api/ready reports that cost as model_load_seconds)."""
    try:
        tile = Image.new("RGB", (64, 64), (235, 235, 235))
        tile.paste(Image.new("RGB", (30, 30), (200, 100, 50)), (17, 17))
        _mask(tile)
        log.info("images: background-removal model warmed")
    except Exception as exc:  # noqa: BLE001 - warmup is best-effort
        log.warning("images: warmup failed (will lazy-load on first use): %s", exc)


# --- the photo studio --------------------------------------------------------

def remove_background_white(img: Image.Image) -> tuple[Image.Image, str]:
    """The studio's "Remove background": the item on white, and the name of
    the engine that did it. Raises ValueError when the model found nothing to
    keep, so the editor can tell the seller instead of silently doing
    nothing, and CutoutBusy when the slot is taken (the editor's wait is the
    short one: a person is watching)."""
    out = cutout(_flatten(img), wait=INFER_WAIT_SECONDS)
    if out is None:
        raise ValueError(
            "Couldn't separate this photo from its background — it's likely "
            "a close-up, dark, or low-contrast shot. Try cropping in tighter, "
            "or paint the background out with the white brush.")
    return out, "local"


def _subject(img: Image.Image) -> tuple[Image.Image, Image.Image]:
    rgb = _flatten(img)
    return rgb, _harden(_mask(rgb, wait=INFER_WAIT_SECONDS))


def auto_clean(img: Image.Image) -> Image.Image:
    """Whiten everything outside the item. The mask is grown a little and
    feathered so the edge stays soft and nothing of the item is eaten."""
    rgb, mask = _subject(img)
    mask = mask.point(lambda a: 255 if a >= 96 else 0)
    mask = mask.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(2))
    return Image.composite(rgb, Image.new("RGB", rgb.size, WHITE), mask)


def smart_crop(img: Image.Image, margin: float = 0.05) -> Optional[Image.Image]:
    """Crop to the item plus a margin. None when there is no confident item
    or the frame is already tight, so the caller can say "nothing to crop"
    instead of degrading the photo."""
    rgb, mask = _subject(img)
    bbox = mask.point(lambda a: 255 if a >= 128 else 0).getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox
    mx, my = int((right - left) * margin), int((bottom - top) * margin)
    box = (max(0, left - mx), max(0, top - my),
           min(rgb.width, right + mx), min(rgb.height, bottom + my))
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.92 * rgb.width * rgb.height:
        return None  # already tight — don't churn the photo for a <8% trim
    return rgb.crop(box)


# --- copies for the AI ---------------------------------------------------------

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

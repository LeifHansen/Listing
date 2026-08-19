"""Content-aware photo orientation.

EXIF only records how the CAMERA was held, so `exif_transpose` fixes nothing
when the item itself was laid down sideways in the frame — the classic
"shirt on its side" listing photo. There's no metadata to read for that: the
only way to know which way is up is to look at the picture.

So this module asks a vision model, cheaply: small thumbnails, batched several
photos per call, running in parallel, on a small/fast model. It returns the
clockwise rotation each photo needs, and the image pipeline applies it BEFORE
background removal and the square crop so everything downstream — the cutout,
the framing, the AI's read of the item, and eBay — sees an upright photo.

Entirely best-effort: any failure returns "no rotation", which is exactly
today's behavior. A wrong guess is one tap of the editor's rotate button.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config
from ..config import log
from . import claude_ai

# Photos per vision call. Small enough that one bad response only costs a
# handful of photos their auto-rotation, big enough to keep a 250-photo batch
# to ~20 calls.
_BATCH = 12
_WORKERS = 3
# Thumbnail size sent to the model. Orientation is a coarse, whole-image
# judgement — 384px is plenty and keeps the token cost near-nothing.
_THUMB = 384
_VALID = (0, 90, 180, 270)

_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{ "photos": [ {"photo": <1-based number>, "rotate": 0|90|180|270} ] }

"rotate" is how many degrees CLOCKWISE the photo must be turned so it looks
like a proper product photo on an e-commerce site (eBay / Amazon). Picture
the reference image: a shirt laid flat, collar and shoulders at the TOP of
the frame, hem at the bottom, sleeves out to the sides; printed graphics and
text reading normally. Include an entry for EVERY photo, using 0 when it
already looks like that.

How to judge which way is up:
- Clothing: collar/shoulders at the TOP, hem at the bottom. A shirt lying
  with its collar pointing left needs 90; pointing right needs 270. Chest
  graphics and brand text read normally when the photo is right.
- Shoes: sole down. Bottles/mugs/boxes: standing as on a table.
- Books/labels/tags: printed text reading left-to-right, horizontal.
- People/mannequins: head at the top.

Rules — read carefully, mistakes here are worse than doing nothing:
- DEFAULT IS 0. If the photo plausibly already matches the reference look,
  answer 0. Only rotate when the evidence is unmistakable.
- 180 is EXTREMELY RARE. Answer 180 only when the item is unambiguously
  upside down — collar clearly at the BOTTOM with hem at the top, or text
  clearly reading upside down. Never answer 180 because a graphic looks odd
  or the lighting is confusing; when torn between 0 and 180, answer 0.
- A close-up of a detail (fabric texture, a stitch, a tag, a logo without
  clear context) is 0 unless readable text settles it.
- Judge each photo independently; a set often mixes orientations.
"""

_VERIFY_SCHEMA = """
Return ONLY a JSON object (no markdown fences):
{ "photos": [ {"photo": <1-based number>, "upright": true|false} ] }

Each of these photos has ALREADY been auto-rotated. Confirm each one now
looks correctly upright, like a product photo on an e-commerce site: clothing
with the collar/shoulders at the top and hem at the bottom, text and graphics
reading normally, items standing naturally. Answer upright=false if the photo
looks sideways or upside down — false means the rotation was WRONG and will
be cancelled, so when unsure answer false.
"""


def _enabled() -> bool:
    flag = os.getenv("AUTO_ORIENT", "on").strip().lower()
    return flag not in ("off", "0", "false", "no") and config.anthropic_ready()


def _verify_enabled() -> bool:
    # The verify pass is a second vision round-trip that only runs when the
    # detect pass proposed at least one rotation. ORIENT_VERIFY=off trades
    # that safety net for one less serial API wait per upload that proposes
    # turns — the detect prompt is already tuned conservative ("DEFAULT IS 0"),
    # and a wrong turn stays one tap of the editor's rotate button.
    flag = os.getenv("ORIENT_VERIFY", "on").strip().lower()
    return flag not in ("off", "0", "false", "no")


def _model() -> str:
    # A small, fast model is plenty for "which way is up" — and keeps a
    # 250-photo batch cheap. Override with ORIENT_MODEL.
    return os.getenv("ORIENT_MODEL", "claude-haiku-4-5-20251001").strip()


def _detect_batch(batch: list[Path]) -> dict[str, int]:
    """{filename: clockwise degrees} for one batch. {} on any failure."""
    from . import images
    try:
        content: list[dict] = []
        for p in batch:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": _b64(images.thumb_jpeg(p, side=_THUMB))}})
        content.append({"type": "text", "text": (
            f"These are photos 1 to {len(batch)} of secondhand items being "
            "listed for sale. For each, say how it must be rotated to appear "
            "upright.\n" + _SCHEMA)})
        resp = claude_ai.client().messages.create(
            model=_model(), max_tokens=400,
            messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        data = claude_ai.extract_json(text)
    except Exception as exc:  # noqa: BLE001 - orientation is an enhancement
        log.info("auto-orient: batch skipped (%s)", exc)
        return {}
    out: dict[str, int] = {}
    for entry in (data.get("photos") or []):
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("photo", 0)) - 1
            deg = int(entry.get("rotate", 0)) % 360
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch) and deg in _VALID and deg:
            out[batch[idx].name] = deg
    return out


def _b64(data: bytes) -> str:
    import base64
    return base64.standard_b64encode(data).decode("ascii")


def _verify_batch(proposals: list[tuple[Path, int]]) -> dict[str, int]:
    """Second, independent look: each proposed rotation is APPLIED to the
    thumbnail and the model must confirm the result looks upright. Only
    confirmed turns survive — an unconfirmed one is cancelled, never applied.
    {} on any failure (= apply nothing from this batch)."""
    from PIL import Image, ImageOps
    from . import images as _images

    try:
        content: list[dict] = []
        for path, deg in proposals:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((_THUMB, _THUMB), Image.LANCZOS)
                content.append(_pil_content_block(
                    im.transpose(_images.CW_TRANSPOSE[deg])))
        content.append({"type": "text", "text": (
            f"These are photos 1 to {len(proposals)} of secondhand items, "
            "after auto-rotation.\n" + _VERIFY_SCHEMA)})
        resp = claude_ai.client().messages.create(
            model=_model(), max_tokens=300,
            messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        answers = claude_ai.extract_json(text).get("photos") or []
    except Exception as exc:  # noqa: BLE001 - no confirmation = no rotation
        log.info("auto-orient: verify pass failed (%s) — applying nothing", exc)
        return {}
    upright = {}
    for entry in answers:
        if isinstance(entry, dict):
            try:
                upright[int(entry.get("photo", 0)) - 1] = bool(entry.get("upright"))
            except (TypeError, ValueError):
                continue
    return {path.name: deg for i, (path, deg) in enumerate(proposals)
            if upright.get(i)}


def _pil_content_block(img) -> dict:
    from io import BytesIO
    import base64
    buf = BytesIO()
    img.save(buf, "JPEG", quality=80)
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.standard_b64encode(buf.getvalue()).decode("ascii")}}


def detect_rotations(paths: list[Path]) -> dict[str, int]:
    """{filename: clockwise degrees needed} for the photos that need turning.

    Two-step for safety: a detect pass proposes turns, then a verify pass
    looks at the ROTATED image and must confirm it's upright before anything
    is applied — a wrong rotation (the upside-down-listing failure) is worse
    than leaving a photo as shot, so unconfirmed proposals are cancelled.
    Files already upright are simply absent. Never raises."""
    files = [p for p in paths if p.is_file()]
    if not files or not _enabled():
        return {}
    batches = [files[i:i + _BATCH] for i in range(0, len(files), _BATCH)]
    proposed: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(batches))) as pool:
        for part in pool.map(_detect_batch, batches):
            proposed.update(part)
    if not proposed:
        return {}
    if not _verify_enabled():
        log.info("auto-orient: %d proposed, applied without verify "
                 "(ORIENT_VERIFY=off), of %d photos", len(proposed), len(files))
        return proposed
    by_name = {p.name: p for p in files}
    items = [(by_name[n], deg) for n, deg in proposed.items() if n in by_name]
    chunks = [items[i:i + _BATCH] for i in range(0, len(items), _BATCH)]
    rotations: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(chunks))) as pool:
        for part in pool.map(_verify_batch, chunks):
            rotations.update(part)
    dropped = len(proposed) - len(rotations)
    log.info("auto-orient: %d proposed, %d confirmed, %d cancelled by verify "
             "(of %d photos)", len(proposed), len(rotations), dropped, len(files))
    return rotations

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

"rotate" is how many degrees CLOCKWISE the photo must be turned so the item
appears the right way up, as a buyer would expect to see it in a product
listing. Include an entry for EVERY photo, using 0 when it's already upright.

How to judge which way is up:
- Clothing: collar/shoulders at the TOP, hem at the bottom. A shirt lying with
  its collar pointing left needs 90; pointing right needs 270.
- Shoes: sole down, toe pointing sideways.
- Bottles/mugs/boxes/appliances: standing as they would sit on a table.
- Books/boxes/labels/tags: printed text reading left-to-right, horizontal.
- Flat-lay photos shot from directly above still have an intended up: use the
  item's own top (collar, cap, spine, the way the print reads).
- People/mannequins: head at the top.

Rules:
- Use 0 whenever the photo is already upright or you genuinely can't tell —
  never guess a rotation just to have one. A wrongly rotated photo is worse
  than an untouched one.
- Judge each photo independently; a set often mixes orientations.
- A close-up of a detail (fabric texture, a stitch, a logo with no clear up)
  is 0 unless text or a tag makes the direction obvious.
"""


def _enabled() -> bool:
    flag = os.getenv("AUTO_ORIENT", "on").strip().lower()
    return flag not in ("off", "0", "false", "no") and config.anthropic_ready()


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
        resp = claude_ai._client().messages.create(
            model=_model(), max_tokens=400,
            messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        data = claude_ai._extract_json(text)
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


def detect_rotations(paths: list[Path]) -> dict[str, int]:
    """{filename: clockwise degrees needed} for the photos that need turning.

    Files already upright are simply absent. Never raises: a failed batch
    contributes nothing, leaving those photos as they were shot.
    """
    files = [p for p in paths if p.is_file()]
    if not files or not _enabled():
        return {}
    batches = [files[i:i + _BATCH] for i in range(0, len(files), _BATCH)]
    rotations: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(batches))) as pool:
        for part in pool.map(_detect_batch, batches):
            rotations.update(part)
    if rotations:
        log.info("auto-orient: straightening %d of %d photo(s)",
                 len(rotations), len(files))
    return rotations

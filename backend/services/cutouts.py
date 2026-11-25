"""A matte made somewhere else, turned into the listing photo.

The background is taken off on the seller's own device now -- Apple's Vision
framework on an iPhone, Google's ML Kit on Android, a BiRefNet model in the
browser -- and none of them is asked for a finished picture. Each answers one
question, the same one: how opaque is every pixel of THIS photo. That matte
comes here, and the server does the rest with exactly the code Photoroom's
cutouts go through (images.compose: the item on the seller's colour under a
soft contact shadow). Three engines, one look; and no second compositing
pipeline to drift away from the first.

What a matte has to be before it is believed:

  * a picture at all, no larger than MAX_SIDE a side, and the SHAPE of the
    photo it was made from (a mask squashed to some other aspect is a mask of
    some other photo);
  * of the photo whose bytes it was computed from -- the route checks the
    SHA-256 the client was handed (see /api/image/file) before anything is
    written, and answers 409 when the photo changed in between;
  * keeping something. "Found nothing" is the one gate a matte answers that
    is engine-independent and cheap to be sure of (images._MIN_FG_COVERAGE).
    Below it, a flat rectangular item is still cut to its own scanned edge,
    as the Photoroom path does; anything else is refused with a sentence.

Nothing else is judged. The shape and solidity guards that once stood behind
the on-server model were thresholds fitted to that model's failures, and they
are what refused necklaces and guitars on flawless mattes.

SOFT, NEVER BINARISED. A matte is upscaled to the photo (bicubic), its
extremes are clamped -- the faint haze an engine leaves across a backdrop,
and the not-quite-opaque speckle inside an item, both read as dirt on white --
and the band between is kept exactly as soft as the engine made it. Hair,
fur, lace and the edge of a glass are in that band. A light feather is added
only where the matte was enlarged by more than half again, where the
interpolation itself leaves stair-steps.

A PICTURE IS STILL NEVER CUT INTO. A device's matte of a painting is the
subject lifted out of it, like anyone's. It is used only to LOCATE the
painting's edge (images.art_cut with the matte as its locator), the shape is
refused unless it is a rectangle at some angle, and what ships is a solid
quad. There is no path here that removes a pixel from inside a picture.

Pillow only -- no numpy, no model -- so the `cutout` CI job proves it on the
same four packages it proves the rest of the photo pass on.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter

from ..config import log
from . import images

MAX_SIDE = 4096
# Clamp below/above these to fully transparent/opaque; linear between.
_LO, _HI = 8, 247
# How far a matte's aspect may stray from its photo's. One percent covers an
# engine rounding a dimension; anything more is a matte of another frame.
_ASPECT = 0.01
# Enlarged by more than this, a matte gets a light feather.
_FEATHER_ABOVE = 1.5
_FEATHER_RADIUS = 0.6

_LEVELS = [0 if v <= _LO else 255 if v >= _HI
           else round((v - _LO) * 255 / (_HI - _LO)) for v in range(256)]

NOTHING_KEPT = ("The background remover found no item in this photo — it "
                "was kept as shot.")
BAD_MASK = ("That cutout came back unreadable — the photo was kept as shot. "
            "Try again.")
WRONG_SHAPE = ("That cutout was made for a different version of this photo — "
               "it was kept as shot. Try again.")

_HEX = re.compile(r"#?([0-9a-fA-F]{6})")


class MaskRefused(ValueError):
    """A matte that cannot be used, with a sentence for the seller. A
    ValueError, so a route answers 422 with the message."""


class NothingKept(MaskRefused):
    """The matte kept (next to) nothing of the photo."""


# --- colours ----------------------------------------------------------------------

def parse_colour(value) -> Optional[tuple[int, int, int]]:
    """'#rrggbb' (or 'rrggbb') as an RGB tuple, or None for anything else."""
    m = _HEX.fullmatch(str(value or "").strip())
    if not m:
        return None
    h = m.group(1)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def colour_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


# --- the matte ----------------------------------------------------------------------

def read_mask(data: bytes) -> Image.Image:
    """The uploaded matte as one 8-bit channel. A PNG with real transparency
    is read by its alpha (an engine that answered with a cut-out); anything
    else by its brightness (a greyscale matte, or one drawn into RGB by a
    canvas that cannot encode a single channel)."""
    if not data:
        raise MaskRefused(BAD_MASK)
    try:
        img = Image.open(BytesIO(data))
        if max(img.size) > MAX_SIDE:
            raise MaskRefused(BAD_MASK)
        img.load()
    except MaskRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - whatever came is not a picture
        raise MaskRefused(BAD_MASK) from exc
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        alpha = rgba.getchannel("A")
        if alpha.getextrema() != (255, 255):
            return alpha
        return rgba.convert("L")
    return img.convert("L")


def check_shape(mask: Image.Image, size: tuple[int, int]) -> None:
    """Refuse a matte whose aspect is not the photo's."""
    w, h = size
    mw, mh = mask.size
    if min(w, h, mw, mh) <= 0:
        raise MaskRefused(WRONG_SHAPE)
    photo, matte = w / h, mw / mh
    # One pixel of rounding on the smaller side is always allowed.
    slack = max(_ASPECT, 1.0 / min(mw, mh))
    if abs(matte - photo) / photo > slack:
        raise MaskRefused(WRONG_SHAPE)


def refine_alpha(mask: Image.Image, size: tuple[int, int]) -> Image.Image:
    """`mask` at the photo's size: resized bicubically, extremes clamped,
    soft band kept, feathered only where it was enlarged a lot."""
    m = mask if mask.mode == "L" else mask.convert("L")
    grow = max(size[0] / max(1, m.width), size[1] / max(1, m.height))
    if m.size != size:
        m = m.resize(size, Image.BICUBIC)
    m = m.point(_LEVELS)
    if grow > _FEATHER_ABOVE:
        m = m.filter(ImageFilter.GaussianBlur(_FEATHER_RADIUS))
    return m


def coverage(alpha: Image.Image) -> float:
    return images._coverage(alpha, alpha.size)


def cut_from_mask(rgb: Image.Image, mask: Image.Image, *, art: bool = False,
                  background: tuple[int, int, int] = images.WHITE
                  ) -> images.Cut:
    """The listing photo made from `rgb` and a matte of it from elsewhere.

    Raises MaskRefused (or NothingKept) with a sentence for the seller."""
    check_shape(mask, rgb.size)
    alpha = refine_alpha(mask, rgb.size)
    if art:
        cut = images.art_cut(rgb, locate=lambda _rgb: (alpha, "device"),
                             background=background)
        if cut is None:
            raise MaskRefused(images.ART_NO_BORDER_KEPT_AS_SHOT)
        return cut
    if coverage(alpha) < images._MIN_FG_COVERAGE:
        # The same rescue the Photoroom path makes for a flat rectangular
        # thing an engine found nothing in: a tray, a sign, a boxed set.
        rescued = images._rectangle_cut(rgb, "border", background)
        if rescued is not None:
            return rescued
        raise NothingKept(NOTHING_KEPT)
    return images.Cut(images.compose(rgb, alpha, background), alpha, "device")


# --- the photo ------------------------------------------------------------------------

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode(data: bytes) -> Image.Image:
    """Source bytes as an opaque RGB image."""
    img = Image.open(BytesIO(data))
    img.load()
    return images.flatten(img)


def source_path(path: Path) -> Path:
    """Where a matte for the optimized photo `path` must be computed from:
    the photo as shot behind a current cutout, else the photo itself. The
    same answer GET /api/image/file?which=source gives."""
    return images.as_shot(path)


def _write_bytes_atomic(dst: Path, data: bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid():x}.{threading.get_ident():x}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_cut(path: Path, source: bytes, cut: images.Cut) -> None:
    """Put `cut` in place as the working copy of `path`, keeping the photo
    it was cut FROM and the matte it was cut THROUGH beside it.

    The order is the whole contract (images.as_shot, images.current_mask):
    the working copy first, then the source bytes as the as-shot copy --
    byte for byte, so a second cut costs no JPEG generation -- then the
    matte. Each of the two kept files is believed only while it is at least
    as new as the working copy, so writing them AFTER it is what makes them
    current, and every later edit, rotate or restore makes them stale
    without needing to know they exist. The caller has already snapshot the
    old working copy to history."""
    images._save_atomic(cut.image, path, quality=images.JPEG_QUALITY,
                        optimize=True)
    if path.parent.name != "optimized":
        return
    _write_bytes_atomic(path.parent.parent / "as_shot" / path.name, source)
    images._keep_mask(path, cut.alpha)


def parts(path: Path) -> Optional[tuple[Image.Image, Image.Image]]:
    """(photo as shot, matte) behind the optimized photo `path` while BOTH
    still speak for it, else None -- what re-colouring a cutout or handing
    it over transparent needs."""
    shot = images.as_shot(path)
    mask = images.current_mask(path)
    if shot == path or mask is None:
        return None
    try:
        rgb = decode(shot.read_bytes())
        with Image.open(mask) as m:
            alpha = m.convert("L")
            alpha.load()
    except Exception as exc:  # noqa: BLE001 - a cache that won't read is no cache
        log.info("cutout parts unreadable for %s (%s)", path.name, exc)
        return None
    if alpha.size != rgb.size:
        alpha = alpha.resize(rgb.size, Image.BICUBIC)
    return rgb, alpha


def transparent_png(rgb: Image.Image, alpha: Image.Image) -> bytes:
    """The item alone, on nothing: an RGBA PNG."""
    out = rgb.convert("RGBA")
    out.putalpha(alpha if alpha.size == rgb.size
                 else alpha.resize(rgb.size, Image.BICUBIC))
    buf = BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()

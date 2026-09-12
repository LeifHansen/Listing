"""Listing photos: as shot, or with the background taken off — and upright.

Per photo the pass does four things. It honours the camera's EXIF
orientation. It turns the ITEM upright when it was photographed lying
sideways or on its head — which EXIF knows nothing about, so a vision model
is asked; see services/orient for how, and for why the last one was wrong
about everything that was not a shirt. It takes the background off when the
seller asked for that: one run of the local model, the matte hardened a
little, the item composited on white under a soft contact shadow — except on
a close-up of PART of an item, a tag or a label or a stitch, where there is
no background to take off and the model, asked anyway, deletes the item and
keeps the label. And it sizes the result for eBay — the longest side to
1600px, never upscaled — saved as a JPEG that carries no metadata, so the GPS
of the seller's home never rides along to a public listing.

Deliberately nothing else. The pass used to be a pipeline: the orientation
guess in its shirt-shaped first form, a matte refined by a border
solidifier, an interior-hole repair, a square crop with subject detection, a
finishing sharpen, three paid cutout APIs as alternate engines, and a guard
for every way those could go wrong. Each was reasonable on its own; together
they made every photo cost a minute and every result a surprise, and the
seller asked for the photos as shot or cut out, and to do the rest
themselves in the editor. So: the model's own matte ships, the frame the
seller composed is the frame that ships, and a wrong cutout is one tap of
Revert. Orientation came back on its own and on its own terms — rebuilt for
objects, applied only when two different looks agree, never in the way of
an upload — and a wrong turn is one tap of the rotate button.

One model inference runs at a time (_INFER_LOCK): two at once double peak
memory and kill a small machine. Callers queue for the slot with a deadline —
short for a person watching the studio's spinner, long for a photo in a batch
that has nobody to tell and no retry (see INFER_WAIT_SECONDS).
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from PIL import (Image, ImageChops, ImageFile, ImageFilter, ImageOps,
                 ImageStat)

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
# Clockwise degrees -> the exact (lossless) Pillow transpose for it. Pillow
# names its rotations counter-clockwise.
CW_TRANSPOSE = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}

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
#
# It sat at 0.02 and refused a necklace. A thin product covers very little of
# the frame it is laid out in and is still perfectly obviously an item: run
# through the real model, a chain on a sweep scores 0.015, a belt 0.052, a
# bangle 0.065. What "found nothing" actually scores is not near those — an
# empty backdrop is 0.0000, a close-up of fabric 0.0009, a speck of dust
# 0.0017. The two populations are an order of magnitude apart and the floor
# was sitting on the wrong side of the gap; it now sits in the middle of it.
_MIN_FG_COVERAGE = float(os.getenv("REMBG_MIN_COVERAGE", "0.005") or 0.005)

# --- and whether what it kept is an OBJECT ----------------------------------
#
# Coverage above is a floor on HOW MUCH survives, and for a long time it was
# the only question asked. It cannot see the failure that actually reaches
# sellers, which is a matte that keeps plenty and keeps the wrong thing.
#
# The report that produced these two numbers: five photos of one framed
# watercolour. The two wide shots came out right. The three close-ups came
# back as smears of brushwork and a tree floating on white — the frame, the
# mount and most of the painting deleted. Every one of them passed the
# coverage floor comfortably, because a fifth of the frame did survive; it
# was simply a fifth made of the wrong pixels.
#
# That is the salient-object model working exactly as built and being pointed
# at the wrong kind of picture. Handed a photograph OF A PICTURE, it finds the
# subject the picture depicts — the tree, the boat — rather than the physical
# thing being sold, and dutifully deletes the artwork around it. Art, prints,
# posters, book covers, trading cards, patterned fabric and printed packaging
# are all the same trap, and they are a large share of what resells.
#
# So a second question, about SHAPE rather than quantity. One product is one
# object, and a matte of it is one solid blob:
#
#   * the largest connected region must be most of what was kept. Confetti —
#     a dozen brushstrokes scattered over the frame — fails here.
#   * what was kept must fill its own bounding box. A tree at one edge and a
#     boat at the other span nearly the whole photo while covering little of
#     it; a framed picture, kept properly, fills its box almost entirely.
#
# Both are deliberately generous, because the two error directions are not
# equal: a cutout wrongly refused leaves the seller's photo EXACTLY AS SHOT
# and says why, while a cutout wrongly shipped destroys it and says nothing.
# The first costs a feature that was opt-in anyway. The second is what this
# comment is about.
_MIN_LARGEST_REGION = float(os.getenv("REMBG_MIN_LARGEST_REGION", "0.6") or 0.6)
_MIN_BBOX_FILL = float(os.getenv("REMBG_MIN_BBOX_FILL", "0.3") or 0.3)
# The same question asked of a matte that IS one object, where it is not about
# scatter — one object cannot be scattered — but about whether the object has
# any substance at all. See _kept_is_the_product for why the two cannot share
# a number: at 0.3 a necklace (0.20), a belt (0.12) and a guitar (0.20) are
# all refused, and what deserves refusing is the wisp the model traces across
# a close-up of fabric, which fills 0.01 of its box. The floor sits between
# them, nearer the wisp.
_MIN_BBOX_FILL_ONE_OBJECT = float(
    os.getenv("REMBG_MIN_BBOX_FILL_ONE", "0.05") or 0.05)

# --- ...but one PRODUCT is not always one object ----------------------------
#
# The rule above says "one product is one object, and a matte of it is one
# solid blob". That is false for a great deal of what resells, and the way it
# is false is silent: a seller photographs a PAIR of shoes, two paintings,
# earrings, a two-piece, a cup and its saucer, an item beside its box — and
# the largest region is half of what was kept, so the cutout is refused and
# the photo comes back untouched.
#
# Reported as "background removal is not working": two canvases lying on
# grass. Run through the real isnet model, that photo mattes PERFECTLY — the
# model keeps both canvases as two clean rectangles and is not fooled by the
# figures painted on them — and scores coverage 0.47, solidity 1.00, box fill
# 0.87. It was thrown away on largest-region 0.51 against a floor of 0.60,
# for no reason except that the seller was selling two things.
#
# So it is only the LARGEST-REGION half that gets a second question. The box
# fill stays a precondition for everything, and it is the clause that keeps
# #255 intact, exactly as its own comment says: a tree at one edge and a boat
# at the other span nearly the whole photo while covering little of it, where
# two canvases side by side fill the box they share. Measured on this file's
# own fixtures — the traps 0.10, 0.17 and 0.22, a pair of shoes 0.60, the
# reported canvases 0.87. Nothing about that changes here.
#
# What changes is that failing the one-blob test is no longer the end of it.
# When the box is filled, ask instead: is this A FEW COMPACT OBJECTS?
#
#   * the substantial regions must be nearly ALL of what was kept, so a long
#     tail of confetti disqualifies;
#   * there must be few of them, because a seller sells a pair or a set, not
#     a dozen scattered fragments;
#   * and each must fill its OWN box too, so a pair of ragged pieces cannot
#     ride in on a tidy arrangement.
_COMPANION_SHARE = float(os.getenv("REMBG_COMPANION_SHARE", "0.25") or 0.25)
_MAX_OBJECTS = int(os.getenv("REMBG_MAX_OBJECTS", "4") or 4)
_OBJECTS_COVER = float(os.getenv("REMBG_OBJECTS_COVER", "0.85") or 0.85)
# The mask is judged at this size. Shape is a low-frequency question, and the
# region labelling below is pure Python — at 160px it is a few thousand cells
# and about a millisecond, against millions of pixels and a visible stall.
_SHAPE_SIDE = int(os.getenv("REMBG_SHAPE_SIDE", "160") or 160)

# --- and whether it is SOLID rather than see-through -------------------------
#
# Both measures above read a BINARISED matte — "the pixels at least half
# opaque" — and neither can see the one thing the seller actually gets, which
# is the matte used as an alpha channel. A pixel the model was half sure about
# is not kept or dropped; it is composited at half strength onto white, and a
# whole item's worth of those is an item half rubbed out.
#
# The report, with a screenshot: a grid of clothing. The maroon shirt came
# out right. The white oxford and the cream fleece came back as pale smears
# dissolving into the background — only the collar label, the placket and a
# printed logo still solid, the fabric around them a ghost.
#
# That correlation is the whole diagnosis. The model is confident where there
# is contrast and unsure where there is not, so a PALE ITEM ON A PALE
# BACKDROP — a white shirt on white foamboard, exactly what sellers are told
# to shoot on — comes back with its high-contrast details at 255 and its
# fabric somewhere in the middle of the range. The shape guards then see the
# details, which really are one solid blob filling its own box, and pass it.
# What ships is the item at a third of its opacity over white, i.e. gone.
#
# So the third question, asked of the alpha as it will be USED: is the item
# opaque where it is not an edge? Softness at the boundary is a good matte
# doing its job — fur, flyaway hair, the anti-aliased rim of anything — so
# the measure looks at the INTERIOR, the visible region eroded away from the
# background, and asks what share of it is kept solid. Fur and lace answer
# well above 0.6 there because their bodies are opaque and only their
# fringes are not. A ghost answers below 0.15, because its middle is the
# part that is see-through.
#
# A HOLE is not a hedge, and the difference is the whole measure. A pixel the
# matte set to zero — the weave of a basket, the gap under a shoe's laces, the
# space inside a mug's handle — is not part of the interior at all and is not
# counted. A pixel it set to 110 is: the model was unsure, that pixel ships at
# half strength over white, and enough of them is an item rubbed out. Openwork
# items are ordinary here (wicker, mesh, crochet, cane, wire) and they are not
# ghosts.
#
# WHAT THIS MEASURES NOW. _fill_interior runs first and makes the interior
# opaque, so on a hedged matte this reads 1.00 where it used to read 0.10 —
# the ghost is repaired rather than caught. That is the point: refusing was
# only ever the best answer available while the alternative was shipping the
# item at a third of its opacity, and it left the middle case (hedged enough
# to look wrong, not enough to be refused) shipping damaged with nothing
# said. This stays as the backstop for a matte the repair could not save —
# one with no interior to promote — and as the thing that fails loudly if
# _fill_interior is ever broken or removed.
#
# The one case that changed hands rather than being fixed is genuinely sheer
# fabric — tulle, organza, a chiffon scarf — which mattes half-opaque all
# over and is indistinguishable from a model that never committed. It used to
# be kept as shot; it now ships opaque, which is wrong for it. Restore
# original recovers the photo. The trade is deliberate: pale garments on
# plain backdrops are what the app tells sellers to shoot, and sheer items
# are rare.
#
# One caution for anyone changing this: unlike _shape_stats, which thresholds
# its downscaled copy LOW on purpose so a merged cell errs toward connected,
# this one asks a HIGH question — and a high threshold applied after averaging
# errs toward REFUSING, which is the direction that silently costs the seller
# the feature. So the opacity question is asked per pixel, before anything is
# merged. See _interior_solidity.
_MIN_INTERIOR_SOLIDITY = float(
    os.getenv("REMBG_MIN_SOLIDITY", "0.4") or 0.4)
# What counts as opaque, asked of a PIXEL. _harden already snaps everything
# from _ALPHA_HIGH up to a flat 255, so a pixel is either 255 or one the model
# hedged on; the slack is for the rounding a resize leaves behind.
_SOLID_ALPHA = 250
# How far in from the background the interior starts, in cells of the
# _SHAPE_SIDE copy. Two is about 1% of the frame — enough to clear the soft
# rim of a well-cut matte, small enough to leave an interior on a bracelet.
_INTERIOR_ERODE = 2

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


# The contact shadow. An item composited on pure white with a hard edge reads
# as a sticker cut out and pasted down; the same item over a soft shadow reads
# as an object sitting on a surface, which is what a catalogue photo looks
# like. It was dropped with the rest of the old pipeline and asked for back --
# on its own, because unlike the passes around it, it is neither slow nor a
# surprise: it is drawn from the silhouette the model already produced, so it
# costs no inference and cannot change what the item looks like.
#
# It is cheap by construction. The shadow is low-frequency -- a blur of ~1.5%
# of the frame -- so it is rendered on a copy no bigger than _SHADOW_SIDE and
# scaled back up: visually identical to blurring the full-size mask, at about
# a tenth of the cost. On a 4032x3024 photo that is a ~800px blur instead of a
# 4032px one.
_SHADOW_ALPHA = 0.40          # how dark, against white
_SHADOW_INK = (55, 55, 55)    # near-black, never pure: pure reads as a hole
_SHADOW_BLUR = 0.015          # softness, as a fraction of the short side
_SHADOW_OFFSET = 0.010        # down and right, ditto -- the light is above left
_SHADOW_SIDE = 800            # render the blur no larger than this


def _contact_shadow(alpha: Image.Image) -> Image.Image:
    """The subject's silhouette as a soft shadow mask, offset down and right."""
    w, h = alpha.size
    blur = max(4, round(min(w, h) * _SHADOW_BLUR))
    off = max(2, round(min(w, h) * _SHADOW_OFFSET))
    scale = min(1.0, _SHADOW_SIDE / max(w, h))
    if scale < 1.0:
        small = (max(1, round(w * scale)), max(1, round(h * scale)))
        soft = alpha.resize(small, Image.BILINEAR).filter(
            ImageFilter.GaussianBlur(max(1.0, blur * scale))).point(
            lambda a: int(a * _SHADOW_ALPHA)).resize((w, h), Image.BILINEAR)
    else:
        soft = alpha.filter(ImageFilter.GaussianBlur(blur)).point(
            lambda a: int(a * _SHADOW_ALPHA))
    shifted = Image.new("L", (w, h), 0)
    shifted.paste(soft, (off, off))
    return shifted


def _kept_is_the_product(total: int, regions: list, box_fill: float) -> bool:
    """Whether what the matte kept looks like the thing being sold.

    One solid blob is the common case and answers yes immediately. Otherwise
    the pair-or-set question above — a few compact objects, together
    accounting for nearly everything kept, and each filling its own box.
    """
    if not total or not regions:
        return False
    one_object = regions[0][0] / total >= _MIN_LARGEST_REGION
    # Box fill is the SCATTER question, and it is asked where scatter is
    # possible — which is not everywhere.
    #
    # It was asked of every matte, as a precondition on the lot, and what that
    # refuses is not only scatter: it refuses any product that is THIN. A
    # necklace laid out in a curve fills 0.20 of the box around it, a belt
    # laid diagonally 0.12, a guitar 0.20, a bangle 0.29 against a floor of
    # 0.30 — every one of them a perfect matte of one connected object,
    # correctly found, and every one of them thrown away with "the matte kept
    # is not the product". Chains, straps, cables, tools, instruments, hoops
    # and anything photographed at an angle are all that shape, and they are
    # a large part of what resells. The seller is told no item was found.
    #
    # Which is not what the measure was written for. Read its own case back:
    # a tree at one edge and a boat at the other span nearly the whole photo
    # while covering little of it. That is a statement about PIECES lying far
    # apart, and every fixture in test_the_cutout_does_not_eat_the_artwork
    # that it exists to refuse — the brushstrokes, the tree and the boat, the
    # tree and boat and sketch, two fragments in opposite corners — is two or
    # more pieces. A matte that is one object cannot be spread across the
    # frame; it can only be long, and long is a shape products come in.
    #
    # So the precondition holds exactly where it always did the work: on a
    # matte that is NOT one object, where it is what keeps the pair-or-set
    # rule below from letting a painting's pieces through.
    #
    # One object still has to be an OBJECT, though, and the floor for that is
    # a different number rather than no number. Handed a close-up of fabric
    # the model traces a wisp across the frame — one region holding 99% of
    # what was kept, filling 0.01 of the box around it, and about half a
    # percent of the photo. That is the "white square" case this file refuses
    # on principle, and it is one object by every measure here.
    if box_fill < (_MIN_BBOX_FILL_ONE_OBJECT if one_object else _MIN_BBOX_FILL):
        return False
    if one_object:
        return True
    objects = [r for r in regions if r[0] >= regions[0][0] * _COMPANION_SHARE]
    return (len(objects) <= _MAX_OBJECTS
            and sum(r[0] for r in objects) / total >= _OBJECTS_COVER
            and all(fill >= _MIN_BBOX_FILL for _cells, fill in objects))


def _kept_shape(kept: Image.Image) -> tuple[int, list[tuple[int, float]], float]:
    """What a binary mask is made of: (total cells kept, one entry per
    connected region as (cells, how well it fills ITS OWN bounding box),
    largest region first, how well the whole lot fills the ONE box around it).

    Every shape question in this file is answered from here, so the labelling
    below — the only pure-Python loop on the photo path — runs once per photo.

    Per-region box fill is what tells a pair of products from the fragments of
    one. Two shoes, two canvases, a cup and its saucer are each compact and
    each fill their own box; a tree and a boat the model found inside a
    painting do not fill theirs, however tidily they sit in the frame
    together. Reported rather than reduced to one number, because which
    question matters depends on how many regions there turn out to be.
    """
    w, h = kept.size
    scale = _SHAPE_SIDE / max(w, h)
    if scale < 1:
        # BOX averages the pixels it merges rather than sampling one of them,
        # and the low threshold then keeps a cell any part of the item touched.
        # Both err toward CONNECTED, which errs toward letting the cutout
        # through — the direction that cannot destroy a photo.
        kept = kept.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                           Image.BOX).point(lambda v: 255 if v >= 32 else 0)
    w, h = kept.size
    px = kept.load()
    total = 0
    for y in range(h):
        for x in range(w):
            if px[x, y]:
                total += 1
    if not total:
        return 0, [], 0.0

    # Flood fill each unvisited region, 4-connected, with an explicit stack:
    # a recursive fill blows Python's stack on a mask that is mostly one blob,
    # which is precisely the healthy case.
    seen = bytearray(w * h)
    regions: list[tuple[int, float]] = []
    for sy in range(h):
        for sx in range(w):
            if seen[sy * w + sx] or not px[sx, sy]:
                continue
            size = 0
            lo_x = hi_x = sx
            lo_y = hi_y = sy
            stack = [(sx, sy)]
            seen[sy * w + sx] = 1
            while stack:
                x, y = stack.pop()
                size += 1
                lo_x, hi_x = min(lo_x, x), max(hi_x, x)
                lo_y, hi_y = min(lo_y, y), max(hi_y, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] \
                            and px[nx, ny]:
                        seen[ny * w + nx] = 1
                        stack.append((nx, ny))
            own_box = (hi_x - lo_x + 1) * (hi_y - lo_y + 1)
            regions.append((size, size / own_box if own_box else 0.0))
    regions.sort(reverse=True)

    box = kept.getbbox()
    box_area = (box[2] - box[0]) * (box[3] - box[1]) if box else 0
    return total, regions, (total / box_area if box_area else 0.0)


def _shape_stats(kept: Image.Image) -> tuple[float, float]:
    """(the largest connected region's share of the kept area, the kept area's
    share of its own bounding box) — the two summary numbers, for callers and
    tests that want the shape as a pair rather than as its parts.

    (0.0, 0.0) for an empty mask, so a caller that reaches here without
    checking coverage still refuses rather than dividing by zero.
    """
    total, regions, box_fill = _kept_shape(kept)
    if not total:
        return 0.0, 0.0
    return regions[0][0] / total, box_fill


def _fill_interior(alpha: Image.Image) -> Image.Image:
    """The matte with the item's INTERIOR made opaque.

    This is the repair for the failure _interior_solidity was written to
    detect. The model is confident where there is contrast and unsure where
    there is not, so a pale item on a pale backdrop — a white shirt on white
    foamboard, which is exactly what sellers are told to shoot on — comes back
    with its collar label and its placket at 255 and the fabric between them
    somewhere in the middle. _harden ships that middle as PARTIAL ALPHA, and a
    pixel the model half believed in is composited at half strength over
    white. An item's worth of those is the item rubbed out: the seller gets a
    crisp logo floating on a ghost of a shirt.

    Detecting that and refusing the cutout (which is what the solidity floor
    does) leaves two outcomes and no good one — badly hedged means the photo
    is kept as shot, moderately hedged means it ships damaged. So: repair it.
    A pixel inside the item is part of the item whatever the model's
    confidence, because there is nothing else it could be.

    "Inside" is the same interior _interior_solidity measures, and reusing
    that definition is what keeps a soft edge soft: cells WHOLLY covered by
    the item, eroded back from the boundary. A rim cell is only partly
    covered, so it is not interior and is left exactly as the model drew it.
    The same is true of the gaps between a fur collar's tufts or a wig's
    flyaway strands — though a fringe dense enough to cover its cells
    completely does count as interior and will harden, which is the one place
    this trades a little softness for a great deal of fabric.

    Only where the model saw the item at all — above _ALPHA_LOW, the same
    line _harden draws between "background" and "an edge". Below it the model
    was confident there is nothing, so the hole through a ring, the gap under
    a mug's handle and the backdrop between a pair of boots all stay holes.
    Gating on any non-zero alpha instead would promote the faintest haze the
    model left on the backdrop, which is the very thing _harden exists to
    delete.

    All C — two point operations, a resize, an erosion and two composites.
    About ten milliseconds on a 12MP photo, and no extra inference.
    """
    w, h = alpha.size
    # The hole gate, read at FULL resolution: a hole is a few pixels wide at
    # the scale the seller sees, and asking a downscaled copy would round the
    # gap under a ring's band away and paint it in.
    seen = alpha.point(lambda a: 255 if a > _ALPHA_LOW else 0)
    # The interior, read at _SHAPE_SIDE, because "how far in from the edge is
    # this" is a low-frequency question — the same copy and the same erosion
    # _interior_solidity uses to answer it.
    scale = _SHAPE_SIDE / max(w, h)
    inner = (seen.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                         Image.BOX) if scale < 1 else seen)
    inner = inner.point(lambda v: 255 if v >= 255 else 0)
    for _ in range(_INTERIOR_ERODE):
        inner = inner.filter(ImageFilter.MinFilter(3))
    # Back to full size as a SOFT mask, blurred by about one cell of the copy
    # it was found on, and deliberately never re-thresholded.
    #
    # This is the difference between measuring a matte and painting one. Where
    # the promoted interior hands back to the ramp left alone, a hard mask
    # makes that hand-off a single step — and because the step follows the
    # 160px working grid, it shows up in the finished photo as a stepped
    # contour running down the inside of the sleeve. Blurred, it is a
    # gradient. (Finding the interior on a finer grid would smooth it too,
    # but a finer grid also starts reading the gaps between a fur collar's
    # tufts as interior and hardens the fringe. The blur costs nothing and
    # keeps the fringe.)
    if inner.size != alpha.size:
        inner = inner.resize(alpha.size, Image.BILINEAR).filter(
            ImageFilter.GaussianBlur(max(w, h) / _SHAPE_SIDE))
    return ImageChops.lighter(alpha, ImageChops.multiply(inner, seen))


# How far an enclosed region's colour has to sit from the backdrop's before it
# is read as ITEM the model dropped rather than backdrop showing through, as a
# plain RGB distance (0-441). Dark navy against a pale studio sweep is 150+;
# the noise across one seamless backdrop is a handful.
_HOLE_COLOUR_DIST = float(os.getenv("REMBG_HOLE_COLOUR_DIST", "40") or 40)

# --- ...and when the colour cannot tell them apart --------------------------
#
# The colour question above compares two MEAN colours, and there is one case
# where that is not enough — the case this whole file's shape guards were
# written for, arriving by a different door.
#
# A framed watercolour on a neutral backdrop. The model keeps the frame and
# deletes everything inside it; the interior is one enclosed region, 61% as
# large as the entire matte. Averaged, a white mount plus a pale sky plus a
# green tree comes to (203, 219, 225), which is 27 from the backdrop's
# (211, 208, 201) — inside _HOLE_COLOUR_DIST, so it is read as the backdrop
# showing through and thrown away. What ships is an EMPTY PICTURE FRAME on
# white, and it ships silently: coverage 0.19, solidity 0.95, one region
# holding 99% of what was kept, box fill 0.54. Every gate in this file passes
# a ring, because a ring is a perfectly respectable shape.
#
# Size alone cannot rescue it — measured on this file's own fixtures, the
# artwork's hole is 0.61 of the matte while a wreath's is 0.76 and an empty
# frame's 1.14, and those two are real holes that must stay holes.
#
# What separates them is that a backdrop seen through a gap IS the backdrop:
# the same seamless sweep, the same paper, the same table, and therefore the
# same flatness. Artwork is not flat, and neither is anything else that gets
# photographed inside a border — a print, a poster, a book cover, a trading
# card, a label on a box. So the second question is asked of SPREAD rather
# than of colour: how varied is this region, against how varied the backdrop
# is in this same photo. As a ratio, so that a photo shot on grass or a rug
# answers it on its own terms rather than against a number picked here.
#
# The same fixtures: mug handle 0.50, wreath 0.39, empty frame 0.06 — every
# genuine hole at or below half the backdrop's own spread — against the
# watercolour's 3.37. The floor sits at 1.5, which is well clear of both.
_HOLE_FLAT_RATIO = float(os.getenv("REMBG_HOLE_FLAT_RATIO", "1.5") or 1.5)
# ...and a ratio needs a floor under the thing it divides by, or it says
# nothing at all. A backdrop drawn as one flat colour has a spread of zero, so
# ANY region beats any multiple of it — the anti-aliased rim of a ring's hole
# scored 2.8x and the ring filled in. A photograph's backdrop is never that
# flat, but a guard that only holds on photographs is not a guard, so the
# region must also carry enough detail to be a picture in its own right.
# Measured: every genuine hole 4.4 or below (mug handle 4.4, wreath 3.7, a
# ring's hole 2.8, an empty frame 0.6), the artwork 31.5.
_HOLE_DETAIL_FLOOR = float(os.getenv("REMBG_HOLE_DETAIL", "10") or 10)
# ...asked only of a hole big enough to BE the item's face. A mug's handle is
# 0.04 of its matte and a basket's weave smaller still; asking this of them
# would trade a well-understood rule for a statistic taken over a handful of
# cells. Everything below this share keeps the colour answer it has always
# had, so openwork stays openwork.
_HOLE_BIG_SHARE = float(os.getenv("REMBG_HOLE_BIG_SHARE", "0.12") or 0.12)


def _mean_rgb(rgb: Image.Image, mask: Image.Image) -> Optional[tuple]:
    """Mean colour of `rgb` over the non-zero pixels of `mask`, or None when
    the mask is empty."""
    if not mask.getbbox():
        return None
    stat = ImageStat.Stat(rgb, mask)
    return tuple(stat.mean[:3])


def _apart(a: Optional[tuple], b: Optional[tuple]) -> float:
    """Plain RGB distance between two mean colours; 0 when either is missing,
    which reads as "no evidence" everywhere this is used."""
    if a is None or b is None:
        return 0.0
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _spread(rgb: Image.Image, mask: Image.Image) -> Optional[float]:
    """How VARIED `rgb` is over the non-zero pixels of `mask` — the mean of
    the per-channel standard deviations — or None when the mask is empty.

    A seamless backdrop answers a few units whatever colour it is; anything
    with a picture on it answers many. See _HOLE_FLAT_RATIO.
    """
    if not mask.getbbox():
        return None
    return sum(ImageStat.Stat(rgb, mask).stddev[:3]) / 3


def _reclaim_enclosed(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    """The matte with ENCLOSED regions of nothing given back to the item, when
    the photo says they were never background.

    The failure this repairs, reported with a screenshot: a Scotch & Soda camp
    shirt, black across the shoulders with a bright print below. The print
    survived every cutout. The black did not — several photos came back with
    the shoulder, a sleeve, or a chunk of the back simply gone, a white hole
    punched through the middle of the garment.

    Neither guard could see it, and not by accident. `_fill_interior` and
    `_interior_solidity` both begin at "what the model SAW" — alpha above
    _ALPHA_LOW — and a region the model set to zero is not seen, so it is not
    interior, so it is background as far as either is concerned. That is the
    right reading for the hole through a ring and it is exactly wrong here.
    Solidity stays high the whole time (the fabric that survived is perfectly
    opaque), coverage stays high, the shape is one tidy object: every gate
    passes and the photo ships with a hole in it.

    What separates the two cases is not the matte, which is identical, but the
    PHOTO underneath — so this is the one repair that has to look at it. A
    hole through a ring shows the backdrop; a hole in a shirt shows the shirt.
    So each enclosed region is asked which it resembles, and only a region
    that is both far from the backdrop AND nearer the item than the backdrop
    is given back. Both halves matter: the distance alone would fill a gap
    that happens to fall in shadow, and the comparison alone would fill a
    genuine hole on a backdrop that merely differs from the item.

    "Enclosed" means not reachable from the frame edge through background,
    which is what keeps the gap between a pair of boots — real backdrop, open
    to the edge — out of this entirely, however dark it is.

    Runs on the _SHAPE_SIDE grid like every other shape question here, with
    the same explicit-stack labelling as _kept_shape, and returns the matte
    untouched (no allocation, no colour statistics) when nothing is enclosed,
    which is almost every photo.
    """
    w, h = alpha.size
    scale = _SHAPE_SIDE / max(w, h)
    size = ((max(1, round(w * scale)), max(1, round(h * scale)))
            if scale < 1 else (w, h))
    # Err toward SEEN, the way _kept_shape errs toward connected: a cell the
    # item merely touches counts as item, so a soft rim never reads as a hole
    # and the regions below are the frankly-empty ones.
    small = (alpha.resize(size, Image.BOX) if size != (w, h) else alpha)
    seen = small.point(lambda v: 255 if v > _ALPHA_LOW else 0)
    sw, sh = size
    px = seen.load()

    # Label the EMPTY cells, 4-connected, recording for each region whether it
    # ever touched the frame edge. Anything that did is backdrop with a way
    # out; the rest is enclosed by the item.
    labels = [0] * (sw * sh)
    regions: list[list] = []          # [cells, touches_edge]
    for sy in range(sh):
        for sx in range(sw):
            if px[sx, sy] or labels[sy * sw + sx]:
                continue
            tag = len(regions) + 1
            cells, edge = 0, False
            stack = [(sx, sy)]
            labels[sy * sw + sx] = tag
            while stack:
                x, y = stack.pop()
                cells += 1
                if x == 0 or y == 0 or x == sw - 1 or y == sh - 1:
                    edge = True
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < sw and 0 <= ny < sh \
                            and not labels[ny * sw + nx] and not px[nx, ny]:
                        labels[ny * sw + nx] = tag
                        stack.append((nx, ny))
            regions.append([cells, edge])
    enclosed = [i + 1 for i, (_, edge) in enumerate(regions) if not edge]
    if not enclosed:
        return alpha

    # What the backdrop and the item actually look like, measured on a copy of
    # the photo at the same grid so every mask below lines up with it.
    photo = rgb.convert("RGB")
    photo = photo.resize(size, Image.BOX) if size != (w, h) else photo
    outside = Image.frombytes(
        "L", size, bytes(255 if labels[i] and regions[labels[i] - 1][1] else 0
                         for i in range(sw * sh)))
    backdrop = _mean_rgb(photo, outside)
    item = _mean_rgb(photo, seen)
    if backdrop is None or item is None:
        # No backdrop to compare against (the item fills the frame) or no item
        # at all. Either way there is no evidence, and inventing some here
        # would paint over a photo on a guess.
        return alpha

    # How varied the backdrop is in THIS photo, and how much of the frame the
    # matte kept — the two things the big-hole question below is asked
    # against. Both are measured once, on the same grid as everything else.
    backdrop_spread = _spread(photo, outside)
    kept_cells = sum(1 for i in range(sw * sh) if not labels[i])

    give_back = bytearray(sw * sh)
    face = False
    for tag in enclosed:
        patch = Image.frombytes(
            "L", size, bytes(255 if labels[i] == tag else 0
                             for i in range(sw * sh)))
        here = _mean_rgb(photo, patch)
        # A hole big enough to be the item's own face, carrying more detail
        # than the backdrop does anywhere in this photo, is not the backdrop
        # showing through — it is a picture, and the thing it is a picture of
        # is what the seller is selling. Asked BEFORE the colour tests
        # because it is the case they cannot answer: a pale artwork inside a
        # frame averages out to something that reads as a pale backdrop, and
        # no comparison of means will ever separate those two. See
        # _HOLE_FLAT_RATIO.
        cells = regions[tag - 1][0]
        here_spread = _spread(photo, patch)
        if (kept_cells and cells >= kept_cells * _HOLE_BIG_SHARE
                and backdrop_spread is not None and here_spread is not None
                and here_spread >= _HOLE_DETAIL_FLOOR
                and here_spread >= backdrop_spread * _HOLE_FLAT_RATIO):
            log.info("bg-removal: an enclosed region worth %.0f%% of the matte "
                     "carries %.1fx the backdrop's detail — keeping it as the "
                     "item's own face, not a hole",
                     100 * cells / kept_cells,
                     here_spread / max(backdrop_spread, 0.01))
            face = True
            for i in range(sw * sh):
                if labels[i] == tag:
                    give_back[i] = 255
            continue
        if _apart(here, backdrop) <= _HOLE_COLOUR_DIST:
            continue                       # looks like the backdrop: a real hole
        if _apart(here, item) >= _apart(here, backdrop):
            continue                       # nearer the backdrop than the item
        for i in range(sw * sh):
            if labels[i] == tag:
                give_back[i] = 255
    if face:
        # A picture has no edge in the middle of it.
        #
        # Giving the region back is not the whole repair, because the band
        # around it is not empty — it is hedged. On the watercolour the model
        # answers about 70 across the inside of the mount: over _ALPHA_LOW, so
        # those cells are "seen" and belong to no enclosed region at all, and
        # too ragged for _fill_interior's wholly-covered test to promote. What
        # _harden then makes of a 70 is a 53, and an eighth of the artwork
        # ships at a fifth of its opacity — the same white smear the region
        # itself would have been, in a thinner band.
        #
        # Once the photo has said this is a picture inside a border, there is
        # nothing inside that border for a soft alpha to mean. So the whole
        # silhouette is made solid: every cell that is not background with a
        # way out to the frame edge, which is the item's outline FILLED.
        #
        # Eroded by the same _INTERIOR_ERODE as everywhere else, so this
        # reaches the inside and never the outer rim. The rim is where soft
        # alpha is the matte doing its job, and hardening it would trade a
        # white smear for a jagged edge.
        outline = Image.frombytes(
            "L", size, bytes(0 if labels[i] and regions[labels[i] - 1][1] else 255
                             for i in range(sw * sh)))
        for _ in range(_INTERIOR_ERODE):
            outline = outline.filter(ImageFilter.MinFilter(3))
        inside = outline.load()
        for y in range(sh):
            for x in range(sw):
                if inside[x, y]:
                    give_back[y * sw + x] = 255

    if not any(give_back):
        return alpha

    # Back to full size the way _fill_interior hands its interior back: as a
    # soft mask, blurred by about one cell, never re-thresholded, so the join
    # is a gradient rather than a stepped contour on the 160px grid.
    mask = Image.frombytes("L", size, bytes(give_back))
    if size != (w, h):
        mask = mask.resize((w, h), Image.BILINEAR).filter(
            ImageFilter.GaussianBlur(max(w, h) / _SHAPE_SIDE))
    log.info("bg-removal: gave back %d enclosed region(s) the photo says are "
             "the item, not the backdrop", sum(1 for _ in enclosed))
    return ImageChops.lighter(alpha, mask)


def _interior_solidity(alpha: Image.Image) -> float:
    """The share of the item's interior that the matte keeps fully opaque.

    "Interior" is the visible region eroded away from the background by
    _INTERIOR_ERODE, which is what separates the two kinds of soft matte: a
    fur collar or an anti-aliased rim is soft only at the boundary and scores
    near 1.0 once the boundary is taken off, while a matte the model hedged
    across the whole item is see-through in the middle and scores near 0.

    The share is of PIXELS, and it has to be, which is the one subtle thing
    here. Shape is a low-frequency question so the interior is found on a
    _SHAPE_SIDE copy, but "is this opaque" is asked of each full-resolution
    pixel BEFORE anything is merged. Judged the other way round — threshold
    the averaged copy — a cell of a 3000px photo is a mean of some 350 pixels
    and comes out "solid" only if very nearly all of them are, so any real
    internal detail (a seam, a fold, hardware, an openwork weave, a busy
    print) drags it under. That is not a measure of opacity, it is a measure
    of uniformity, and it got stricter the bigger the seller's camera was:
    the same matte scored 0.33 at 640px, 0.06 at 1600 and 0.00 at 3000, so a
    newer phone had its cutouts refused where an older one passed. Binarising
    first makes the answer the same at every size.

    All C — two point operations, two resizes and an erosion.

    1.0 when erosion leaves nothing, which is a chain or a filigree earring
    rather than a ghost: there is no interior, so this measure has nothing to
    say and must not be what refuses the photo.
    """
    w, h = alpha.size
    solid = alpha.point(lambda a: 255 if a >= _SOLID_ALPHA else 0)
    visible = alpha.point(lambda a: 255 if a else 0)
    scale = _SHAPE_SIDE / max(w, h)
    if scale < 1:
        # BOX averages rather than samples, so each cell now carries the SHARE
        # of its pixels that were solid (or visible) rather than a verdict on
        # their mean alpha.
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        solid = solid.resize(size, Image.BOX)
        visible = visible.resize(size, Image.BOX)
    # Interior: cells that were wholly inside the item, then eroded back from
    # the boundary. A cell the rim only clips is not fully visible and is not
    # interior — rims are exactly what this must not measure.
    inner = visible.point(lambda v: 255 if v >= 255 else 0)
    for _ in range(_INTERIOR_ERODE):
        inner = inner.filter(ImageFilter.MinFilter(3))
    if not inner.histogram()[255]:
        return 1.0
    # The mean solid-share over the interior cells: every cell covers the same
    # number of pixels, so this is the share of interior PIXELS kept opaque.
    return ImageStat.Stat(solid, inner).mean[0] / 255


def cutout(rgb: Image.Image, wait: Optional[float] = None) -> Optional[Image.Image]:
    """The item on white under a soft contact shadow, or None when the model
    found no item to keep.

    Raises CutoutBusy when the inference slot is taken for longer than
    `wait`; any other failure raises as itself so a caller can say why."""
    # Repaired BEFORE it is hardened, and hardened exactly once. _harden maps
    # the band between LOW and HIGH onto a ramp, so running it over its own
    # output re-ramps every mid value toward zero and quietly eats the matte.
    # Three passes, in this order and each exactly once.
    #
    # _reclaim_enclosed first, because it is the only one that can see the
    # failure it repairs: it reads the PHOTO to tell a hole through a ring
    # from a hole punched in a shirt, and both of the others start from "what
    # the model saw" and so cannot tell those apart at all.
    #
    # Then _fill_interior, then _harden — and _harden last and once, since it
    # maps the band between LOW and HIGH onto a ramp and running it over its
    # own output re-ramps every mid value toward zero and quietly eats the
    # matte.
    alpha = _harden(_fill_interior(_reclaim_enclosed(rgb, _mask(rgb, wait=wait))))
    kept = alpha.point(lambda a: 255 if a >= 128 else 0)
    coverage = (sum(kept.histogram()[128:]) / (rgb.width * rgb.height))
    if coverage < _MIN_FG_COVERAGE:
        log.info("bg-removal: no item found (coverage %.3f)", coverage)
        return None
    # Enough survived. Is it SOLID, or an item rubbed out to a third of
    # itself? See _MIN_INTERIOR_SOLIDITY. Asked before the shape question
    # because it is the cheaper of the two, and because the ghost it catches
    # is a matte whose shape is perfectly respectable.
    solidity = _interior_solidity(alpha)
    if solidity < _MIN_INTERIOR_SOLIDITY:
        log.info("bg-removal: the matte is see-through — only %.2f of the "
                 "item's interior is opaque (coverage %.3f); keeping the "
                 "photo as shot", solidity, coverage)
        return None
    # And is it the product? One object, or a few — see _COMPANION_SHARE.
    total, regions, box_fill = _kept_shape(kept)
    if not _kept_is_the_product(total, regions, box_fill):
        log.info("bg-removal: what the matte kept is not the product — %d "
                 "region(s), largest %.2f of what it kept and filling %.2f of "
                 "its own box, the lot filling %.2f of theirs (coverage "
                 "%.3f); keeping the photo as shot",
                 len(regions), (regions[0][0] / total) if total else 0.0,
                 regions[0][1] if regions else 0.0, box_fill, coverage)
        return None
    canvas = Image.new("RGB", rgb.size, WHITE)
    canvas.paste(Image.new("RGB", rgb.size, _SHADOW_INK), (0, 0),
                 _contact_shadow(alpha))
    canvas.paste(rgb, (0, 0), alpha)
    return canvas


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


# What a photo gets instead of a cutout when it is a close-up of part of an
# item. Not an error and not a refusal — nothing was attempted, because there
# was nothing for the cutout to do. It rides in `bg_error` so the seller is
# told and the charge comes back, which is what that field is for.
DETAIL_KEPT_AS_SHOT = (
    "This is a close-up of a detail, not a photo of the whole item — there is "
    "no background to take off, so it was kept as shot.")


# --- the photo the cutout replaced -------------------------------------------
#
# A cutout is a PRESENTATION change: it is what the buyer should see on the
# listing. It is not what the app should THINK with, and for a long time it
# was both, because every vision pass reads the optimized photo.
#
# That is the second half of the Hilo Hattie report. The cutout tore up two
# tag close-ups; identify then read those same torn files, could not find a
# brand on them, and invented one. The close-ups are spared now (see
# orient._details), which removes that cause — but not the shape of the
# problem. Any cutout that goes wrong, for any reason this file has not
# thought of yet, still silently becomes the app's only record of the item,
# and the seller reads the result as "the AI is bad at identifying things".
# Nothing in the draft says a photo was damaged, because nothing downstream
# knows a photo COULD have been.
#
# So the pass keeps the photo it replaced. Written from the image already in
# memory one step before the composite, so it costs one JPEG encode and no
# decode, and it is the right frame by construction: upright, EXIF honoured,
# sized for eBay, metadata stripped — the same photo the seller would get
# from Restore original, without re-reading a 12MP HEIC to produce it.
#
# WHY NOT JUST READ original/. It is tempting, and it is what this looked
# like at first. Three things are wrong with it. The originals are pruned on
# a timer (storage.prune_originals), so re-identifying an older listing would
# quietly read a different photo than a new one. They are pre-rotation — the
# ITEM's turn is decided by services/orient and applied here, so a photo the
# pass straightened would go back to the model lying on its side. And they
# are whatever the camera wrote: a 12MP HEIC to decode on every call, on the
# same 4GB box that is holding the cutout model.
#
# WHY THE MTIME RULE IS THE WHOLE INVALIDATION STORY. This file is preferred
# only while it is at least as new as the working copy next to it. Every way
# a photo changes after this pass rewrites that working copy — the studio's
# save, a quick rotate, Restore original, a pull back from R2 — so each of
# them makes this file older, and as_shot() stops offering it without any of
# those routes knowing it exists. The seller's own edit is the seller's
# intent and must win; this only ever speaks for a photo nobody has touched
# since the pass ran.
_AS_SHOT = "as_shot"


def _keep_as_shot(dst: Path, img: Image.Image) -> None:
    """Keep `img` as the faithful copy of the photo written to `dst`.

    Best-effort in the strongest sense: this is an aid to the passes that
    READ photos, and a photo must never fail to be listed because a cache
    beside it could not be written. Only for a real session photo — a direct
    caller optimizing to some other directory gets nothing, exactly as
    vision_copy does."""
    if dst.parent.name != "optimized":
        return
    try:
        out = dst.parent.parent / _AS_SHOT / dst.name
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.{os.getpid():x}.tmp")
        try:
            img.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True)
            os.replace(tmp, out)  # atomic: a racing reader never sees a torn file
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    except Exception as exc:  # noqa: BLE001 - a cache is never worth a photo
        log.info("as-shot copy skipped for %s: %s", dst.name, exc)


def as_shot(path: Path) -> Path:
    """The photo as the camera saw it, for an optimized session photo whose
    background was taken off — or `path` itself for every other photo, which
    already IS what the camera saw.

    This is what every pass that has to READ an item should open: what its
    tag says, what it is made of, what the flaw in the corner is. `path`
    stays right for anything that has to SHOW the photo.

    Safe to call with any path: one that is not a session photo, has no
    faithful copy, or has been edited since comes straight back."""
    if path.parent.name != "optimized":
        return path
    kept = path.parent.parent / _AS_SHOT / path.name
    try:
        if kept.stat().st_mtime >= path.stat().st_mtime:
            return kept
    except OSError:  # no copy kept, or the photo itself is gone
        pass
    return path


def optimize(src: Path, dst: Path, remove_bg: bool = False,
             rotate: int = 0, detail: bool = False) -> dict:
    """One photo: as shot, or cut out on white; sized for eBay; no EXIF.
    Returns what was done. A cutout that finds nothing keeps the photo as shot
    and says so in `bg_error`, so the caller can give the charge back.

    `rotate` is clockwise degrees (0/90/180/270): the ITEM's turn, decided by
    services/orient and applied right after the camera's EXIF — before the
    cutout, so the contact shadow falls below an upright item rather than
    beside a sideways one. Anything but a clean quarter turn is ignored: it
    would resample the photo and report a turn that did not happen. Reported
    as `rotated` when applied. Nothing here asks for a turn: the studio's
    Restore original and every other direct caller ship the photo as shot,
    and only the batch pass (optimize_batch) decides one.

    `detail` says this photo is a close-up of PART of an item — a tag, a
    label, a stitch, a texture — as decided by the same pass and on the same
    terms. The cutout is not run on one. Every pixel in the frame is the
    item, so there is no background to take off, and a salient-object model
    asked anyway answers the only question it knows: it keeps the label and
    deletes the garment behind it. The three guards below cannot catch that —
    the matte of a torn-out label is one opaque region that fills its own box,
    which is what a GOOD cutout looks like — so the photo has to be spared
    before the model is asked, not after. See orient._details."""
    img, shot = _load(src)
    turn = int(rotate or 0) % 360
    turn = turn if turn in CW_TRANSPOSE else 0
    if turn:
        img = img.transpose(CW_TRANSPOSE[turn])
    bg_removed, bg_error = False, None
    faithful = None
    if remove_bg and detail:
        bg_error = DETAIL_KEPT_AS_SHOT
    elif remove_bg:
        try:
            out = cutout(img)
        except Exception as exc:  # noqa: BLE001 - a photo must never fail for its cutout
            log.warning("bg-removal: keeping %s as shot (%s)", src.name, exc)
            out, bg_error = None, f"Background removal failed: {exc}"
        if out is not None:
            # Hold on to what the camera saw before the cutout replaces it.
            # This is the only moment it exists in the right form — upright,
            # EXIF honoured, sized, metadata stripped — and the only moment
            # it is free. See _keep_as_shot.
            faithful, img, bg_removed = img, out, True
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
    # AFTER the working copy, never before: as_shot() prefers this file only
    # while it is at least as new as the photo beside it, which is what makes
    # a later edit in the studio win automatically.
    if faithful is not None:
        _keep_as_shot(dst, faithful)
    out = {"file": dst.name, "original_size": shot, "output_size": img.size,
           "background_removed": bg_removed}
    if turn:
        out["rotated"] = turn
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


def source_for(src_dir: Path, name: str) -> Optional[Path]:
    """The original upload that produced `name` (img_NNN.jpg), or None.

    optimize_all names its output from the photo's position in the naturally
    sorted source list and says so: "the same photo maps to the same output
    name every run". This reads that mapping backwards, which is what makes a
    photo recoverable after the pass has done something to it the seller did
    not want.

    None when the index is out of range or the originals have been pruned —
    both of which the caller has to say out loud rather than paper over, since
    the alternative is telling someone their photo is restored when it is not.
    """
    # Exactly the shape optimize_all writes — img_{i:03d}.jpg, so three
    # digits or more. `name` arrives off the wire and is used to index a
    # directory listing, so it is matched against what this app produces
    # rather than against whatever happens to parse.
    m = re.fullmatch(r"img_(\d{3,})\.jpg", (name or "").strip())
    if not m or not src_dir.is_dir():
        return None
    sources = [p for p in sorted(src_dir.iterdir(), key=lambda p: natural_key(p.name))
               if p.suffix.lower() in _EXTS]
    i = int(m.group(1))
    # By NAME before by position. Every writer of an original stamps it with
    # the index its working copy will carry -- src_NNN from an upload or an
    # import, add_NNN from "Add photos" -- and reading the directory by
    # position gets the "Add photos" case wrong: add_003.jpg sorts before
    # src_000.jpg, so on a listing with three uploads and two added photos,
    # img_003 counted along the list to src_001 and put somebody else's
    # photo where the seller asked for their own. A file that says which
    # index it is for is believed. The positional read stays for originals
    # that carry the camera's own names.
    named = [p for p in sources if re.fullmatch(rf"(?:src|add)_{i:03d}", p.stem)]
    if len(named) == 1:
        return named[0]
    return sources[i] if 0 <= i < len(sources) else None


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
    if jobs and should_stop is not None and should_stop():
        raise Stopped()
    # One batched look at every photo still to do, up front. It answers two
    # things: which photos have their ITEM lying sideways or on its head, so
    # each is turned before its cutout rather than after, and which are
    # close-ups of PART of an item, so the cutout is not run on them at all.
    # Best-effort and bounded: a photo the pass cannot answer for is left as
    # shot and cut out as usual.
    turns, details = _screen_for([src for src, _dst in jobs], should_stop)
    for src, dst in jobs:
        if should_stop is not None and should_stop():
            raise Stopped()
        try:
            result = optimize(src, dst, remove_bg,
                              rotate=turns.get(src.name, 0),
                              detail=src.name in details)
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


def _screen_for(sources: list[Path],
                should_stop=None) -> tuple[dict[str, int], frozenset[str]]:
    """({filename: clockwise degrees}, {filenames that are close-ups}) for the
    photos in `sources`, per services/orient. Never raises and never costs a
    photo: with the API off, over budget, or on any failure the answer is "as
    shot, and cut out as usual" — an empty turn map and no close-ups, which is
    exactly this file's behaviour before either answer existed. Keyed by name,
    which is unique within a batch (one directory) and is how optimize_batch
    looks a photo up.

    Imported here, not at module scope: orient reaches the Anthropic SDK, and
    everything else in this file is Pillow. Keeping the AI dependency inside
    the one function that needs it is what lets the photo gate in CI prove
    the pass on Pillow alone."""
    if not sources:
        return {}, frozenset()
    try:
        from . import orient
        found = orient.screen(sources, should_stop=should_stop)
        return found.rotations, found.details
    except Exception as exc:  # noqa: BLE001 - the screen is an enhancement
        log.warning("auto-orient: skipped (%s)", exc)
        return {}, frozenset()


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
            "a close-up, a dark item on a dark surface, or a photo OF a "
            "picture rather than of an object. Try cropping in tighter, "
            "shooting against a contrasting surface, or painting the "
            "background out with the white brush.")
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

def thumb_jpeg(path: Path, side: int = 512, quality: int = 72) -> bytes:
    """Small JPEG bytes for AI grouping calls — keeps a 40-photo request
    light. Upright per the camera's EXIF and opaque. `quality` goes up for a
    call that has to read a label off the copy."""
    from io import BytesIO
    with Image.open(path) as img:
        img = _flatten(ImageOps.exif_transpose(img))
        img.thumbnail((side, side), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()


def quarter_turns_jpeg(path: Path, side: int = 448) -> dict[int, bytes]:
    """The same photo at each of its four clockwise quarter-turns, as small
    JPEGs keyed by degrees — 0 is the photo as the camera meant it. What the
    orientation pass lays side by side when it asks which one is upright."""
    from io import BytesIO
    with Image.open(path) as img:
        img = _flatten(ImageOps.exif_transpose(img))
        img.thumbnail((side, side), Image.LANCZOS)
        out: dict[int, bytes] = {}
        for deg in (0, 90, 180, 270):
            turned = img.transpose(CW_TRANSPOSE[deg]) if deg else img
            buf = BytesIO()
            turned.save(buf, "JPEG", quality=80)
            out[deg] = buf.getvalue()
        return out


# The size vision calls send. Claude reads images in 28px patches and never
# downscales anything up to ~1092px on the long side (⌈1092/28⌉² = 1521 visual
# tokens); the full 1600px listing photo costs ⌈1600/28⌉² = 3364 tokens on
# high-resolution models AND double the upload bytes, for no extra detail the
# identify prompts actually use. Tag close-ups keep cropping from the full
# photo — this is only the whole-frame payload size.
VISION_SIDE = int(os.getenv("VISION_IMAGE_SIDE", "1092") or "1092")


def vision_copy(path: Path, side: int = 0) -> Path:
    """A cached, right-sized JPEG copy of an optimized photo for vision calls.

    Made from as_shot(path), not from `path`: a vision call is the app trying
    to work out what the item IS, and a cutout can only have taken detail
    away from that. See the note above _keep_as_shot.

    Lives in the session's vision/ dir (a sibling of optimized/, invisible to
    the image list and the R2 mirror) and is regenerated whenever either the
    source or the photo beside it is newer — photo edits rewrite the optimized
    file, so staleness is just an mtime comparison, and the same comparison is
    what hands the seller's edit back to the model. Returns `path` unchanged
    for anything that isn't a session's optimized photo, so callers can pass
    any path safely."""
    if path.parent.name != "optimized":
        return path
    side = side or VISION_SIDE
    src = as_shot(path)
    dst = path.parent.parent / "vision" / path.name
    try:
        if dst.is_file() and dst.stat().st_mtime >= max(
                path.stat().st_mtime, src.stat().st_mtime):
            return dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as img:
            img = _flatten(img)
            img.thumbnail((side, side), Image.LANCZOS)
            tmp = dst.with_name(dst.name + ".tmp")
            img.save(tmp, "JPEG", quality=85)
            os.replace(tmp, dst)  # atomic: a racing reader never sees a torn file
        return dst
    except Exception as exc:  # noqa: BLE001 - a copy is an optimization only
        log.info("vision copy skipped for %s: %s", path.name, exc)
        return path

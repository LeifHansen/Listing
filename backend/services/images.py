"""Listing photos: as shot, or with the background taken off — and upright.

Per photo the pass does four things. It honours the camera's EXIF
orientation. It turns the ITEM upright when it was photographed lying
sideways or on its head — which EXIF knows nothing about, so a vision model
is asked; see services/orient for how, and for why the last one was wrong
about everything that was not a shirt. It takes the background off when the
seller asked for that: one call to Photoroom (services/cutout_api) for the
matte, the item composited on white under a soft contact shadow — except on
a close-up of PART of an item, a tag or a label or a stitch, where there is
no background to take off and a cutout, asked anyway, deletes the item and
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
themselves in the editor. So: the engine's own matte ships, the frame the
seller composed is the frame that ships, and a wrong cutout is one tap of
Revert. Orientation came back on its own and on its own terms — rebuilt for
objects, applied only when two different looks agree, never in the way of
an upload — and a wrong turn is one tap of the rotate button.

There is no background-removal model on this server any more. The in-house
one (rembg) and every repair pass fitted to its failures are gone; a photo
Photoroom cannot cut — no key, an account out of images, an outage, nothing
found — is kept as shot and says why in `bg_error`, so it is never lost and
never silently left with its background on.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from PIL import Image, ImageFile, ImageFilter, ImageOps

from ..config import log
from ..ebay_errors import PHOTO_MIN_SIDE
from ..storage import natural_key
from . import artwork, cutout_api


def _save_atomic(img: Image.Image, dst: Path, **save_args) -> None:
    """Save a JPEG through a temp file and an atomic rename, so a reader (or
    a resume) never sees a torn file. The temp name is unique per process
    AND thread: two requests writing the same photo's copy at once would
    otherwise interleave into one temp file and rename a corrupt result into
    place, where a cache keeps it until the photo is next edited."""
    tmp = dst.with_name(f".{dst.name}.{os.getpid():x}.{threading.get_ident():x}.tmp")
    try:
        img.save(tmp, "JPEG", **save_args)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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

# The most pixels a photo may be DECODED at. A PNG, HEIC or WebP has no
# reduced-scale decode the way a JPEG has (`draft`), so its full size is what
# lands in memory: a 480KB PNG of one colour claiming 12500x12500 decoded to
# 1.3GB here, on a 2GB machine, from an anonymous upload. Pillow's own bomb
# guard only refuses past ~179MP. 60MP clears every phone's output (a 48MP
# HEIC is 48.8MP) and is checked AFTER `draft`, so a 200MP JPEG that decodes
# at an eighth still goes through.
MAX_PIXELS = int(os.getenv("PHOTO_MAX_PIXELS", "60000000") or 60_000_000)

# Modes a LANCZOS shrink handles as-is. A palette or bilevel image would be
# resampled NEAREST, so those are shrunk after _flatten instead, as before.
_SHRINK_EARLY = ("RGB", "RGBA", "L", "LA", "CMYK")


class TooLarge(ValueError):
    """A photo with more pixels than MAX_PIXELS, refused from its header
    before a pixel is decoded. A ValueError, so a route that maps those to a
    422 shows the seller the sentence."""


def check_pixels(img: Image.Image) -> None:
    """Raise TooLarge when `img` -- opened, not yet loaded, and drafted if it
    is going to be -- would decode to more than MAX_PIXELS."""
    w, h = img.size
    if w * h > MAX_PIXELS:
        raise TooLarge(
            f"This photo is {w}×{h} pixels, too large to process — the limit "
            f"is about {MAX_PIXELS // 1_000_000} megapixels. Export it smaller "
            "and upload it again.")


def below_ebay_minimum(paths) -> list[Path]:
    """The photos in `paths` that eBay would refuse for size: a longest side
    under PHOTO_MIN_SIDE (see ebay_errors.photos_too_small). This app never
    upscales, so a photo that arrived small (adopted from an eBay listing that
    was first put up with small photos, say) stays small.

    Only a photo that opens and measures small is counted. One that is missing
    or won't decode is left to the caller's own checks and to eBay. This is a
    size check, and a guess about an unreadable file would block a listing
    over a problem it might not have. Reads the header only, not the pixels."""
    small = []
    for path in paths:
        try:
            with Image.open(path) as img:
                if max(img.size) < PHOTO_MIN_SIDE:
                    small.append(Path(path))
        except Exception:  # noqa: BLE001 - unreadable is not "small"
            continue
    return small
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

# --- background removal ------------------------------------------------------
# A matte that keeps less than this share of the frame found no item — a
# close-up texture, a dark item on a dark table — and shipping it would ship
# a white square. The photo is kept as shot instead, and says so. Set in the
# gap between a thin product laid out on a sweep (a chain scores ~0.015, a
# belt ~0.05) and a matte that found nothing (an empty backdrop 0.0000, a
# speck of dust 0.0017).
_MIN_FG_COVERAGE = float(os.getenv("BG_MIN_COVERAGE", "0.005") or 0.005)


class Stopped(Exception):
    """Raised inside a photo batch that its caller called off mid-run. Nothing
    is left half-written: optimize() renames its result into place, so the
    photos already finished stay valid and a stopped run is still resumable."""


def engine_state() -> dict:
    """What the readiness probe says about background removal: the engine,
    whether it has a key, and whether that key only makes watermarked
    previews. See cutout_api.engine_state."""
    return cutout_api.engine_state()


# The contact shadow. An item composited on pure white with a hard edge reads
# as a sticker cut out and pasted down; the same item over a soft shadow reads
# as an object sitting on a surface, which is what a catalogue photo looks
# like. It was dropped with the rest of the old pipeline and asked for back --
# on its own, because unlike the passes around it, it is neither slow nor a
# surprise: it is drawn from the silhouette the engine already produced, so it
# costs no extra call and cannot change what the item looks like.
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


def compose(rgb: Image.Image, alpha: Image.Image,
            background: tuple[int, int, int] = WHITE) -> Image.Image:
    """The item on a solid `background` (white unless the seller chose a
    colour) under a soft contact shadow.

    The last step of EVERY cutout -- Photoroom's, the art path's geometry, or
    a mask made on the seller's own phone or browser (services/cutouts):
    whatever produced the matte, what ships is the same composite, so a pile
    cut by three different engines still looks like one shoot. Photoroom
    could draw the background and the shadow itself, but only on its Image
    Editing plan at about five times the price of the matte alone.
    """
    canvas = Image.new("RGB", rgb.size, background)
    canvas.paste(Image.new("RGB", rgb.size, _SHADOW_INK), (0, 0),
                 _contact_shadow(alpha))
    canvas.paste(rgb, (0, 0), alpha)
    return canvas


class Cut(NamedTuple):
    """A cutout and what made it: the composite that ships, the matte it was
    composited through (kept beside the photo, so the same cut can be put on
    another colour or exported transparent without being made again), and
    the engine's name."""

    image: Image.Image
    alpha: Image.Image
    engine: str


def _coverage(alpha: Image.Image, size: tuple[int, int]) -> float:
    """How much of the frame the matte actually keeps, 0-1: the share of
    pixels at least half opaque, counted straight off the alpha's histogram."""
    return sum(alpha.histogram()[128:]) / float(size[0] * size[1])


def cutout_with_engine(rgb: Image.Image,
                       background: tuple[int, int, int] = WHITE
                       ) -> tuple[Image.Image, str]:
    """The item on white (or `background`), cut out by Photoroom, and the
    engine's name. See _engine_cut."""
    cut = _engine_cut(rgb, background)
    return cut.image, cut.engine


def _engine_cut(rgb: Image.Image,
                background: tuple[int, int, int] = WHITE) -> Cut:
    """The item on white, cut out by Photoroom, as a Cut.

    Never None: every way this can fail raises cutout_api.CutoutApiError
    with a sentence a seller can read — no key configured, a key Photoroom
    refused, an account out of images, a rate limit that outlasted the
    retries, or a matte that kept nothing. There is no local model to fall
    back to, so the caller keeps the photo as shot and passes the sentence
    on (optimize() into `bg_error`, the studio into its 422).

    The one gate a matte still answers is "found nothing at all", which is
    engine-independent and cheap to be sure about. The shape and solidity
    guards that used to stand behind the local model are gone with it: every
    threshold in them was fitted to that model's output, and they are what
    refused necklaces, belts, bangles and guitars on flawless mattes.
    """
    cut = cutout_api.photoroom_cutout(rgb)
    if cut is None:
        log.warning("bg-removal: asked for a cutout but PHOTOROOM_API_KEY is "
                    "not set")
        raise cutout_api.CutoutApiError(cutout_api.UNAVAILABLE)
    # A service answers at the size it was SENT, and jpeg_payload caps that
    # at BG_API_MAX_SIDE. Every caller is already inside the cap today, so
    # nothing is resized -- but an alpha of one size against a photo of
    # another understates coverage and composites at the wrong size. Cheap to
    # make impossible rather than to remember.
    if cut.size != rgb.size:
        log.info("bg-removal: photoroom answered at %dx%d for a %dx%d photo — "
                 "scaling the matte back", cut.width, cut.height,
                 rgb.width, rgb.height)
        cut = cut.resize(rgb.size, Image.LANCZOS)
    alpha = cut.split()[3]
    coverage = _coverage(alpha, rgb.size)
    if coverage < _MIN_FG_COVERAGE:
        log.info("bg-removal: photoroom found no item (coverage %.4f)",
                 coverage)
        raise cutout_api.NothingFound(
            "Photoroom found no item in this photo — it was kept as shot.")
    log.info("bg-removal: photoroom kept %.3f of the frame", coverage)
    return Cut(compose(cut.convert("RGB"), alpha, background), alpha,
               "photoroom")


def _cutout_or_border(rgb: Image.Image,
                      background: tuple[int, int, int] = WHITE
                      ) -> tuple[Image.Image, str]:
    """_cutout_or_border_cut, as (image, engine)."""
    cut = _cutout_or_border_cut(rgb, background)
    return cut.image, cut.engine


def _cutout_or_border_cut(rgb: Image.Image,
                          background: tuple[int, int, int] = WHITE) -> Cut:
    """Photoroom's cutout, or -- when Photoroom found NOTHING -- the item cut
    to its own rectangular outline ("border"). A tray, a sign, a plaque, a
    boxed set: a cutout fixes on the picture printed on its face and keeps
    nothing it would ship, but the shape of the thing is a rectangle, so the
    geometry that cuts a framed print answers for it. artwork.outline()
    answers None for anything that is not a rectangle, so a garment is still
    kept as shot, with Photoroom's reason.

    Any OTHER failure is re-raised untouched: an outage or an empty Photoroom
    account is said, never papered over with geometry."""
    try:
        return _engine_cut(rgb, background)
    except cutout_api.NothingFound:
        rescued = _rectangle_cut(rgb, "border", background)
        if rescued is None:
            raise
        return rescued


# What a photo of ART gets instead of a cutout when its outline cannot be
# found. Same shape as DETAIL_KEPT_AS_SHOT: not an error and not a refusal,
# just nothing attempted, reported in `bg_error` so the seller is told and the
# charge comes back.
#
# It used to say the picture's edge "isn't clearly in the frame", and sellers
# read it as the app not seeing what was plainly there: the piece WAS in the
# frame, on every photo, and the scan that could not find it was the thing at
# fault. What actually stops the outline being found is an edge that does not
# stand out from what is behind it, so that is what it says — along with the
# one thing the seller can do about it.
ART_NO_BORDER_KEPT_AS_SHOT = (
    "This is a picture, and its outer edge couldn't be told apart from what's "
    "behind it — cutting to a guessed edge would crop the artwork, so it was "
    "kept as shot. A backdrop that contrasts with the frame or edge helps.")


def _rectangle_cut(rgb: Image.Image, engine: str,
                   background: tuple[int, int, int] = WHITE) -> Optional[Cut]:
    """_rectangle_alpha composited, as a Cut named `engine`, or None."""
    alpha = _rectangle_alpha(rgb)
    if alpha is None:
        return None
    return Cut(compose(rgb, alpha, background), alpha, engine)


def _rectangle_alpha(rgb: Image.Image) -> Optional[Image.Image]:
    """The matte of the photo cut to the outer outline of the FLAT RECTANGULAR THING in it
    — or of each, when there are two side by side — kept whole on white; or
    None when there isn't one.

    artwork.outline() on its own, composited. Geometry, no model: the outline
    is found in the photo, and what ships is its four corners filled solid,
    so nothing inside it can be removed.

    Two callers, and they want it for two different reasons. art_cutout asks
    first because a found outline cannot be wrong about what is inside it.
    And the ordinary cutout path asks LAST, after Photoroom has found nothing
    -- see _cutout_or_border.
    """
    quads = artwork.outline(rgb)
    if quads is None:
        return None
    log.info("art cutout: kept everything inside the outline %s of a %dx%d "
             "photo", quads, rgb.width, rgb.height)
    return artwork.outline_matte(rgb.size, quads)


def art_cutout(rgb: Image.Image, locate: Optional[Callable] = None,
               background: tuple[int, int, int] = WHITE
               ) -> Optional[Image.Image]:
    """art_cut's composite, or None. See art_cut."""
    cut = art_cut(rgb, locate, background)
    return cut.image if cut is not None else None


def art_cut(rgb: Image.Image, locate: Optional[Callable] = None,
            background: tuple[int, int, int] = WHITE) -> Optional[Cut]:
    """A PICTURE on white: everything inside its outer border, kept whole.

    The separate path for paintings, prints, posters, drawings, photographs
    and anything else whose own front surface is an image. The model never
    supplies the matte, and that is the entire point — see services/artwork. A
    salient-object model handed a photograph OF A PICTURE finds the subject
    the picture depicts and deletes the artwork around it, which is how a
    Marcia Alpert gouache reached a listing as the baby out of it, floating on
    white, with the water and the quilt she painted it on gone.

    None of the guards in this file could have caught that, and none of them
    is wrong: they read the matte, and the matte of a baby lifted out of a
    painting is one connected, solid, box-filling region — arithmetically a
    perfect cutout. The thing that separates it from one is not in the matte.

    So the matte here is a FILLED FOUR-CORNERED OUTLINE, and nothing inside
    it is ever examined, let alone removed. None means the outline could not
    be found and the photo must be kept exactly as shot; it never means "cut
    to what the model kept", which is the failure this exists to prevent.

    A SECOND LOOK at the outline, and only at the outline. `outline()` is
    asked first and is still the trusted answer, because it is geometry and
    cannot be wrong about what is inside the corners it returns. It handles a
    hand-held photo, a lit wall, a shadow and a textured floor itself, so what
    reaches the second look is a picture whose edge could not be found at
    all: one lying on a surface close to its own colour. Then a segmentation
    matte's outer SHAPE is offered instead, and artwork.quad_from_alpha
    refuses it unless it is a rectangle at some angle rather than a subject
    lifted out of one. Either way what ships is solid, from
    artwork.outline_matte() or artwork.quad(). There is still no code path
    here that can remove a pixel from inside the outline.

    That matte is Photoroom's, asked for by _border_alpha, and only when
    outline() found nothing -- unless the caller hands its own `locate`: a
    mask made on the seller's device (services/cutouts) is offered the same
    way, used the same way, and costs nothing. `locate(rgb)` answers
    (alpha or None, who).
    """
    out = _rectangle_cut(rgb, "art", background)
    if out is not None:
        return out
    alpha, who = (locate or _border_alpha)(rgb)
    if alpha is None:
        return None
    # Four corners rather than a box, because the photo that gets here is the
    # one outline() could not find at all, and a print that faint against its
    # background is usually also lying at an angle on it. See
    # artwork.quad_from_alpha.
    corners = artwork.quad_from_alpha(rgb.size, alpha)
    if corners is None:
        return None
    log.info("art cutout: kept the picture whole inside %s (located by the "
             "%s engine) of a %dx%d photo", corners, who, rgb.width, rgb.height)
    solid = artwork.quad(rgb.size, corners)
    return Cut(compose(rgb, solid, background), solid, "art")


def _border_alpha(rgb: Image.Image) -> tuple[Optional[Image.Image], str]:
    """A matte to LOCATE a picture's outer edge with, and what produced it,
    or (None, "") when there is none to be had.

    Only the outer SHAPE of it is ever used. artwork.quad_from_alpha refuses
    anything that is not a rectangle at some angle, and what the caller ships
    is artwork.quad() -- a solid convex shape -- so there is no code path here
    that can remove a pixel from inside a picture's border. That is the
    invariant services/artwork exists for and nothing here touches it.

    Photoroom is a segmentation model with the same failure on a picture as
    any other: handed a painting, it lifts the subject out of it. What makes
    its answer safe here is that the answer is used as GEOMETRY and checked
    for being a rectangle. A baby lifted out of a painting is not a rectangle
    at any angle, so it is refused and the photo is kept as shot.

    The cost is one call, paid only by a picture whose border the geometric
    scan could not find -- the photo that was otherwise getting nothing at
    all. A failure is not worth surfacing: the photo is about to be kept as
    shot, which is exactly what would have happened without this call.
    """
    try:
        cut = cutout_api.photoroom_cutout(rgb)
    except cutout_api.CutoutApiError as exc:
        log.info("art border: photoroom couldn't help (%s)", exc)
        return None, ""
    if cut is None:
        return None, ""
    if cut.size != rgb.size:
        cut = cut.resize(rgb.size, Image.LANCZOS)
    return cut.split()[3], "photoroom"


def _flatten(img: Image.Image) -> Image.Image:
    """Any transparency composited onto WHITE, as opaque RGB.

    A bare `.convert("RGB")` paints transparent pixels BLACK, so a PNG that
    arrived already cut out (an iPhone "lift subject" shot, another tool's
    export) would reach the engine, and the listing, as an item on a black
    field."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, WHITE)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    if img.mode.startswith("I"):
        # 16-bit greyscale (a scan, a TIFF): convert("RGB") CLIPS at 255
        # rather than scaling, so a mid grey came out pure white. Brought
        # into 8 bits first -- unless the values already fit in them.
        wide = img if img.mode == "I" else img.convert("I")
        if wide.getextrema()[1] > 255:
            wide = wide.point(lambda v: v * (1 / 256))
        return wide.convert("L").convert("RGB")
    return img.convert("RGB") if img.mode != "RGB" else img


def flatten(img: Image.Image) -> Image.Image:
    """Public name for _flatten: any transparency onto WHITE, as opaque RGB.
    For the routes that take a picture from the client (the studio's save)
    and must never paint a transparent pixel black."""
    return _flatten(img)


def write_jpeg(img: Image.Image, dst: Path, quality: int = JPEG_QUALITY) -> None:
    """`img` flattened onto white and written to `dst` as a JPEG, atomically
    and without metadata -- the one way a route outside the photo pass should
    put a working copy on the volume. See _save_atomic for why the temp name
    is unique per process and thread."""
    _save_atomic(_flatten(img), dst, quality=quality, optimize=True)


def _load(src: Path) -> tuple[Image.Image, tuple[int, int]]:
    """The photo as the camera meant it — upright per its EXIF, opaque, and no
    larger than TARGET_SIZE on its longest side — plus the size it was shot
    at. Nothing downstream needs more pixels than the output has, so a JPEG
    is asked to decode at a reduced scale up front (`draft`): a 12MP phone
    photo then never exists at full size in memory at all."""
    with Image.open(src) as raw:
        shot = raw.size
        w, h = raw.size
        scale = TARGET_SIZE / max(w, h)
        if scale < 1:
            raw.draft(None, (max(1, round(w * scale)), max(1, round(h * scale))))
        # From the header, after draft and before anything decodes: a PNG's
        # getexif() below loads the whole image when its EXIF trails the
        # pixels. See MAX_PIXELS.
        check_pixels(raw)
        # Orientation 5-8 mean the sensor was sideways, so the photo the
        # seller took is the stored one turned — report that size.
        if raw.getexif().get(274, 1) in (5, 6, 7, 8):
            shot = (shot[1], shot[0])
        # Shrunk BEFORE it is turned: exif_transpose copies the whole image,
        # even one already upright, and for a format draft cannot reduce that
        # copy was a second full-size decode in memory.
        if raw.mode in _SHRINK_EARLY and max(raw.size) > TARGET_SIZE:
            raw.thumbnail((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
        img = ImageOps.exif_transpose(raw)
        img.load()
    # Read before _flatten, which composites a transparent photo onto a fresh
    # canvas that carries no profile.
    icc = img.info.get("icc_profile")
    img = _flatten(img)
    if max(img.size) > TARGET_SIZE:
        img.thumbnail((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    return _to_srgb(img, icc), shot


def _to_srgb(img: Image.Image, icc: Optional[bytes]) -> Image.Image:
    """`img` (RGB) moved out of its embedded colour profile into sRGB.

    The saved JPEG carries no metadata, and that includes the profile -- so
    the pixels have to be in the colour space an untagged JPEG is read in.
    An iPhone shoots Display P3: its numbers read as sRGB put every listing
    photo on eBay visibly duller than the item. An sRGB profile, or one
    lcms cannot read, leaves the pixels as they are."""
    if not icc:
        return img
    try:
        from io import BytesIO

        from PIL import ImageCms

        profile = ImageCms.ImageCmsProfile(BytesIO(icc))
        if "srgb" in ImageCms.getProfileDescription(profile).lower():
            return img
        return ImageCms.profileToProfile(img, profile,
                                         ImageCms.createProfile("sRGB"),
                                         outputMode="RGB")
    except Exception as exc:  # noqa: BLE001 - colour is never worth a photo
        log.info("colour profile not applied (%s)", type(exc).__name__)
        return img


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
        _save_atomic(img, out, quality=JPEG_QUALITY, optimize=True)
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


# --- the matte the cutout was made through -------------------------------------
#
# Kept beside the photo as mask/<stem>.png -- one small greyscale PNG -- for
# the same reason as the as-shot copy and under the same mtime rule: it
# speaks for the working copy only while it is at least as new as it. With
# both kept, a cut photo can be put on another colour, or handed over as a
# transparent PNG, without anyone paying for the cut a second time.
_MASK = "mask"


def mask_file(path: Path) -> Path:
    """Where the matte behind the optimized photo `path` is kept."""
    return path.parent.parent / _MASK / (path.stem + ".png")


def _keep_mask(dst: Path, alpha: Image.Image) -> None:
    """Keep `alpha` as the matte behind the photo written to `dst`.
    Best-effort, like _keep_as_shot, and only for a real session photo."""
    if dst.parent.name != "optimized":
        return
    try:
        out = mask_file(dst)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.{os.getpid():x}.{threading.get_ident():x}.tmp")
        try:
            alpha.convert("L").save(tmp, "PNG", optimize=True)
            os.replace(tmp, out)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    except Exception as exc:  # noqa: BLE001 - a cache is never worth a photo
        log.info("mask copy skipped for %s: %s", dst.name, exc)


def current_mask(path: Path) -> Optional[Path]:
    """The matte behind `path` while it still speaks for it -- kept, and at
    least as new as the working copy -- else None."""
    if path.parent.name != "optimized":
        return None
    kept = mask_file(path)
    try:
        if kept.stat().st_mtime >= path.stat().st_mtime:
            return kept
    except OSError:
        pass
    return None


# --- what the screen said about each photo --------------------------------------
#
# orient.screen decides, per photo, whether it is a close-up of a detail and
# whether the item is a picture -- and those answers used to live exactly as
# long as the batch did. The studio's Remove background, pressed later on the
# same photo, knew neither, so a painting opened in the editor and cut got
# the subject lifted out of it: the very failure the batch path was built to
# prevent. So the answers are written down beside the photos, by name, in one
# small JSON file per session. Only "true" is ever written: a photo the screen
# never saw, or saw as an ordinary item, has no entry, which is what it had
# before any of this existed.
_FLAGS = "photo_flags.json"
_flags_lock = threading.Lock()


def _flags_file(path: Path) -> Path:
    return path.parent.parent / _FLAGS


def photo_flags(path: Path) -> dict:
    """{"art": bool, "detail": bool} for the optimized session photo `path`.
    Both False when nothing was recorded, or for any other path."""
    out = {"art": False, "detail": False}
    if path.parent.name != "optimized":
        return out
    try:
        data = json.loads(_flags_file(path).read_text())
        rec = data.get(path.name) if isinstance(data, dict) else None
    except (OSError, ValueError):
        rec = None
    if isinstance(rec, dict):
        out["art"] = bool(rec.get("art"))
        out["detail"] = bool(rec.get("detail"))
    return out


def note_flags(path: Path, art: bool = False, detail: bool = False) -> None:
    """Record what the screen said about `path`. Best-effort; a no-op for a
    plain photo or a path outside a session."""
    if path.parent.name != "optimized" or not (art or detail):
        return
    flags = {k: True for k, v in (("art", art), ("detail", detail)) if v}
    _merge_flags(_flags_file(path), {path.name: flags})


def carry_flags(src_opt: Path, dst_opt: Path, mapping: dict[str, str]) -> None:
    """Copy the recorded flags of `mapping`'s photos from one session's
    optimized/ to another's, renamed -- the bulk split, which moves photos
    from the staging pile into each item's own session."""
    try:
        data = json.loads((src_opt.parent / _FLAGS).read_text())
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    moved = {new: data[old] for old, new in mapping.items()
             if isinstance(data.get(old), dict)}
    if moved:
        _merge_flags(dst_opt.parent / _FLAGS, moved)


def forget_flags(path: Path) -> None:
    """Drop what was recorded about `path` (the photo was deleted)."""
    if path.parent.name != "optimized":
        return
    _merge_flags(_flags_file(path), {path.name: None})


def _merge_flags(file: Path, changes: dict) -> None:
    with _flags_lock:
        try:
            data = json.loads(file.read_text())
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        for name, rec in changes.items():
            if rec is None:
                data.pop(name, None)
            else:
                data[name] = rec
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            tmp = file.with_name(f".{file.name}.{os.getpid():x}.{threading.get_ident():x}.tmp")
            tmp.write_text(json.dumps(data, sort_keys=True))
            os.replace(tmp, file)
        except OSError as exc:
            log.info("photo flags not written (%s)", exc)


def optimize(src: Path, dst: Path, remove_bg: bool = False,
             rotate: int = 0, detail: bool = False, art: bool = False,
             device: bool = False,
             background: tuple[int, int, int] = WHITE) -> dict:
    """One photo: as shot, or cut out on white; sized for eBay; no EXIF.
    Returns what was done. A cutout that cannot be had — no key, an account
    out of images, an outage, nothing found — keeps the photo as shot and
    says why in `bg_error`, so the caller can give the charge back.

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
    deletes the garment behind it. Nothing reading the matte can catch that —
    the matte of a torn-out label is one opaque region that fills its own box,
    which is what a GOOD cutout looks like — so the photo has to be spared
    before the engine is asked, not after. See orient._details.

    A flat RECTANGULAR thing Photoroom found nothing in -- a tray, a sign, a
    boxed set, whose printed face is what a cutout fixes on -- is cut to its
    own scanned outline instead (`bg_engine` "border"). Only for "found
    nothing": an outage or an account problem is said, not papered over with
    geometry, or a pile would come back with its trays cut and its shirts not.

    `art` says the item in this photo IS A PICTURE — a painting, print,
    poster, drawing or photograph — decided by that same pass. It takes the
    art path (art_cutout), which keeps everything inside the picture's outer
    border and never asks the engine which part of a painting is interesting.
    Same reason as `detail`, one step further: on a picture the engine's
    answer is not merely useless, it is confidently wrong in a way no guard
    reading the matte can see. See orient._art and services/artwork.

    `device` (with remove_bg) says the seller's own phone or browser will
    make the matte -- free -- so nothing here may spend a Photoroom call. The
    screening still decides: a close-up is kept as shot, as above; a picture
    is cut by its scanned border, which is geometry and costs nothing, and
    otherwise waits (`bg_pending` "art") for a device mask to LOCATE its edge;
    everything else is kept as shot for now with `bg_pending` "item". The
    caller records what is pending; services/cutouts finishes it.

    `background` is the solid colour a cutout is composited onto: white
    unless the seller chose another (prefs `cutout_background`)."""
    img, shot = _load(src)
    turn = int(rotate or 0) % 360
    turn = turn if turn in CW_TRANSPOSE else 0
    if turn:
        img = img.transpose(CW_TRANSPOSE[turn])
    bg_removed, bg_error = False, None
    bg_engine = ""
    bg_pending = ""
    faithful = None
    matte = None
    if remove_bg and detail:
        bg_error = DETAIL_KEPT_AS_SHOT
    elif remove_bg and device:
        # Nothing here is paid for. A picture whose border the scan can find
        # is cut now, by geometry; anything else waits for the device.
        cut = _rectangle_cut(img, "art", background) if art else None
        if cut is not None:
            faithful, img, matte = img, cut.image, cut.alpha
            bg_removed, bg_engine = True, cut.engine
        else:
            bg_pending = "art" if art else "item"
    elif remove_bg:
        try:
            # A picture never reaches the engine as a cutout. Its border
            # decides the matte, or nothing happens at all — art_cutout
            # returning None means "keep as shot", never "cut it out anyway",
            # because a cutout is what lifts the baby out of the painting.
            # Photoroom may help FIND that border (see art_cutout); it never
            # supplies the matte for a picture.
            if art:
                cut = art_cut(img, background=background)
                if cut is None:
                    bg_error = ART_NO_BORDER_KEPT_AS_SHOT
            else:
                cut = _cutout_or_border_cut(img, background)
            out = cut.image if cut is not None else None
            if cut is not None:
                bg_engine, matte = cut.engine, cut.alpha
        except cutout_api.CutoutApiError as exc:
            # Already a sentence for the seller: "unavailable right now",
            # "busy, try again in a minute", "found no item".
            log.info("bg-removal: keeping %s as shot (%s)", src.name, exc)
            out, matte, bg_error = None, None, str(exc)
        except Exception as exc:  # noqa: BLE001 - a photo must never fail for its cutout
            log.warning("bg-removal: keeping %s as shot (%s)",
                        src.name, type(exc).__name__)
            out, matte = None, None
            bg_error = ("Background removal failed — the photo was kept as "
                        "shot.")
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
    _save_atomic(img, dst, quality=JPEG_QUALITY, optimize=True)
    # AFTER the working copy, never before: as_shot() prefers this file only
    # while it is at least as new as the photo beside it, which is what makes
    # a later edit in the studio win automatically.
    if faithful is not None:
        _keep_as_shot(dst, faithful)
    # And the matte after both, under the same rule.
    if matte is not None:
        _keep_mask(dst, matte)
    out = {"file": dst.name, "original_size": shot, "output_size": img.size,
           "background_removed": bg_removed}
    if turn:
        out["rotated"] = turn
    # What the screen said about this photo, for whoever records it (the
    # studio honours it later: a picture is cut by its border, a close-up is
    # asked about first). Only when true, so a plain photo's result is
    # unchanged.
    if art:
        out["art"] = True
    if detail:
        out["detail"] = True
    if bg_pending:
        out["bg_pending"] = bg_pending
    if bg_removed:
        # Which path actually cut it: "photoroom"; "art" for a picture cut to
        # its own border; "border" for a flat rectangular item Photoroom
        # found nothing in.
        out["bg_engine"] = bg_engine
    if bg_error:
        out["bg_error"] = bg_error
    return out


_EXTS = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".gif",
         ".tif", ".tiff", ".heic", ".heif", ".hif", ".avif"}


def optimize_all(src_dir: Path, dst_dir: Path, remove_bg: bool = False,
                 progress=None, should_stop=None, device: bool = False,
                 background: tuple[int, int, int] = WHITE,
                 on_result=None) -> list[dict]:
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
        done_already=len(results), grand_total=len(jobs),
        device=device, background=background, on_result=on_result)))
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
                   grand_total: int = 0, should_stop=None,
                   device: bool = False,
                   background: tuple[int, int, int] = WHITE,
                   on_result=None) -> list[dict]:
    """(src, dst) jobs in order; a failed photo yields {"file", "error"}
    instead of raising. Serial: a decode, one Photoroom call, a composite and
    an encode per photo, and a batch is already one of several running in
    the bulk worker pool.

    `done_already`/`grand_total` describe work this call is NOT doing — the
    photos a resumed batch found already finished — so `progress` keeps
    saying "38 of 40" across a restart instead of dropping to "0 of 2".

    `device`/`background` are optimize()'s. `on_result(dst, result)` is
    called as each photo is finished -- before the next starts -- so a caller
    can write down what is pending for the device as it happens, and a batch
    the machine dies in the middle of has recorded every photo it finished.
    It must not raise; if it does, it is logged and the batch goes on."""
    total = grand_total or len(jobs)
    done = done_already
    results = []
    if jobs and should_stop is not None and should_stop():
        raise Stopped()
    # One batched look at every photo still to do, up front. It answers three
    # things: which photos have their ITEM lying sideways or on its head, so
    # each is turned before its cutout rather than after; which are close-ups
    # of PART of an item, so the cutout is not run on them at all; and which
    # are PICTURES, so the cutout keeps everything inside their own border
    # instead of asking a model which part of a painting matters.
    # Best-effort and bounded: a photo the pass cannot answer for is left as
    # shot and cut out as usual.
    turns, details, art = _screen_for([src for src, _dst in jobs], should_stop)
    for src, dst in jobs:
        if should_stop is not None and should_stop():
            raise Stopped()
        try:
            result = optimize(src, dst, remove_bg,
                              rotate=turns.get(src.name, 0),
                              detail=src.name in details,
                              art=src.name in art,
                              device=device, background=background)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad image
            result = {"file": src.name, "error": str(exc)}
        else:
            note_flags(dst.with_suffix(".jpg"), art=bool(result.get("art")),
                       detail=bool(result.get("detail")))
        if on_result is not None:
            try:
                on_result(dst.with_suffix(".jpg"), result)
            except Exception as exc:  # noqa: BLE001 - bookkeeping never costs a photo
                log.warning("photo pass: on_result failed for %s (%s)",
                            dst.name, type(exc).__name__)
        results.append(result)
        done += 1
        if progress:
            try:
                progress(done, total)
            except Exception:  # noqa: BLE001 - progress is display-only
                pass
    return results


def _screen_for(sources: list[Path], should_stop=None) -> tuple[
        dict[str, int], frozenset[str], frozenset[str]]:
    """({filename: clockwise degrees}, {filenames that are close-ups},
    {filenames whose item is a picture}) for the photos in `sources`, per
    services/orient. Never raises and never costs a photo: with the API off,
    over budget, or on any failure the answer is "as shot, and cut out as
    usual" — an empty turn map, no close-ups and no pictures, which is exactly
    this file's behaviour before any of those answers existed. Keyed by name,
    which is unique within a batch (one directory) and is how optimize_batch
    looks a photo up.

    NOTE what the empty answer means for art: a picture the screen never got
    to see is cut out like any other photo, and a cutout lifts the subject
    out of the painting. That is the reason this answer is worth having a
    whole pass for rather than a heuristic here.

    Imported here, not at module scope: orient reaches the Anthropic SDK, and
    everything else in this file is Pillow. Keeping the AI dependency inside
    the one function that needs it is what lets the photo gate in CI prove
    the pass on Pillow alone."""
    if not sources:
        return {}, frozenset(), frozenset()
    try:
        from . import orient
        found = orient.screen(sources, should_stop=should_stop)
        return found.rotations, found.details, found.art
    except Exception as exc:  # noqa: BLE001 - the screen is an enhancement
        log.warning("auto-orient: skipped (%s)", exc)
        return {}, frozenset(), frozenset()


# --- the photo studio --------------------------------------------------------

def cloud_cut(rgb: Image.Image,
              background: tuple[int, int, int] = WHITE) -> Cut:
    """Photoroom's cutout of an ordinary item, or its own rectangular edge
    when Photoroom found nothing in a flat rectangular thing, as a Cut.
    Raises cutout_api.CutoutApiError with the seller's sentence otherwise.
    (A picture goes through art_cut instead, never this.)"""
    return _cutout_or_border_cut(rgb, background)


def remove_background_white(img: Image.Image, art: bool = False,
                            background: tuple[int, int, int] = WHITE
                            ) -> tuple[Image.Image, str]:
    """The studio's "Remove background": the item on white, and the name of
    the engine that did it. Raises cutout_api.CutoutApiError — a ValueError,
    so the route answers 422 with the sentence — when Photoroom could not
    cut it, so the editor can tell the seller why instead of silently doing
    nothing.

    The rectangle rescue is the same as the photo pass's, and the studio is
    where it matters most: this is the button a seller presses AFTER a batch
    left a photo as shot, so a second refusal is the end of the road for that
    listing.

    `art` is what the photo pass recorded about this photo (photo_flags): a
    PICTURE. The studio used to cut every photo the same way, so a painting
    the batch had carefully spared was lifted out of itself the moment the
    seller pressed this button on it. A picture takes the art path here too,
    and a picture whose edge cannot be found is said, not cut."""
    rgb = _flatten(img)
    if art:
        cut = art_cut(rgb, background=background)
        if cut is None:
            raise cutout_api.CutoutApiError(ART_NO_BORDER_KEPT_AS_SHOT)
        return cut.image, cut.engine
    return _cutout_or_border(rgb, background)


# --- copies for the AI ---------------------------------------------------------

def _draft_to(img: Image.Image, side: int) -> None:
    """Ask a JPEG to decode at a reduced scale, for a copy of at most `side`.

    The same trick `_load` uses, and for the same reason -- but these two
    functions are pointed at the CAMERA ORIGINAL (the screen pass reads `src`,
    not the optimized output), so without it a 12MP phone photo is fully
    decoded to be thrown away at 512px. Measured on a 4032x3024 JPEG: 178ms
    down to 45ms, and the pass runs over every photo in the batch, twice
    (screen, then confirm) -- while holding a threadpool slot.

    `None` keeps the mode, so a CMYK or greyscale original is not silently
    converted before _flatten sees it. Decoding is in halves and never below
    the size asked for, so the LANCZOS resize below still has more pixels than
    it needs and the output is unchanged for practical purposes. A no-op for
    any format that cannot do it, which is what makes it safe to call blind.
    """
    img.draft(None, (side, side))


def _small_upright(path: Path, side: int) -> Image.Image:
    """`path` no larger than `side` on its longest side, upright per the
    camera's EXIF and opaque — the copy both AI thumbnails below start from."""
    with Image.open(path) as img:
        _draft_to(img, side)
        check_pixels(img)
        # Shrunk before it is turned, as in _load: exif_transpose copies.
        if img.mode in _SHRINK_EARLY:
            img.thumbnail((side, side), Image.LANCZOS)
        img = _flatten(ImageOps.exif_transpose(img))
        img.thumbnail((side, side), Image.LANCZOS)
        return img


def thumb_jpeg(path: Path, side: int = 512, quality: int = 72) -> bytes:
    """Small JPEG bytes for AI grouping calls — keeps a 40-photo request
    light. Upright per the camera's EXIF and opaque. `quality` goes up for a
    call that has to read a label off the copy."""
    from io import BytesIO
    buf = BytesIO()
    _small_upright(path, side).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def quarter_turns_jpeg(path: Path, side: int = 448) -> dict[int, bytes]:
    """The same photo at each of its four clockwise quarter-turns, as small
    JPEGs keyed by degrees — 0 is the photo as the camera meant it. What the
    orientation pass lays side by side when it asks which one is upright."""
    from io import BytesIO
    img = _small_upright(path, side)
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
            _save_atomic(img, dst, quality=85)
        return dst
    except Exception as exc:  # noqa: BLE001 - a copy is an optimization only
        log.info("vision copy skipped for %s: %s", path.name, exc)
        return path

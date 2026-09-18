"""Where a picture ENDS — the outer border of a painting, print or poster.

The report, with a screenshot: a Marcia Alpert gouache, "Baby in a Basket",
photographed front, back, signature and detail. The listing came back with the
baby cut out of the painting. Not the painting cut out of the wall — the BABY,
lifted off the teal water and the patchwork quilt it is painted on, floating on
white. Another photo kept the turtle and the signature and deleted the rest.

Every guard in services/images passed, and they were right to. They read the
ALPHA and ask whether what survived looks like a product: is it one connected
piece, does it fill its own bounding box, is it solid through the middle. A
baby lifted out of a painting is all three. It is, arithmetically, a perfect
cutout. The information that separates it from one is not in the matte at all
— it is the fact that the thing being photographed is ITSELF A PICTURE, and a
salient-object model handed a picture answers the only question it knows:
which part of this is the subject. For a painting the honest answer is "all of
it", and that is the one answer the model cannot give.

So art does not go to the model. It gets its own rule, and the rule is
geometric rather than learned:

    A picture is a RECTANGLE — at whatever angle it was held. Find its
    outer border and keep everything inside it, whole. Never ask what is
    interesting within it.

That makes the seller's requirement structural instead of statistical.
`border()` returns a box; `mask()` turns a box into a filled rectangle. There
is no code path here that can remove a pixel from inside the border, because
nothing here ever looks inside it.

AND WHEN THE BORDER IS NOT THERE, NOTHING HAPPENS. A picture shot close enough
that it runs off the edge of the frame, a canvas photographed at an angle, a
print on paper the same colour as the table — none of those have a findable
border, and a guess at one crops a painting. So every uncertainty returns None
and the photo is kept exactly as shot. That direction is nearly free: the
seller loses an opt-in background removal on one photo. The other direction
destroys the item the listing is for.

Pillow only, no numpy and no model — the same constraint the rest of the photo
pass works under, which is what lets CI prove this on Pillow alone.
"""
from __future__ import annotations

import math
import os
from typing import Optional

from PIL import (Image, ImageChops, ImageDraw, ImageFilter, ImageStat)

from ..config import log

# The working size. Everything below is a question about a border several
# hundred pixels long, so a 240px thumbnail answers it as well as a 4000px
# photo and answers it in Python-loop time. Coordinates are scaled back up
# before they are returned.
_SIDE = int(os.getenv("ART_BORDER_SIDE", "240") or 240)

# How far a pixel's colour must sit from the surround before it counts as part
# of the picture rather than part of the wall. Chebyshev distance over RGB,
# 0-255. Low, because the failure of being too generous here is a border found
# slightly wide — which keeps MORE of the artwork.
_SURROUND_DIST = int(os.getenv("ART_SURROUND_DIST", "26") or 26)

# ...and how much local contrast makes a pixel "busy". A wall, a table top and
# a sheet of backing card are smooth; a picture is not. This is what finds a
# print whose colours happen to match the surface it is lying on.
_BUSY_LEVEL = int(os.getenv("ART_BUSY_LEVEL", "18") or 18)

# The band around the edge of the photo that is ASSUMED to be surround, as a
# fraction of the short side, when sampling what the surround looks like. A
# picture that reaches into this band is not disqualified by it: it just makes
# the sample less pure, and an impure sample finds a wider border, which is
# the safe direction.
_SURROUND_BAND = 0.06

# Closing the content mask: a picture with a pale sky in it comes back as
# several blobs, and the border is the box around all of them TOGETHER only if
# they are one region. Dilate then erode by this many cells (at _SIDE) to join
# them up. Too small and a picture fragments; too large and the picture merges
# with a shadow beside it — which, again, finds a wider border.
_CLOSE = int(os.getenv("ART_CLOSE", "4") or 4)

# A picture FILLS ITS OWN BOX, because it is a rectangle. This is the test that
# separates "I found the artwork" from "I found two dark objects on a table
# that happen to span a rectangle between them". Set high on purpose: 0.85 of
# the bounding box is a shape with corners, and the cost of failing it is that
# the photo is kept as shot.
_MIN_RECT_FILL = float(os.getenv("ART_MIN_RECT_FILL", "0.85") or 0.85)

# ...and the same question asked of a picture that is not square-on in the
# photo, which is very nearly all of them.
#
# The report: a framed picture, seven photos, every one of them whole in the
# frame on a plain white background, and seven refused. A hand-held shot is a
# rectangle turned a few degrees, and a turned rectangle fills its AXIS-ALIGNED
# box badly -- 0.94 at 2 degrees, 0.85 at 5, 0.74 at 10. Measured that way the
# test above refuses an ordinary phone photo of an ordinary framed picture for
# the crime of being hand-held, and refuses the whole set the same way, because
# one pair of hands tilts them all. Nothing about the photo was wrong and
# nothing was logged that a seller could see; the border was simply never
# found. The same arithmetic was already understood for a remote engine's matte
# -- see _MIN_ALPHA_RECT_FILL, which was fitted at the best angle for exactly
# this reason -- but the geometric scan, which is what runs when no remote
# engine is configured at all, still asked whether the picture was UPRIGHT.
#
# So a shape that fails upright is asked again at its BEST ANGLE, and has to
# clear a higher bar there: "is this a rectangle at SOME angle" is a weaker
# question than "is this an upright rectangle", so it must be put more strictly
# to keep out the shapes _MIN_RECT_FILL exists to refuse. The gap is wide and
# does not close with rotation, because none of those shapes is a rectangle at
# any angle: a framed picture scores 0.96-1.00 at every tilt from 0 to 45
# degrees, while an ellipse -- which is never a picture -- scores 0.79, two
# objects spanning a box between them 0.80, and a figure lifted out of a
# painting 0.65. Same number as _MIN_ALPHA_RECT_FILL, and the same reasoning.
_MIN_TILT_FILL = float(os.getenv("ART_MIN_TILT_FILL", "0.9") or 0.9)

# The smallest share of the photo a border may enclose. Below this we have
# found something IN the picture, or a stray object, rather than the picture.
_MIN_AREA = float(os.getenv("ART_MIN_AREA", "0.12") or 0.12)

# ...and the largest. A box spanning this much of BOTH axes means no border was
# found on any side: the picture bleeds off the frame. The seller's rule for
# that case is to do nothing at all, so this returns None rather than a box
# equal to the whole photo — which would composite the picture onto white,
# re-encode it, and change nothing except its file size.
_BLEED_SPAN = float(os.getenv("ART_BLEED_SPAN", "0.97") or 0.97)

# Grow the box outward by this share of its own size before it is used. The
# content mask finds where the PICTURE starts, which on a framed piece is
# inside the moulding and on a print is inside the mount. Every pixel this
# adds is a pixel of the item the seller is selling; every pixel it fails to
# add is a slice off the edge of their frame.
_MARGIN = float(os.getenv("ART_MARGIN", "0.02") or 0.02)

# --- is it a rectangle, at whatever angle it was photographed from? ----------
#
# Shared by both halves of this module. The geometric scan below asks it of the
# content it found in the photo (see _is_turned_rectangle), and quad_from_alpha
# asks it of a remote engine's matte. It is the same question in both places
# and it is asked the same way, because the thing being separated is the same:
# a picture, which is a rectangle however it is held, from a shape that is not
# a rectangle at any angle at all.

# Angles tried when fitting that rectangle. A rectangle repeats every 90
# degrees, so the sweep never needs to go further.
_FIT_COARSE = int(os.getenv("ART_FIT_COARSE", "3") or 3)

# The value the flood below paints the OUTSIDE with while it works. Any level
# that is neither 0 nor 255, since what it runs over is a two-level mask.
_OUTSIDE = 128


def _solid(shape: Image.Image, box: Optional[Box] = None) -> Image.Image:
    """`shape` -- a two-level mask -- with the background it ENCLOSES filled in.

    Both halves of this module measure how much of its own box a shape fills,
    and a hole is the one thing that measurement cannot survive. A framed
    picture behind a pale mount fills 0.71 of its box; an ellipse, which is
    never a picture, fills 0.785. Without this the gate reads the picture as
    the worse shape of the two and keeps the photo as shot.

    The hole is not a fact about the shape. It is a fact about the MASK:
    whatever inside the picture happens to match the wall it hangs on drops
    out of it -- a white mount, a pale sky, bare canvas, the glare off
    glazing, or on a remote engine's matte a patch the model let go. None of
    it says anything about whether the OUTER EDGE is a rectangle, which is
    the only thing this module ever asks and the only thing it ever cuts to.

    THE SQUARE CASE, which is how this was reported: how many cells of the
    240px working grid a mount covers depends on the photo's own shape. The
    long side is normalised to 240, so a square photo's short side is 240
    where a 4:3 photo's is 180 -- the same piece, cropped square, arrives
    with its mount a third wider in cells. Past about eight cells _CLOSE can
    no longer bridge it, the mount stays a hole, and the fill drops from 0.90
    to 0.71. That is the whole of it: a picture that passed in landscape was
    refused for having been cropped square, and refused the same way on every
    photo in the set, because one crop shapes them all.

    Only background the shape fully encloses. Anything with a way out to the
    frame edge is left alone, which is what keeps the gap between two objects
    a gap -- two things spanning a box between them must never read as one
    picture.

    Filling can only ever ADD to a shape, and a hole is by definition inside
    the shape's bounding box, so this moves no border: the box is what it
    always was, and the only photo whose outcome changes is one that was
    being kept as shot.

    Flooded inside that bounding box rather than over the whole frame. The
    shape cannot reach outside its own box, so nothing out there can be
    enclosed by it, and a cell of background on the crop's rim is a cell with
    a way out -- which is the difference between 3ms and 80ms per photo.
    """
    box = box or shape.getbbox()
    if not box:
        return shape
    bw, bh = box[2] - box[0], box[3] - box[1]
    # One cell of background all the way around the crop, so a single seed in
    # its corner reaches every cell outside the shape -- including the ones
    # the shape's own edge is standing on.
    pad = Image.new("L", (bw + 2, bh + 2), 0)
    pad.paste(shape.crop(box), (1, 1))
    ImageDraw.floodfill(pad, (0, 0), _OUTSIDE)
    out = shape.copy()
    out.paste(pad.point(lambda v: 0 if v == _OUTSIDE else 255)
                 .crop((1, 1, bw + 1, bh + 1)), (box[0], box[1]))
    return out


def _fill_at(alpha: Image.Image, deg: float) -> float:
    """What share of its bounding box the matte fills once turned by `deg`."""
    turned = alpha.rotate(deg, resample=Image.BILINEAR, fillcolor=0)
    box = turned.getbbox()
    if not box:
        return 0.0
    bw, bh = box[2] - box[0], box[3] - box[1]
    if not bw or not bh:
        return 0.0
    kept = turned.crop(box).point(lambda a: 255 if a >= 128 else 0)
    return sum(kept.histogram()[128:]) / float(bw * bh)


def _best_angle(alpha: Image.Image) -> tuple[float, float]:
    """The angle at which the matte most looks like a rectangle, and how much
    it looks like one there. Coarse sweep, then one degree either side."""
    best, best_fill = 0.0, 0.0
    for deg in range(0, 90, _FIT_COARSE):
        fill = _fill_at(alpha, float(deg))
        if fill > best_fill:
            best, best_fill = float(deg), fill
    for step in (1.0, 0.5):
        for deg in (best - step, best + step):
            fill = _fill_at(alpha, deg)
            if fill > best_fill:
                best, best_fill = deg, fill
    return best, best_fill


def _padded(alpha: Image.Image) -> tuple[Image.Image, int, int]:
    """`alpha` centred on a square canvas big enough that no rotation can push
    a corner off it, with the offset it was placed at.

    Padding first is not tidiness. A shape clipped by the edge of its own
    canvas as it turns loses the corners that make it a rectangle, and scores
    BETTER for it -- so the fit would flatter exactly the shapes it exists to
    catch.
    """
    diag = int((alpha.width ** 2 + alpha.height ** 2) ** 0.5) + 4
    out = Image.new("L", (diag, diag), 0)
    ox, oy = (diag - alpha.width) // 2, (diag - alpha.height) // 2
    out.paste(alpha, (ox, oy))
    return out, ox, oy


def _is_turned_rectangle(region: Image.Image, upright: float) -> bool:
    """Whether `region` is a picture photographed at an angle, rather than a
    shape that is not a picture at all.

    Asked only of a region that has already FAILED the upright test, and it is
    the difference between "this seller used a tripod" and "this is not a
    picture". See _MIN_TILT_FILL: a hand-held photo is a rectangle turned a
    few degrees, and a turned rectangle fills its axis-aligned box poorly
    however perfect a rectangle it is.

    The direction of error here is the module's usual one. A shape wrongly
    called a turned picture is cut to the box around itself, which keeps a
    margin of background on an item that is still whole; a real picture
    wrongly refused is a seller's whole set of photos silently left as shot,
    which is the report this was written for.
    """
    deg, fill = _best_angle(_padded(region)[0])
    if fill < _MIN_TILT_FILL:
        log.info("art border: shape fills %.2f of its box upright and %.2f of "
                 "its best rectangle (at %.1f°) — not a picture at any angle; "
                 "keeping the photo as shot", upright, fill, deg)
        return False
    log.info("art border: the picture was shot hand-held, about %.1f° off "
             "square — it fills %.2f of its own rectangle there, against "
             "%.2f of the upright box", deg, fill, upright)
    return True


# --- and the scan in from the edge of the photo ------------------------------
#
# The content mask finds the IMAGE. On a framed piece that is inside the
# moulding, and on a print it is inside the mount — so the box it returns can
# sit well within the thing being sold, and cutting to it crops the frame or
# the mount off. A white-mounted print on a white table is the worst of it:
# eighty pixels of blank paper between the printed area and the sheet's own
# edge, differing from the table by two or three levels, with no texture on
# it. Growing the content box outward cannot cross that gap — there is nothing
# in it to grow along.
#
# Scanning the other way does. The surround is, by assumption, whatever is at
# the EDGE of the photo, so each side is walked from the photo's own edge
# INWARD to the first line that is not surround. That line is the outside of
# the thing, whatever is inside it: the frame's moulding, the sheet's shadow,
# the canvas's stretcher. It crosses blank mount without noticing it, because
# it never has to stand on it.
#
# The thresholds are much lower than the ones that found the picture, because
# this is a different question: not "is this the picture" but "is this still
# the wall", and the answer must be a confident yes to keep scanning.
#
# Stopping EARLY is safe in every direction it can go: too early on one side
# keeps a strip of background, which is a slightly worse cutout of an intact
# item, and too early on all four reads as bleed and keeps the photo as shot.
# Stopping late is the only outcome that cuts the artwork, so every threshold
# here errs toward stopping early, and the result is unioned with the content
# box so a scan that finds nothing can never shrink it.
#
# WHAT "STILL THE WALL" LOOKS LIKE IS A FACT ABOUT THE PHOTO, NOT A NUMBER.
#
# The report: framed prints shot on a rumpled sheet, and the background removed
# from almost none of them. The content mask had the picture exactly right in
# every one of them -- a dark frame on pale cloth, filling 0.89 of its own box
# and 0.42 of the photo, a textbook find. What refused them was this scan. A
# sheet has creases in it and a crease is texture, so the FIRST line in from
# the edge already read as "not the wall". Every side stopped where it started,
# the box grew to the whole photo, the bleed test saw a picture running off all
# four edges, and the photo was kept as shot.
#
# The same arithmetic refuses a plain seamless sweep, which is the commonest
# backdrop there is. The surround is one median colour and a sweep is LIT: 22
# levels brighter at the top of the frame than at the bottom is an ordinary
# lamp, against an _EDGE_DIST of 6. Measured on a clean white sweep with no
# picture in the band at all, every row scores a hit share of 1.00. Neither
# case is a photo with anything wrong with it, and neither was visible: the
# only line logged was the bleed refusal, which named the one thing that had
# not happened.
#
# That is also why the answer was MOST of them rather than the odd photo. The
# trigger is the backdrop, and a seller shoots their whole set on one backdrop.
#
# So the two thresholds are FLOORS, and the bar is whatever the surround in
# this photo actually does: the top of its own spread across the band, or the
# floor, whichever is higher. On a flat sweep the band's spread is a level or
# two, the floor wins, and nothing about a photo that already worked changes.
# On a lit sweep, a crumpled sheet or a wood table the bar rises above the
# gradient and the creases, and the scan goes back to looking for something
# the backdrop is not already doing by itself.
_EDGE_DIST = int(os.getenv("ART_EDGE_DIST", "6") or 6)
_EDGE_TEXTURE = int(os.getenv("ART_EDGE_TEXTURE", "6") or 6)
_EDGE_SHARE = float(os.getenv("ART_EDGE_SHARE", "0.10") or 0.10)
# How much of the surround's own spread is left BELOW the bar. The band is
# mostly surround by assumption, so the bar sits near the top of what it does:
# high enough that a crease or a gradient is not an edge, low enough to still
# be a bar. A picture poking into the band can only push it UP, which stops the
# scan sooner and leaves the content box as it was -- the safe way to be wrong.
_EDGE_QUANTILE = float(os.getenv("ART_EDGE_QUANTILE", "0.99") or 0.99)
# How many consecutive non-surround lines end the scan. One line is a scratch
# on the table or a row of sensor noise; two in a row is an edge.
_EDGE_RUN = int(os.getenv("ART_EDGE_RUN", "2") or 2)

Box = tuple[int, int, int, int]
# Four corners, clockwise from the top-left of the picture as it lies in the
# photo. A picture shot square-on is a Box; one shot hand-held is a Quad.
Quad = tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]


def _band(size: tuple[int, int]) -> tuple[Image.Image, int]:
    """The ring around the edge of the frame that is ASSUMED to be surround,
    as a mask, and how many lines deep it is.

    Everything this module knows about the background comes from here: its
    colour (_surround) and how far that colour wanders (_scan_inward). The two
    have to read the same band or the second is calibrating against something
    the first never sampled.
    """
    w, h = size
    band = max(1, round(min(w, h) * _SURROUND_BAND))
    ring = Image.new("L", (w, h), 255)
    ring.paste(0, (band, band, max(band, w - band), max(band, h - band)))
    return ring, band


def _surround(small: Image.Image) -> tuple[int, int, int]:
    """The colour of whatever the picture is lying on or hanging against,
    sampled from a band around the edge of the frame.

    Median rather than mean: a mean is dragged by the corner of the picture
    poking into the band, and a median is not until half the band is picture.
    """
    med = ImageStat.Stat(small, _band(small.size)[0]).median
    return (int(med[0]), int(med[1]), int(med[2]))


def _away_from(small: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """How far each pixel sits from `colour` — Chebyshev over the channels, so
    a picture that differs in only one channel is still a picture."""
    far = ImageChops.difference(small, Image.new("RGB", small.size, colour))
    r, g, b = far.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def _texture(small: Image.Image) -> Image.Image:
    """How busy each pixel's neighbourhood is — the morphological gradient, the
    local range over a 3x3 window. Cheap, and unlike an edge kernel it responds
    to texture as well as to lines."""
    grey = small.convert("L")
    return ImageChops.difference(grey.filter(ImageFilter.MaxFilter(3)),
                                 grey.filter(ImageFilter.MinFilter(3)))


def _quantile(hist: list[int], q: float) -> int:
    """The level at or below which `q` of the samples in `hist` fall."""
    total = sum(hist)
    if not total:
        return 0
    want, run = total * q, 0
    for level, n in enumerate(hist):
        run += n
        if run >= want:
            return level
    return len(hist) - 1


def _content(small: Image.Image,
             bars: Optional[tuple[int, int]] = None) -> Image.Image:
    """A 1-bit mask of everything that is not the surround.

    Two signals, ORed, because each one alone misses a picture the other
    catches: colour (a bright print on a white wall) and local contrast (a
    pale drawing on paper the same white as the table it is on).

    `bars` overrides the two thresholds for a photo whose background does not
    hold still at them — see _second_look. The default is the pair this module
    was fitted with, and it is what every photo is asked first.
    """
    dist, busy_at = bars or (_SURROUND_DIST, _BUSY_LEVEL)
    chroma = _away_from(small, _surround(small))
    busy = _texture(small)

    mask = ImageChops.lighter(
        chroma.point(lambda v: 255 if v >= dist else 0),
        busy.point(lambda v: 255 if v >= busy_at else 0))
    if _CLOSE > 0:
        k = _CLOSE * 2 + 1
        # Close: join the parts of one picture, then take back the growth.
        # MaxFilter/MinFilter are Pillow's dilate/erode for a binary mask.
        mask = mask.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    return mask


def _largest_region(mask: Image.Image) -> tuple[int, Optional[Box],
                                                Optional[Image.Image]]:
    """(cells, bounding box, the region on its own) for the largest
    4-connected region of `mask`.

    The region comes back as its own 1-bit image because the box alone cannot
    answer the question that matters: a picture photographed hand-held is a
    rectangle at an angle, and telling one from a shape that is not a
    rectangle at all means measuring the SHAPE, not its box. See
    _is_turned_rectangle. Everything else in the mask is dropped, so a stray
    blob beside the picture cannot join in.

    An explicit stack, not recursion: the healthy case here is one region
    covering most of the mask, which is exactly the shape that blows Python's
    call stack. The same reason services/images._kept_shape uses one.
    """
    w, h = mask.size
    px = mask.load()
    seen = bytearray(w * h)
    best: tuple[int, Optional[Box]] = (0, None)
    best_cells: list[tuple[int, int]] = []
    for sy in range(h):
        for sx in range(w):
            if seen[sy * w + sx] or not px[sx, sy]:
                continue
            lo_x = hi_x = sx
            lo_y = hi_y = sy
            found = [(sx, sy)]
            stack = [(sx, sy)]
            seen[sy * w + sx] = 1
            while stack:
                x, y = stack.pop()
                lo_x, hi_x = min(lo_x, x), max(hi_x, x)
                lo_y, hi_y = min(lo_y, y), max(hi_y, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] \
                            and px[nx, ny]:
                        seen[ny * w + nx] = 1
                        found.append((nx, ny))
                        stack.append((nx, ny))
            if len(found) > best[0]:
                best = (len(found), (lo_x, lo_y, hi_x + 1, hi_y + 1))
                best_cells = found
    if best[1] is None:
        return 0, None, None
    region = Image.new("L", (w, h), 0)
    rp = region.load()
    for x, y in best_cells:
        rp[x, y] = 255
    return best[0], best[1], region


def _scan_inward(small: Image.Image, box: Box) -> Box:
    """`box` widened to the first non-surround line found scanning IN from
    each edge of the photo.

    Answers "is this still the wall" rather than "is this the picture", so it
    reads two weak signals and keeps scanning while BOTH say the line is
    surround: no colour step away from it, and no texture on it. A wall, a
    table top and a sheet of backing card have neither; a frame's moulding, a
    sheet's drop shadow and a canvas's edge all have one or the other.

    Weak against the SURROUND IN THIS PHOTO, not against a fixed number. A
    crumpled sheet and a lit sweep both clear a fixed 6 with nothing in front
    of the camera at all, which refused the seller's whole set — see the note
    on _EDGE_DIST. The floors still apply; the bar is the higher of the floor
    and the top of what the band does on its own.

    Unioned with `box`, never replacing it: a scan that finds nothing at all
    leaves the content box exactly as it was, and can only ever make the
    result larger. See the note on _EDGE_DIST for why every error this can
    make except one is harmless.
    """
    w, h = small.size
    surround = _surround(small)
    away, texture = _away_from(small, surround), _texture(small)
    ap, tx = away.load(), texture.load()
    ring, band = _band(small.size)
    wanders = _quantile(away.histogram(ring), _EDGE_QUANTILE)
    carries = _quantile(texture.histogram(ring), _EDGE_QUANTILE)
    dist_bar, tex_bar = (max(_EDGE_DIST, wanders + 1),
                         max(_EDGE_TEXTURE, carries + 1))
    if dist_bar > _EDGE_DIST or tex_bar > _EDGE_TEXTURE:
        log.info("art border: the background of this photo is not flat — it "
                 "wanders %d levels off its own colour and carries %d of "
                 "texture, so the edge scan looks for %d/%d rather than %d/%d",
                 wanders, carries, dist_bar, tex_bar,
                 _EDGE_DIST, _EDGE_TEXTURE)
    inside_band = []

    def _not_surround(pts) -> bool:
        pts = list(pts)
        if not pts:
            return False
        hits = sum(1 for x, y in pts
                   if ap[x, y] >= dist_bar or tx[x, y] >= tex_bar)
        return hits / len(pts) >= _EDGE_SHARE

    def _first(lines, step: int, span: int) -> Optional[int]:
        """The OUTERMOST of _EDGE_RUN consecutive non-surround lines, or None
        when this side has nothing to say.

        `step` is +1 scanning from the top or the left and -1 from the bottom
        or the right, which is what turns the index the run ENDED on back into
        the one it started on. A reverse scan used to subtract it as though it
        counted upward, landing two lines INSIDE the edge it had just found —
        a couple of cells off the right and the bottom of every piece this
        scan located, invisible only because _MARGIN grew them back.
        """
        run = 0
        for i, pts in lines:
            if _not_surround(pts):
                run += 1
                if run >= _EDGE_RUN:
                    at = i - step * (run - 1)
                    # Inside the band this scan ASSUMED was surround, which is
                    # where its own colour and its own bar were measured. A
                    # side that answers "the thing starts here" about the lines
                    # it just called wall has contradicted its premise, and it
                    # has crossed no surround at all, so it has learned nothing
                    # about where a mount ends. The content box stands.
                    if (at if step > 0 else span - 1 - at) < band:
                        inside_band.append(True)
                        return None
                    return at
            else:
                run = 0
        return None

    # Each side scans across the FULL span of the other axis, not the content
    # box's span: the frame's moulding reaches past the printed area it
    # surrounds, and a scan confined to that area's rows would step over the
    # corners of its own frame.
    left = _first(((x, ((x, y) for y in range(h))) for x in range(w)), 1, w)
    right = _first(((x, ((x, y) for y in range(h)))
                    for x in range(w - 1, -1, -1)), -1, w)
    top = _first(((y, ((x, y) for x in range(w))) for y in range(h)), 1, h)
    bottom = _first(((y, ((x, y) for x in range(w)))
                     for y in range(h - 1, -1, -1)), -1, h)
    if inside_band:
        log.info("art border: %d of the four edge scans stopped inside the "
                 "band they had assumed was background — those sides keep the "
                 "border the content mask found", len(inside_band))
    return (min(box[0], left if left is not None else box[0]),
            min(box[1], top if top is not None else box[1]),
            max(box[2], (right + 1) if right is not None else box[2]),
            max(box[3], (bottom + 1) if bottom is not None else box[3]))


# The share of the assumed-background band a raised content mask may still
# call content before the second look is dropped. A picture nicking the band
# with a corner is ordinary; a mask that still finds content all round the rim
# after the bar was raised above the background's own spread is not looking at
# a background at all — it is the picture, running off the edge of the photo,
# and the seller's rule for that case is to do nothing.
_BAND_QUIET = float(os.getenv("ART_BAND_QUIET", "0.15") or 0.15)


def _second_look(small: Image.Image) -> Optional[tuple[int, int]]:
    """Content thresholds raised to clear the background's OWN variation, or
    None when there is nothing to gain or nothing to trust.

    _SURROUND_DIST and _BUSY_LEVEL are fixed numbers measured against one
    median colour, which is a fair description of a wall and a poor one of a
    lit sweep or a crumpled sheet. When the background wanders past them the
    mask turns the whole photo on, the largest region is the photo, and the
    picture in the middle of it is never found — see the note on _EDGE_DIST,
    which is the same failure one step down.

    The bar comes from the band around the edge of the frame — the same band
    the surround colour comes from, read at the same quantile the edge scan
    reads it at and for the same reason — and it can only ever RISE: the
    floors are what this module was fitted with, and no photo is asked less.

    Two things make it a second look rather than the first. It is only ever
    reached by a photo that was already going to be kept as shot, so nothing
    that works today can change. And it is dropped unless the raised mask
    leaves the band QUIET — a mask that still finds content all the way round
    the rim was never looking at a background, and the picture that fills the
    frame is the bleed case, which stays refused.
    """
    ring = _band(small.size)[0]
    bars = (max(_SURROUND_DIST,
                _quantile(_away_from(small, _surround(small)).histogram(ring),
                          _EDGE_QUANTILE) + 1),
            max(_BUSY_LEVEL,
                _quantile(_texture(small).histogram(ring), _EDGE_QUANTILE) + 1))
    if bars == (_SURROUND_DIST, _BUSY_LEVEL):
        return None
    band_hist = _content(small, bars).histogram(ring)
    still_on = band_hist[255] / max(1, sum(band_hist))
    if still_on > _BAND_QUIET:
        log.info("art border: raising the bar to %d/%d still leaves %.2f of "
                 "the rim reading as content — that is the picture, not a "
                 "background; keeping the photo as shot", *bars, still_on)
        return None
    log.info("art border: no border at %d/%d, and this photo's background is "
             "not flat — looking again at %d/%d, which is where its own rim "
             "settles", _SURROUND_DIST, _BUSY_LEVEL, *bars)
    return bars


def border(rgb: Image.Image) -> Optional[Box]:
    """The outer border of the picture in `rgb`, or None when there isn't one.

    Returns (left, top, right, bottom) in `rgb`'s own pixel coordinates, grown
    a little outward so a frame's moulding is never shaved.

    None is the answer to every doubt, and it means "keep this photo exactly
    as shot": no clear border on all sides, a border enclosing too little of
    the frame to be the picture, a shape that is not rectangular enough to BE
    a picture, or a picture that bleeds off the edge. The caller must treat
    None as "do nothing" rather than as "fall back to the model" — falling
    back to the model is the bug this module exists for.

    Asked twice at most. The first pass is the one this module was fitted
    with, and a photo that answers it is finished there — so no photo whose
    border is found today can change. Only a REFUSAL goes round again, with
    the background measured off this photo's own rim instead of assumed flat,
    and a refusal is the one outcome a second look cannot make worse: the
    alternative to whatever it finds is the photo, kept as shot.
    """
    w, h = rgb.size
    if w < 8 or h < 8:
        return None
    scale = _SIDE / max(w, h)
    small = (rgb.resize((max(8, round(w * scale)), max(8, round(h * scale))),
                        Image.BOX) if scale < 1 else rgb).convert("RGB")

    box = _border_in(small, None)
    if box is None:
        bars = _second_look(small)
        if bars is not None:
            box = _border_in(small, bars)
    if box is None:
        return None

    # Back to full resolution, then outward. Rounded out on every side (floor
    # the near edges, ceil the far ones) so the scaling itself never shaves a
    # row off the artwork.
    k = 1 / scale if scale < 1 else 1.0
    left, top = box[0] * k, box[1] * k
    right, bottom = box[2] * k, box[3] * k
    mx, my = (right - left) * _MARGIN, (bottom - top) * _MARGIN
    return (max(0, int(left - mx)), max(0, int(top - my)),
            min(w, int(right + mx + 0.999)), min(h, int(bottom + my + 0.999)))


def _border_in(small: Image.Image,
               bars: Optional[tuple[int, int]]) -> Optional[Box]:
    """The picture's outer border in `small`'s own coordinates, or None.

    Every refusal in this module is here, and each one means the same thing to
    the caller: keep the photo as shot.
    """
    sw, sh = small.size
    cells, box, region = _largest_region(_content(small, bars))
    if not box or region is None:
        log.info("art border: no content found — keeping the photo as shot")
        return None
    # A white mount, a pale sky, bare canvas: none of it differs from the wall,
    # so none of it is in the mask, and the picture reaches the rectangle test
    # below as a ring with its middle missing. The middle is not the question.
    # See _solid.
    region = _solid(region, box)
    cells = sum(region.histogram()[128:])
    bw, bh = box[2] - box[0], box[3] - box[1]
    fill = cells / (bw * bh) if bw and bh else 0.0
    area = (bw * bh) / (sw * sh)

    # A picture fills its own box -- and a picture that was not held square to
    # the camera fills a TURNED one, which is why failing the first test is a
    # question rather than an answer. Only a shape that is not a rectangle at
    # any angle is refused here: two objects spanning a box between them, or a
    # picture the content mask broke into pieces.
    if fill < _MIN_RECT_FILL and not _is_turned_rectangle(region, fill):
        return None
    if area < _MIN_AREA:
        log.info("art border: box covers %.2f of the frame, too small to be "
                 "the picture — keeping the photo as shot", area)
        return None
    # Out to the real edge of the thing, THEN the bleed test — the scan is
    # what turns "the printed area" into "the sheet it is printed on", and a
    # picture whose sheet reaches every edge of the photo is the bleed case
    # however modest the printed area inside it looked.
    box = _scan_inward(small, box)
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw / sw >= _BLEED_SPAN and bh / sh >= _BLEED_SPAN:
        log.info("art border: the picture runs off every edge — no border to "
                 "cut to, keeping the photo as shot")
        return None
    return box


# How nearly a remote engine's matte must fill the best rectangle that can be
# drawn around it before that shape is allowed to be a picture's border.
#
# Measured at the BEST ANGLE, not against the axis-aligned box, and that is the
# whole point. A print photographed hand-held over a floor is a rectangle that
# happens to be rotated a few degrees, and a rotated rectangle fills its
# axis-aligned box poorly: 0.94 at 2 degrees, 0.85 at 5, 0.74 at 10. Judging it
# that way refuses the exact photo this exists to rescue -- 39 in one seller's
# batch -- while an ellipse, which is never a picture, scores 0.785 at every
# angle. Asking "is this a rectangle at SOME angle" separates those two; asking
# "is it an upright rectangle" only separates tripod shots from handheld ones.
# The geometric scan asks the same question of its own content for the same
# reason and at the same number -- see _MIN_TILT_FILL.
_MIN_ALPHA_RECT_FILL = float(os.getenv("ART_ALPHA_RECT_FILL", "0.9") or 0.9)


def quad_from_alpha(size: tuple[int, int],
                    alpha: Image.Image) -> Optional[Quad]:
    """The picture's outer edge as a remote engine's matte found it, as four
    corners -- or None.

    The second opinion for the case this module otherwise answers with "do
    nothing": a print whose border `border()` could not scan at all -- one
    lying on a surface close to its own colour, or with too little between it
    and the floor for the scan to stop on. Being hand-held is no longer one of
    those cases on its own: the scan fits its own content at an angle now (see
    _MIN_TILT_FILL), so a tilted picture with a findable edge never reaches
    here. A segmentation model finds a sheet of paper on a wooden floor
    easily. What it cannot be trusted with is what is INSIDE that sheet, and
    nothing here asks it -- only the outer shape is taken, and the caller
    fills it solid through quad().

    Four corners rather than a box because the photo is the angled one: the
    axis-aligned box around a print lying at 8 degrees includes a triangle of
    floor at each corner, and a listing photo with four wedges of somebody's
    floorboards in it is not a cutout. The corners follow the print.

    None on every doubt, with the same meaning as everywhere else in this
    module: keep the photo as shot. Never "use the matte itself".
    """
    w, h = size
    if w < 8 or h < 8:
        return None
    if alpha.getbbox() is None:
        return None
    # Everything below is a question about a shape a few hundred pixels
    # across, so it is answered on a thumbnail and scaled back up -- and the
    # sweep rotates the image ~35 times, which is why that matters.
    scale = _SIDE / max(alpha.size)
    small = (alpha.resize((max(8, round(alpha.width * scale)),
                           max(8, round(alpha.height * scale))), Image.BOX)
             if scale < 1 else alpha)
    # The same hole the geometric scan meets, arriving the other way round: an
    # engine that let go of a pale sky, or of the glare off the glazing, hands
    # back a matte with a gap in the middle of the picture, and a gap in the
    # middle says nothing about the outer edge. Lightened rather than
    # replaced, so a soft rim stays soft and the shape's own bounding box --
    # which is what the corners below are measured from -- is untouched.
    small = ImageChops.lighter(
        small, _solid(small.point(lambda a: 255 if a >= 128 else 0)))
    pad, ox, oy = _padded(small)
    diag = pad.width

    deg, fill = _best_angle(pad)
    if fill < _MIN_ALPHA_RECT_FILL:
        log.info("art border: the matte fills %.2f of its best rectangle — "
                 "that is a subject inside a picture, not the picture; "
                 "keeping the photo as shot", fill)
        return None

    box = pad.rotate(deg, resample=Image.BILINEAR, fillcolor=0).getbbox()
    if not box:
        return None
    bw, bh = box[2] - box[0], box[3] - box[1]
    area = (bw * bh) / float(small.width * small.height)
    if area < _MIN_AREA:
        log.info("art border: the matte covers %.2f of the frame, too small "
                 "to be the picture — keeping the photo as shot", area)
        return None

    # Out by the usual margin, then back: the corners are found in the TURNED
    # image, so each is turned back by the same angle about the same centre,
    # un-padded and un-scaled. PIL rotates counter-clockwise about the centre,
    # so undoing it is the same rotation with the sign flipped.
    mx, my = bw * _MARGIN, bh * _MARGIN
    corners = ((box[0] - mx, box[1] - my), (box[2] + mx, box[1] - my),
               (box[2] + mx, box[3] + my), (box[0] - mx, box[3] + my))
    centre = diag / 2.0
    rad = math.radians(deg)
    cos, sin = math.cos(rad), math.sin(rad)
    k = 1 / scale if scale < 1 else 1.0
    out = []
    for px, py in corners:
        # PIL's rotate(deg) sends a source point (x, y) to
        #     (u·cos + v·sin, −u·sin + v·cos)   where u = x−c, v = y−c
        # and these corners were measured in that rotated image, so getting
        # back to the photo means applying that matrix's INVERSE -- which,
        # being a rotation, is just its transpose. Turning the same way twice
        # instead is the bug this replaced: it put the quad somewhere else
        # entirely, clipping the print at one corner and taking floor at the
        # opposite one, and only a test that measured overlap could see it.
        u, v = px - centre, py - centre
        sx = centre + u * cos - v * sin
        sy = centre + u * sin + v * cos
        out.append((round((sx - ox) * k), round((sy - oy) * k)))
    log.info("art border: the matte is a rectangle at %.1f° filling %.2f of "
             "it", deg, fill)
    return tuple(out)


def quad(size: tuple[int, int], corners: Quad) -> Image.Image:
    """A matte fully opaque inside the four corners and fully clear outside.

    mask() for a picture that is not square-on. Same guarantee, same reason:
    what comes back is a SOLID convex shape, so compositing through it cannot
    remove anything inside the picture's edge. There is still no threshold in
    here and no shape to get wrong -- the corners came in, the fill goes out.
    """
    out = Image.new("L", size, 0)
    ImageDraw.Draw(out).polygon([(int(x), int(y)) for x, y in corners],
                                fill=255)
    return out


def mask(size: tuple[int, int], box: Box) -> Image.Image:
    """A matte that is fully opaque inside `box` and fully clear outside it.

    The whole point of the module in one function: what comes back is a FILLED
    RECTANGLE, so compositing through it cannot remove anything inside the
    picture's border. There is no threshold to tune and no shape to get wrong.
    """
    out = Image.new("L", size, 0)
    out.paste(255, (max(0, box[0]), max(0, box[1]),
                    min(size[0], box[2]), min(size[1], box[3])))
    return out

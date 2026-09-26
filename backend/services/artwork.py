"""Where a picture ENDS — the outer edge of a painting, print or poster.

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

    A picture is a FOUR-CORNERED SHAPE — a rectangle, however it was held
    and from wherever it was shot. Find its outer edge and keep everything
    inside it, whole. Never ask what is interesting within it.

That makes the seller's requirement structural instead of statistical.
`outline()` returns corners; `outline_matte()` fills them solid. There is no
code path here that can remove a pixel from inside the outline, because
nothing here ever decides what is inside it.

AND WHEN THE OUTLINE IS NOT THERE, NOTHING HAPPENS. A picture shot close enough
that it runs off the edge of the frame, a print on paper the same colour as
the table, a painting whose own edge melts into the wall — none of those have
a findable outline, and a guess at one crops a painting. So every uncertainty
returns None and the photo is kept exactly as shot. That direction is nearly
free: the seller loses an opt-in background removal on one photo. The other
direction destroys the item the listing is for.

Pillow only, no numpy and no model — the same constraint the rest of the photo
pass works under, which is what lets CI prove this on Pillow alone.
"""
from __future__ import annotations

import math
import os
import re
from typing import NamedTuple, Optional

from PIL import (Image, ImageChops, ImageDraw, ImageFilter)

from ..config import log

# The working size. Everything below is a question about an edge several
# hundred pixels long, so a 240px thumbnail answers it as well as a 4000px
# photo and answers it in Python-loop time. Coordinates are scaled back up
# before they are returned.
_SIDE = int(os.getenv("ART_BORDER_SIDE", "240") or 240)

# The smallest share of the photo a picture may cover — asked of an outline and
# of a segmentation matte alike. Below this the shape is something IN the
# picture, or a stray object, rather than the picture.
_MIN_AREA = float(os.getenv("ART_MIN_AREA", "0.12") or 0.12)

# ...and the largest. A shape covering this much of the photo means there is
# no background in it: the picture bleeds off the frame. The seller's rule for
# that case is to do nothing at all, so the answer is None rather than an
# outline equal to the whole photo — which would composite the picture onto
# white, re-encode it, and change nothing except its file size.
_BLEED_SPAN = float(os.getenv("ART_BLEED_SPAN", "0.97") or 0.97)

# Grow a matte's rectangle outward by this share of its own size before it is
# used (quad_from_alpha). A matte's edge is soft and sits a little inside the
# thing; every pixel this adds is a pixel of the item the seller is selling,
# and every pixel it fails to add is a slice off the edge of their frame.
_MARGIN = float(os.getenv("ART_MARGIN", "0.02") or 0.02)

# --- is a matte a rectangle, at whatever angle it was photographed from? -----
#
# quad_from_alpha asks it of a segmentation model's matte: what is being
# separated is a picture, which is a rectangle however it is held, from a shape
# that is not a rectangle at any angle at all.

# Angles tried when fitting that rectangle. A rectangle repeats every 90
# degrees, so the sweep never needs to go further.
_FIT_COARSE = int(os.getenv("ART_FIT_COARSE", "3") or 3)

# The value the flood below paints the OUTSIDE with while it works. Any level
# that is neither 0 nor 255, since what it runs over is a two-level mask.
_OUTSIDE = 128

Box = tuple[int, int, int, int]
# Four corners, clockwise from the top-left of the picture as it lies in the
# photo.
Quad = tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]


def _solid(shape: Image.Image, box: Optional[Box] = None) -> Image.Image:
    """`shape` -- a two-level mask -- with the background it ENCLOSES filled in.

    quad_from_alpha measures how much of its best rectangle a matte fills,
    and a hole is the one thing that measurement cannot survive. A framed
    picture whose model let go of its pale mount fills 0.71 of its box; an
    ellipse, which is never a picture, fills 0.785. Without this the gate
    reads the picture as the worse shape of the two and keeps the photo as
    shot.

    The hole is not a fact about the shape. It is a fact about the MASK:
    whatever inside the picture the model was unsure of drops out of it -- a
    white mount, a pale sky, bare canvas, the glare off glazing. None of it
    says anything about whether the OUTER EDGE is a rectangle, which is the
    only thing this module ever asks and the only thing it ever cuts to.

    Only background the shape fully encloses. Anything with a way out to the
    frame edge is left alone, which is what keeps the gap between two objects
    a gap -- two things spanning a box between them must never read as one
    picture.

    Filling can only ever ADD to a shape, and a hole is by definition inside
    the shape's bounding box, so this moves no edge: the box is what it
    always was.

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


# How nearly a segmentation model's matte must fill the best rectangle that can
# be drawn around it before that shape is allowed to be a picture's border.
#
# Measured at the BEST ANGLE, not against the axis-aligned box, and that is the
# whole point. A print photographed hand-held over a floor is a rectangle that
# happens to be rotated a few degrees, and a rotated rectangle fills its
# axis-aligned box poorly: 0.94 at 2 degrees, 0.85 at 5, 0.74 at 10. Judging it
# that way refuses the exact photo this exists to rescue -- 39 in one seller's
# batch -- while an ellipse, which is never a picture, scores 0.785 at every
# angle. Asking "is this a rectangle at SOME angle" separates those two; asking
# "is it an upright rectangle" only separates tripod shots from handheld ones.
_MIN_ALPHA_RECT_FILL = float(os.getenv("ART_ALPHA_RECT_FILL", "0.9") or 0.9)


def quad_from_alpha(size: tuple[int, int],
                    alpha: Image.Image) -> Optional[Quad]:
    """The picture's outer edge as a segmentation model's matte found it, as
    four corners -- or None.

    The second opinion for the case this module otherwise answers with "do
    nothing": a picture whose outline `outline()` could not find at all --
    one lying on a surface close to its own colour, so there is no edge
    between them for the flood to stop at. A segmentation model finds a sheet
    of paper on a wooden floor easily. What it cannot be trusted with is what
    is INSIDE that sheet, and nothing here asks it -- only the outer shape is
    taken, and the caller fills it solid through quad().

    Which model drew that matte is the caller's business and makes no
    difference to anything here. A paid engine was the only one allowed to
    answer for a while; the local one may now too (images._border_alpha), and
    the reason the answer is safe is the same for both -- it is checked for
    being a rectangle, and only its outer shape is used.

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
    # An engine that let go of a pale sky, or of the glare off the glazing,
    # hands back a matte with a gap in the middle of the picture, and a gap in
    # the middle says nothing about the outer edge. Lightened rather than
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
    # ...and the bleed case, which outline() refuses and this once did not. A
    # matte covering the whole frame says there is no background in this
    # photo, so there is no border in it either: cutting to it composites the
    # picture onto white, re-encodes it, and changes nothing except the file
    # size -- while telling the seller their background was removed and
    # charging them for it. Put to the AREA, not the span: the shape may be
    # turned, and a picture at 12 degrees spans the whole photo while leaving
    # a wedge of floor in each corner of it, which is a real cut.
    if area >= _BLEED_SPAN:
        log.info("art border: the matte covers %.2f of the frame — nothing in "
                 "this photo is background, so there is no border to cut to; "
                 "keeping it as shot", area)
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


# --- the outline, flooded in from the edge of the photo ----------------------
#
# The report this replaced: "the image remover still fails nearly every time
# for art pieces despite the item being fully in frame". It was right about
# nearly every time. Run over 300 photos made the way sellers make them — a
# framed piece on a painted wall under one lamp, a canvas on a wood floor, a
# print on a rumpled bed, a frame leaning against the skirting board, every
# one of them whole in the frame — the scan that stood here gave a usable
# cutout for 30, kept 143 as shot, and cut into five. With the model-assisted
# second look (quad_from_alpha) behind it, 11 photos in 40 came out right.
# This finds 270 of the 300, keeps 6 as shot, and cuts into none.
#
# The scan's premise was the trouble: that the background is ONE COLOUR, taken
# as the median of a band round the edge of the frame, and that anything far
# enough from that colour is the picture. A room is not one colour. A wall is
# brighter near the lamp than across the room, a phone darkens its own corners,
# a frame throws a shadow, a floor has planks, and a picture leaning on the
# floor has wall above it and floor below. Each of those put wall into the
# "picture", joined it to the frame, and turned a rectangle into a shape that
# was not one — and the photo was kept as shot. Patches followed (a second look
# with raised bars, a rotated fit, hole filling) and each fixed the one photo it
# was written for, because each was still measuring distance from one colour.
#
# So the question is asked the other way round. Not "how far is this pixel
# from the background colour" but "how strong an edge must be crossed to get
# to this pixel from outside the photo". A wall that fades from 200 to 150
# across the frame has no edge anywhere in it, so all of it is reached for
# free; the frame's outer edge is a real step, so everything inside it costs
# at least that much to reach. That is _reach, and it holds for a lit wall, a
# vignette, a soft shadow, a floor meeting a wall, and planks alike, because
# none of them have to be told apart from the picture by colour: they are all
# on the near side of the picture's own edge.
#
# How strong an edge counts is not guessed either. The thresholds are walked
# upward (_LADDER) and the first one at which a clean four-cornered shape comes
# free of everything around it is the answer. Lowest first is the point: at a
# low bar the frame's outer edge still stands, so the first shape found is the
# OUTER one, the frame rather than the mount inside it.
#
# What makes a shape a picture, and what keeps a subject cut out of a painting
# from passing for one:
#
#   * four corners that fit it closely both ways — the shape fills the
#     four-cornered outline and the outline covers the shape (_MIN_FIT). The
#     best four corners of a circle cover 0.64 of it, of a head-and-shoulders
#     far less; a framed picture is well above 0.95 both ways.
#   * corners a photograph of a rectangle can have. A hand-held shot tilts it
#     and a shot from above or below tapers it, but no photograph of a
#     rectangle makes one side much shorter than the side opposite it
#     (_MIN_SIDE_RATIO), or a corner sharper than _MAX_CORNER allows.
#   * not the last surviving piece of something larger (_DISSOLVE). As the bar
#     rises, the weaker edges of an object give way first — a pale face goes
#     before dark shoulders — and what is left can be a perfectly good
#     trapezium. So the shape is compared with what it belonged to when it
#     first came free of the photo's edge, and if a solid piece of that has
#     since dissolved, this is a part and not the piece.
#   * nothing hugging it at a constant distance on three sides (_outer_layer).
#     That is a frame whose outer edge was faint against its wall — the flood
#     got into the moulding and the first clean shape was the mount inside it.
#     The outline is grown out to that edge rather than cut to the mount.
#
# And two things are not refusals any more. A second picture beside the first
# is kept too (_COMPANION), rather than cut away. And an outline that runs a
# little past the edge of the photo is clipped to it: the matte only ever keeps
# what is inside the outline, so clipping it keeps everything of the piece that
# is in the photo. Only an outline that is the whole photo is refused, because
# there is nothing to take away.

# The bars tried, lowest first. An edge's strength is the local range of the
# smoothed photo across it, 0-255, so these run from sensor noise to a black
# frame on a white wall.
_LADDER = (3, 4, 5, 6, 8, 10, 12, 15, 18, 22, 26, 31, 37, 44, 52, 62, 74, 88,
           105, 125)
# Opening applied to the shape before it is measured, in cells: it cuts the
# threads of shadow and floor texture that tie a frame to its surroundings,
# and it keeps a square corner square.
_OPEN = 5
# How closely the four corners must fit the shape, both ways.
_MIN_FIT = float(os.getenv("ART_MIN_FIT", "0.95") or 0.95)
# The widest corner a photographed rectangle may show (and so the sharpest,
# 180 minus it), and the shortest a side may be against the side opposite it.
# 0.7 is a picture on the floor shot from standing height; a subject whose
# outline happens to have four corners is usually far more lopsided.
_MAX_CORNER = float(os.getenv("ART_MAX_CORNER", "135") or 135)
_MIN_SIDE_RATIO = float(os.getenv("ART_MIN_SIDE_RATIO", "0.7") or 0.7)
# A piece of what the shape used to belong to counts as SOLID if it survives an
# opening this many cells across — a face does, a fringe of shadow or a thread
# of carpet does not — and more of it than this share of the shape means the
# shape is part of something larger.
_CHUNK = 9
_DISSOLVE = float(os.getenv("ART_DISSOLVE", "0.2") or 0.2)
# A second region this large against the first is a second item in the photo:
# kept too when it is a picture, and the photo refused when it is not.
_COMPANION = 0.25
# How far out a frame's outer edge may sit from the mount the flood stopped at,
# as a share of the piece's short side.
_LAYER = 0.15
# How far a side's fitted direction may turn from the first guess at it.
_REFIT_DEG = 4.0
# How far a side may be pulled in to the edge it was fitted outside of, in
# working cells, and how many stretches of it are read to do that.
_SNAP_IN = int(os.getenv("ART_SNAP_IN", "5") or 5)
_SNAP_PARTS = 4
_SNAP_FLOOR = float(os.getenv("ART_SNAP_FLOOR", "8") or 8)
# The side positions are settled at this multiple of the working size.
_FINE = 2


def _edges(img: Image.Image) -> Image.Image:
    """How sharply each pixel's neighbourhood changes: the 3x3 local range of
    a median-smoothed copy, per channel, keeping the largest of the three.

    The median first, because it removes texture finer than a few cells —
    wood grain, carpet, the mortar between bricks, sensor noise — while
    leaving a step, which is what the outer edge of a frame is. Per channel,
    because a frame can differ from its wall in hue alone."""
    smooth = img.filter(ImageFilter.MedianFilter(5))
    out = None
    for band in smooth.split():
        grad = ImageChops.difference(band.filter(ImageFilter.MaxFilter(3)),
                                     band.filter(ImageFilter.MinFilter(3)))
        out = grad if out is None else ImageChops.lighter(out, grad)
    return out


def _reach(edges: Image.Image) -> Image.Image:
    """For each pixel, the weakest edge that has to be crossed to get to it
    from outside the photo: the lowest, over every path in from the border, of
    the strongest edge on that path.

    Everything a threshold could ask about the photo is in this one image —
    the pixels a flood from the border reaches with bar `t` are exactly those
    below `t` here — so it is computed once and each rung of _LADDER is a
    point() on it. One pass of a bucket queue: the levels are 0-255, so the
    lowest open pixel is always found in the first non-empty bucket."""
    w, h = edges.size
    grad = edges.tobytes()
    best = bytearray(b"\xff") * (w * h)
    done = bytearray(w * h)
    buckets: list[list[int]] = [[] for _ in range(256)]
    rim = ({x for x in range(w)} | {(h - 1) * w + x for x in range(w)}
           | {y * w for y in range(h)} | {y * w + w - 1 for y in range(h)})
    for i in rim:
        best[i] = grad[i]
        buckets[grad[i]].append(i)
    size = w * h
    for level in range(256):
        todo = buckets[level]
        while todo:
            i = todo.pop()
            if done[i]:
                continue
            done[i] = 1
            x = i % w
            for j in (i - 1 if x else -1, i + 1 if x < w - 1 else -1,
                      i - w, i + w):
                if 0 <= j < size and not done[j]:
                    cost = grad[j] if grad[j] > level else level
                    if cost < best[j]:
                        best[j] = cost
                        buckets[cost].append(j)
    return Image.frombytes("L", (w, h), bytes(best))


class _Region(NamedTuple):
    """A 4-connected region of a mask, as the runs of pixels it is made of."""
    cells: int
    rim: bool                            # touches the edge of the photo
    runs: list[tuple[int, int, int]]     # (y, first x, last x + 1)

    def matte(self, size: tuple[int, int]) -> Image.Image:
        out = Image.new("L", size, 0)
        draw = ImageDraw.Draw(out)
        for y, x0, x1 in self.runs:
            draw.line([(x0, y), (x1 - 1, y)], fill=255)
        return out


def _regions(mask: Image.Image) -> list[_Region]:
    """Every 4-connected region of `mask`, largest first.

    Labelled by RUNS rather than pixel by pixel: each row's runs are found
    with one regular expression over its bytes, and a run joins whatever
    run it overlaps in the row above. A few thousand runs instead of tens of
    thousands of pixels, and it is asked once per rung of _LADDER."""
    w, h = mask.size
    data = mask.tobytes()
    runs: list[tuple[int, int, int]] = []
    parent: list[int] = []

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    above: list[int] = []
    for y in range(h):
        here = []
        for m in re.finditer(rb"[^\x00]+", data[y * w:(y + 1) * w]):
            here.append(len(runs))
            parent.append(len(runs))
            runs.append((y, m.start(), m.end()))
        j = 0
        for i in here:
            _, x0, x1 = runs[i]
            while j < len(above) and runs[above[j]][2] <= x0:
                j += 1
            k = j
            while k < len(above) and runs[above[k]][1] < x1:
                a, b = root(i), root(above[k])
                if a != b:
                    parent[b] = a
                k += 1
        above = here
    groups: dict[int, list[tuple[int, int, int]]] = {}
    for i, run in enumerate(runs):
        groups.setdefault(root(i), []).append(run)
    out = [_Region(sum(x1 - x0 for _, x0, x1 in g),
                   any(y in (0, h - 1) or x0 == 0 or x1 == w for y, x0, x1 in g),
                   g)
           for g in groups.values()]
    out.sort(key=lambda r: -r.cells)
    return out


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convex hull, counter-clockwise (monotone chain)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _poly_area(poly) -> float:
    return abs(sum(poly[i][0] * poly[i - 1][1] - poly[i - 1][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


def _four(hull: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The hull cut down to its four most important corners: repeatedly drop
    the vertex whose removal costs the least area."""
    poly = list(hull)
    while len(poly) > 4:
        cheapest, at = None, 0
        for i in range(len(poly)):
            a, b, c = poly[i - 1], poly[i], poly[(i + 1) % len(poly)]
            cost = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))
            if cheapest is None or cost < cheapest:
                cheapest, at = cost, i
        poly.pop(at)
    return poly


def _clockwise(q) -> list[tuple[float, float]]:
    """Corners in order round the centre, starting top-left."""
    cx = sum(p[0] for p in q) / len(q)
    cy = sum(p[1] for p in q) / len(q)
    return sorted(q, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


def _sides(q) -> list[tuple[tuple[float, float], tuple[float, float],
                            tuple[float, float]]]:
    """(start, end, outward unit normal) for each side of `q`."""
    cx = sum(p[0] for p in q) / 4
    cy = sum(p[1] for p in q) / 4
    out = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        n = ((b[1] - a[1]) / length, -(b[0] - a[0]) / length)
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if (mx - cx) * n[0] + (my - cy) * n[1] < 0:
            n = (-n[0], -n[1])
        out.append((a, b, n))
    return out


def _meet(p, d, q, e) -> Optional[tuple[float, float]]:
    """Where the line through p along d meets the line through q along e."""
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return (p[0] + t * d[0], p[1] + t * d[1])


def _moved(q, by: list[float]) -> list[tuple[float, float]]:
    """`q` with side i moved outward by by[i] (inward when negative)."""
    lines = [((a[0] + n[0] * s, a[1] + n[1] * s), (b[0] - a[0], b[1] - a[1]))
             for (a, b, n), s in zip(_sides(q), by)]
    out = []
    for i in range(4):
        (p, d), (r, e) = lines[i - 1], lines[i]
        out.append(_meet(p, d, r, e) or r)
    return out


def _fit_quad(matte: Image.Image) -> Optional[list[tuple[float, float]]]:
    """Four corners for a region: each side a straight line along the
    region's own boundary, set at the outside of it, and the corners where
    those lines meet.

    The hull's four most important corners are only a first guess. With a
    shadow along two sides they sit at the shadow's corners rather than the
    frame's, and joining them cuts across the frame's other two corners. So
    each side is re-fitted to the boundary pixels along the middle of it,
    where the edge is the frame's own, and set at the outside of them. The
    fitted direction is only taken when it agrees with the first guess to
    within _REFIT_DEG: a few ragged pixels — a frame standing on a skirting
    board, with a shadow notched into it — can tilt a fitted line well off
    the frame it belongs to, and a tilted side clips a corner."""
    rim = _boundary(matte)
    hull = _hull([c for x, y in rim
                  for c in ((x - 0.5, y - 0.5), (x + 0.5, y - 0.5),
                            (x - 0.5, y + 0.5), (x + 0.5, y + 0.5))])
    if len(hull) < 4:
        return None
    lines = []
    for a, b, n in _sides(_clockwise(_four(hull))):
        length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        d = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        middle = [(px, py) for px, py in rim
                  if 0.15 * length <= (px - a[0]) * d[0] + (py - a[1]) * d[1]
                  <= 0.85 * length
                  and abs((px - a[0]) * n[0] + (py - a[1]) * n[1]) <= 4]
        if len(middle) >= 5:
            # Total least squares: the direction the points spread along most.
            mx = sum(p[0] for p in middle) / len(middle)
            my = sum(p[1] for p in middle) / len(middle)
            sxx = sum((p[0] - mx) ** 2 for p in middle)
            syy = sum((p[1] - my) ** 2 for p in middle)
            sxy = sum((p[0] - mx) * (p[1] - my) for p in middle)
            ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
            fitted = (math.cos(ang), math.sin(ang))
            if abs(fitted[0] * d[0] + fitted[1] * d[1]) >= \
                    math.cos(math.radians(_REFIT_DEG)):
                d = fitted
        out = (d[1], -d[0])
        if out[0] * n[0] + out[1] * n[1] < 0:
            out = (-out[0], -out[1])
        # Out to the outermost of the side's own pixels, so the line runs along
        # the outside of the region rather than through the middle of its edge.
        push = max([(px - a[0]) * out[0] + (py - a[1]) * out[1]
                    for px, py in middle], default=0.0)
        lines.append(((a[0] + out[0] * (push + 0.5),
                       a[1] + out[1] * (push + 0.5)), d))
    corners = []
    for i in range(4):
        (p, d), (r, e) = lines[i - 1], lines[i]
        at = _meet(p, d, r, e)
        if at is None:
            return None
        corners.append(at)
    return _clockwise(corners)


def _boundary(matte: Image.Image) -> list[tuple[float, float]]:
    """The centres of the pixels of `matte` with a 4-neighbour outside it."""
    w, h = matte.size
    pad = Image.new("L", (w + 2, h + 2), 0)
    pad.paste(matte, (1, 1))
    inner = matte
    for dx, dy in ((0, 1), (2, 1), (1, 0), (1, 2)):
        inner = ImageChops.darker(inner, pad.crop((dx, dy, dx + w, dy + h)))
    rim = ImageChops.subtract(matte, inner)
    box = rim.getbbox()
    if not box:
        return []
    width = box[2] - box[0]
    return [(box[0] + i % width + 0.5, box[1] + i // width + 0.5)
            for i in (m.start() for m in
                      re.finditer(rb"[^\x00]", rim.crop(box).tobytes()))]


def _fit(quad, matte: Image.Image, cells: int) -> tuple[float, float]:
    """(share of the region inside the quad, share of the quad the region
    fills)."""
    drawn = Image.new("L", matte.size, 0)
    ImageDraw.Draw(drawn).polygon([tuple(p) for p in quad], fill=255)
    both = ImageChops.multiply(drawn, matte).histogram()[255]
    return both / cells, both / max(1, drawn.histogram()[255])


def _picture_shaped(region: _Region, size: tuple[int, int]
                    ) -> tuple[Optional[list[tuple[float, float]]], str]:
    """The region's four corners if it is shaped like a photographed picture,
    else None — and, either way, why. Its size is asked later, of the outline
    once it has been settled onto the piece's edge: this one still carries a
    rim of that edge, and would flatter a piece just under the floor."""
    matte = region.matte(size)
    q = _fit_quad(matte)
    if q is None:
        return None, "no four corners"
    corners = []
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        v1, v2 = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
        norm = math.hypot(*v1) * math.hypot(*v2)
        if not norm:
            return None, "a corner with no sides"
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / norm
        corners.append(math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
    if max(corners) > _MAX_CORNER or min(corners) < 180 - _MAX_CORNER:
        return None, "corners no photograph of a rectangle has"
    sides = [math.hypot(q[(i + 1) % 4][0] - q[i][0], q[(i + 1) % 4][1] - q[i][1])
             for i in range(4)]
    for i in (0, 1):
        if min(sides[i], sides[i + 2]) < _MIN_SIDE_RATIO * max(sides[i], sides[i + 2]):
            return None, "one side far shorter than the side opposite it"
    covered, filled = _fit(q, matte, region.cells)
    if covered < _MIN_FIT or filled < _MIN_FIT:
        return None, f"four corners fit it {covered:.2f}/{filled:.2f}"
    return q, ""


def _profile(edges: Image.Image, a, b, n, offsets) -> list[
        tuple[float, Optional[float], list[int]]]:
    """Along side a-b moved outward by each offset: (offset, mean edge
    strength, the samples) — mean None where most of the line is off the
    photo. The middle 80% of the side, so a corner is never read as an edge
    running across it, and at most 48 samples along it: an average, and a
    share of samples lit, are both settled long before that."""
    w, h = edges.size
    px = edges.load()
    count = max(8, min(48, int(math.hypot(b[0] - a[0], b[1] - a[1]))))
    out = []
    for s in offsets:
        vals = []
        for j in range(count):
            f = 0.1 + 0.8 * j / (count - 1)
            x = round(a[0] + (b[0] - a[0]) * f + n[0] * s)
            y = round(a[1] + (b[1] - a[1]) * f + n[1] * s)
            if 0 <= x < w and 0 <= y < h:
                vals.append(px[x, y])
        mean = sum(vals) / len(vals) if len(vals) * 2 >= count else None
        out.append((s, mean, vals))
    return out


def _snap(edges: Image.Image, q, reach: float, step: float
          ) -> list[tuple[float, float]]:
    """Each side re-laid along the first real edge met coming in from the
    background, pulled in by no more than `reach`.

    The region the flood left can carry a ragged halo of the wall it stopped
    short of, and the shadow outside the frame, so its outline can sit a few
    cells off the piece and be turned a degree or two off it as well. So each
    side is read in _SNAP_PARTS stretches: each stretch walks in from outside
    to the first line that rises well above what the background does by
    itself, and the side becomes a line through where the stretches stopped,
    pushed out so every one of them is on or inside it. Coming in from
    OUTSIDE is what makes this safe: a faint outer edge of a frame stops it
    before the bold inner edge of the mount is ever reached."""
    lines = []
    for a, b, n in _sides(q):
        offsets = [i * step for i in range(-round(reach / step),
                                           round(2 * reach / step) + 1)]
        beyond = sorted(m for s, m, _ in _profile(edges, a, b, n, offsets)
                        if s >= reach and m is not None)
        bar = max(_SNAP_FLOOR, 2.5 * (beyond[len(beyond) // 2] if beyond else 0.0))
        stops = []
        for k in range(_SNAP_PARTS):
            f0, f1 = k / _SNAP_PARTS, (k + 1) / _SNAP_PARTS
            pa = (a[0] + (b[0] - a[0]) * f0, a[1] + (b[1] - a[1]) * f0)
            pb = (a[0] + (b[0] - a[0]) * f1, a[1] + (b[1] - a[1]) * f1)
            move = 0.0
            for s, m, _ in reversed(_profile(edges, pa, pb, n, offsets)):
                if s > 2 * step:
                    continue
                if m is not None and m >= bar:
                    move = min(0.0, s + 2 * step)
                    break
            mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
            stops.append((mid[0] + n[0] * move, mid[1] + n[1] * move))
        # A line through the stops (least squares, against the side), pushed
        # out to the outermost of them.
        length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        d = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        ts = [(p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1] for p in stops]
        us = [(p[0] - a[0]) * n[0] + (p[1] - a[1]) * n[1] for p in stops]
        tm, um = sum(ts) / len(ts), sum(us) / len(us)
        var = sum((t - tm) ** 2 for t in ts)
        slope = sum((t - tm) * (u - um) for t, u in zip(ts, us)) / var \
            if var else 0.0
        # Never turned further than a stretch's worth of snapping could turn it.
        slope = max(-reach / length, min(reach / length, slope))
        push = max(u - (um + slope * (t - tm)) for t, u in zip(ts, us))
        base = um + push - slope * tm
        start = (a[0] + n[0] * base, a[1] + n[1] * base)
        direction = (d[0] + n[0] * slope, d[1] + n[1] * slope)
        lines.append((start, direction))
    out = []
    for i in range(4):
        (p, d), (r, e) = lines[i - 1], lines[i]
        out.append(_meet(p, d, r, e) or r)
    return out


def _outer_layer(edges: Image.Image, q) -> Optional[list[tuple[float, float]]]:
    """`q` grown out to a frame's outer edge, when one hugs it on at least
    three sides — else None.

    The flood that found `q` may have got into a frame through a stretch of
    its outer edge that was faint against the wall, and flooded the moulding
    all the way round; the first clean shape was then the mount inside it.
    What gives that away is an edge running parallel to `q` a moulding's
    width outside it, on side after side. Two ways to count one:

      * STRONG on three sides: a clear edge, lit along most of the side, with
        a trough of background-level edge between it and `q` — so it is a
        separate edge, not the far side of `q`'s own.
      * or CONSISTENT on three sides: a weaker line, at the same distance on
        each. A moulding is the same width all the way round; texture in a
        floor is not arranged in a frame round the picture."""
    short = min(math.hypot(q[(i + 1) % 4][0] - q[i][0], q[(i + 1) % 4][1] - q[i][1])
                for i in range(4))
    reach = max(4, int(short * _LAYER))
    strong: list[Optional[int]] = []
    weak: list[list[int]] = []
    for a, b, n in _sides(q):
        prof = _profile(edges, a, b, n, range(0, reach + 1))
        seen = sorted(m for _, m, _ in prof if m is not None)
        if not seen:
            strong.append(None)
            weak.append([])
            continue
        base = seen[len(seen) // 2]
        hit, maybe, trough = None, [], False
        for s, m, vals in prof:
            if m is None:
                break
            if m <= max(6.0, 1.5 * base):
                trough = True
                continue
            if not trough:
                continue
            if m >= max(8.0, 2.5 * base) and \
                    sum(v >= max(8.0, 2 * base) for v in vals) >= 0.6 * len(vals):
                hit = s
            if m >= max(6.0, 1.6 * base) and \
                    sum(v >= max(6.0, 1.5 * base) for v in vals) >= 0.5 * len(vals):
                maybe.append(s)
        strong.append(hit)
        weak.append(maybe)
    found = [s for s in strong if s is not None]
    if len(found) >= 3:
        usual = sorted(found)[len(found) // 2]
        return _moved(q, [(s if s is not None else usual) + 1.0 for s in strong])
    best = None
    for at in sorted({s for side in weak for s in side}):
        agree = [max((s for s in side if abs(s - at) <= 1.5), default=None)
                 for side in weak]
        count = sum(s is not None for s in agree)
        if count >= 3 and (best is None or count > best[0]):
            best = (count, at, agree)
    if best is None:
        return None
    _, at, agree = best
    return _moved(q, [(s if s is not None else at) + 1.0 for s in agree])


def _dissolved(history: list[Image.Image], region: _Region,
               size: tuple[int, int]) -> float:
    """How much SOLID stuff the region has lost since it first came free of the
    edge of the photo, as a share of the region.

    `history` holds, rung by rung, a mask of the regions that were free of the
    photo's edge. The region's host is the earliest free region holding most
    of it. Whatever of the host is not in the region has dissolved on the way
    up the ladder; an opening throws away the fringes (shadow, threads of
    texture, the rim of an edge) and what is left is a part of the THING — the
    face whose weaker edge gave way before the shoulders did."""
    mine = region.matte(size)
    for free in history:
        if ImageChops.multiply(mine, free).histogram()[255] * 2 < region.cells:
            continue
        fp = free.load()
        seed = next((x, y) for y, x0, x1 in region.runs for x in range(x0, x1)
                    if fp[x, y])
        host = free.copy()
        ImageDraw.floodfill(host, seed, _OUTSIDE)
        gone = ImageChops.subtract(host.point(lambda v: 255 if v == _OUTSIDE
                                              else 0), mine)
        solid = gone.filter(ImageFilter.MinFilter(_CHUNK)) \
                    .filter(ImageFilter.MaxFilter(_CHUNK))
        return solid.histogram()[255] / region.cells
    return 0.0


def outline(rgb: Image.Image) -> Optional[tuple[Quad, ...]]:
    """The outer outline of the picture in `rgb` as four corners — or of each
    picture, when there are two side by side — or None.

    Corners are in `rgb`'s own pixel coordinates, clockwise from top-left, and
    may run a little past the edge of the photo; outline_matte() clips them.
    None is the answer to every doubt, and it means "keep this photo exactly as
    shot": no shape that is a picture, a picture that is part of something
    larger, a second thing beside it that is not a picture, or an outline that
    is the whole photo. The caller must treat None as "do nothing" rather than
    as "fall back to the model" — falling back to the model is the bug this
    module exists for.
    """
    w, h = rgb.size
    if w < 8 or h < 8:
        return None
    scale = min(1.0, _SIDE / max(w, h))
    size = (max(8, round(w * scale)), max(8, round(h * scale)))
    small = rgb.resize(size, Image.BOX).convert("RGB") if scale < 1 \
        else rgb.convert("RGB")
    edges = _edges(small)
    reach = _reach(edges.filter(ImageFilter.MaxFilter(3)))
    counts = reach.histogram()
    cells = size[0] * size[1]
    history: list[Image.Image] = []
    last = None
    for bar in _LADDER:
        standing = sum(counts[bar:])
        if standing == last:
            continue
        last = standing
        if standing < _MIN_AREA * cells:
            break
        shape = reach.point(lambda v, bar=bar: 255 if v >= bar else 0) \
                     .filter(ImageFilter.MinFilter(_OPEN)) \
                     .filter(ImageFilter.MaxFilter(_OPEN))
        free = [r for r in _regions(shape) if not r.rim]
        held = Image.new("L", size, 0)
        draw = ImageDraw.Draw(held)
        for region in free:
            for y, x0, x1 in region.runs:
                draw.line([(x0, y), (x1 - 1, y)], fill=255)
        history.append(held)
        if not free or free[0].cells < 0.8 * _MIN_AREA * cells:
            continue
        first, why = _picture_shaped(free[0], size)
        if first is None:
            continue
        lost = _dissolved(history[:-1], free[0], size)
        if lost > _DISSOLVE:
            log.info("art border: a four-cornered shape came free at bar %d, "
                     "but %.2f of what it belonged to dissolved first — a part "
                     "of something, not the piece; keeping the photo as shot",
                     bar, lost)
            return None
        found = [first]
        for region in free[1:]:
            if region.cells < _COMPANION * free[0].cells:
                break
            other, why = _picture_shaped(region, size)
            if other is None:
                log.info("art border: a picture, and beside it something that "
                         "is not one (%s) — keeping the photo as shot", why)
                return None
            found.append(other)
        return _settled(rgb, edges, found, size, scale, bar)
    log.info("art border: no four-cornered outline came free at any bar — "
             "keeping the photo as shot")
    return None


def _settled(rgb: Image.Image, edges: Image.Image, found: list, size,
             scale: float, bar: int) -> Optional[tuple[Quad, ...]]:
    """The quads the ladder found, grown out to any frame they sit inside,
    snapped to their edges at _FINE times the working size, and scaled back to
    the photo — or None when together they are the whole photo."""
    fine_size = (size[0] * _FINE, size[1] * _FINE)
    fine = _edges(rgb.resize(fine_size, Image.BOX).convert("RGB"))
    out = []
    for q in found:
        q = _snap(edges, q, _SNAP_IN, 0.5)
        for _ in range(3):
            grown = _outer_layer(edges, q)
            if grown is None:
                break
            log.info("art border: an edge hugs the outline a frame's width "
                     "outside it — growing out to the frame")
            q = _snap(edges, grown, _SNAP_IN, 0.5)
        else:
            # Still finding frame after frame: whatever this is, it is not a
            # picture with an edge that can be trusted.
            return None
        q = _snap(fine, [(x * _FINE, y * _FINE) for x, y in q],
                  (_SNAP_IN - 1) * _FINE, 0.5)
        out.append([(x / _FINE, y / _FINE) for x, y in q])
    # The first is the largest; a second picture beside it is vouched for by
    # the first's size, however small the photo makes it.
    if _poly_area(out[0]) < _MIN_AREA * size[0] * size[1]:
        log.info("art border: the outline covers %.2f of the photo — too small "
                 "to be the piece; keeping the photo as shot",
                 _poly_area(out[0]) / (size[0] * size[1]))
        return None
    covered = Image.new("L", size, 0)
    for q in out:
        ImageDraw.Draw(covered).polygon(q, fill=255)
    if covered.histogram()[255] >= _BLEED_SPAN * size[0] * size[1]:
        log.info("art border: the outline is the whole photo — nothing to "
                 "take away, keeping it as shot")
        return None
    k = 1 / scale
    quads = tuple(tuple((round(x * k), round(y * k)) for x, y in q) for q in out)
    log.info("art border: %d picture(s) outlined at bar %d: %s", len(quads),
             bar, quads)
    return quads


def outline_matte(size: tuple[int, int], quads) -> Image.Image:
    """A matte fully opaque inside every one of `quads` and clear outside them
    — clipped to the photo, so an outline running past its edge keeps
    everything of the piece that is in it.

    The whole point of the module in one function: what comes back is SOLID,
    so compositing through it cannot remove anything inside a picture's
    outline. There is no threshold in here and no shape to get wrong."""
    out = Image.new("L", size, 0)
    draw = ImageDraw.Draw(out)
    for q in quads:
        draw.polygon([(int(x), int(y)) for x, y in q], fill=255)
    return out


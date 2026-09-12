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

    A picture is a RECTANGLE. Find its outer border and keep everything
    inside it, whole. Never ask what is interesting within it.

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

import os
from typing import Optional

from PIL import Image, ImageChops, ImageFilter, ImageStat

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
_EDGE_DIST = int(os.getenv("ART_EDGE_DIST", "6") or 6)
_EDGE_TEXTURE = int(os.getenv("ART_EDGE_TEXTURE", "6") or 6)
_EDGE_SHARE = float(os.getenv("ART_EDGE_SHARE", "0.10") or 0.10)
# How many consecutive non-surround lines end the scan. One line is a scratch
# on the table or a row of sensor noise; two in a row is an edge.
_EDGE_RUN = int(os.getenv("ART_EDGE_RUN", "2") or 2)

Box = tuple[int, int, int, int]


def _surround(small: Image.Image) -> tuple[int, int, int]:
    """The colour of whatever the picture is lying on or hanging against,
    sampled from a band around the edge of the frame.

    Median rather than mean: a mean is dragged by the corner of the picture
    poking into the band, and a median is not until half the band is picture.
    """
    w, h = small.size
    band = max(1, round(min(w, h) * _SURROUND_BAND))
    ring = Image.new("L", (w, h), 255)
    inner = (band, band, max(band, w - band), max(band, h - band))
    ring.paste(0, inner)
    med = ImageStat.Stat(small, ring).median
    return (int(med[0]), int(med[1]), int(med[2]))


def _content(small: Image.Image) -> Image.Image:
    """A 1-bit mask of everything that is not the surround.

    Two signals, ORed, because each one alone misses a picture the other
    catches: colour (a bright print on a white wall) and local contrast (a
    pale drawing on paper the same white as the table it is on).
    """
    far = ImageChops.difference(small, Image.new("RGB", small.size,
                                                 _surround(small)))
    r, g, b = far.split()
    # Chebyshev over the channels: the largest single-channel difference. A
    # picture that differs in only one channel is still a picture.
    chroma = ImageChops.lighter(ImageChops.lighter(r, g), b)

    grey = small.convert("L")
    # Morphological gradient — the local range over a 3x3 window. Cheap, and
    # unlike an edge kernel it responds to texture as well as to lines.
    busy = ImageChops.difference(grey.filter(ImageFilter.MaxFilter(3)),
                                 grey.filter(ImageFilter.MinFilter(3)))

    mask = ImageChops.lighter(
        chroma.point(lambda v: 255 if v >= _SURROUND_DIST else 0),
        busy.point(lambda v: 255 if v >= _BUSY_LEVEL else 0))
    if _CLOSE > 0:
        k = _CLOSE * 2 + 1
        # Close: join the parts of one picture, then take back the growth.
        # MaxFilter/MinFilter are Pillow's dilate/erode for a binary mask.
        mask = mask.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    return mask


def _largest_region(mask: Image.Image) -> tuple[int, Optional[Box]]:
    """(cells, bounding box) of the largest 4-connected region of `mask`.

    An explicit stack, not recursion: the healthy case here is one region
    covering most of the mask, which is exactly the shape that blows Python's
    call stack. The same reason services/images._kept_shape uses one.
    """
    w, h = mask.size
    px = mask.load()
    seen = bytearray(w * h)
    best = (0, None)
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
            if size > best[0]:
                best = (size, (lo_x, lo_y, hi_x + 1, hi_y + 1))
    return best


def _scan_inward(small: Image.Image, box: Box) -> Box:
    """`box` widened to the first non-surround line found scanning IN from
    each edge of the photo.

    Answers "is this still the wall" rather than "is this the picture", so it
    reads two weak signals and keeps scanning while BOTH say the line is
    surround: no colour step away from it, and no texture on it. A wall, a
    table top and a sheet of backing card have neither; a frame's moulding, a
    sheet's drop shadow and a canvas's edge all have one or the other.

    Unioned with `box`, never replacing it: a scan that finds nothing at all
    leaves the content box exactly as it was, and can only ever make the
    result larger. See the note on _EDGE_DIST for why every error this can
    make except one is harmless.
    """
    w, h = small.size
    surround = _surround(small)
    px = small.load()
    grey = small.convert("L")
    texture = ImageChops.difference(grey.filter(ImageFilter.MaxFilter(3)),
                                    grey.filter(ImageFilter.MinFilter(3)))
    tx = texture.load()

    def _not_surround(pts) -> bool:
        pts = list(pts)
        if not pts:
            return False
        hits = 0
        for x, y in pts:
            r, g, b = px[x, y][:3]
            if (max(abs(r - surround[0]), abs(g - surround[1]),
                    abs(b - surround[2])) >= _EDGE_DIST
                    or tx[x, y] >= _EDGE_TEXTURE):
                hits += 1
        return hits / len(pts) >= _EDGE_SHARE

    def _first(lines) -> Optional[int]:
        """The index of the first of _EDGE_RUN consecutive non-surround lines."""
        run = 0
        for i, pts in lines:
            if _not_surround(pts):
                run += 1
                if run >= _EDGE_RUN:
                    return i - run + 1
            else:
                run = 0
        return None

    # Each side scans across the FULL span of the other axis, not the content
    # box's span: the frame's moulding reaches past the printed area it
    # surrounds, and a scan confined to that area's rows would step over the
    # corners of its own frame.
    left = _first((x, ((x, y) for y in range(h))) for x in range(w))
    right = _first((x, ((x, y) for y in range(h))) for x in range(w - 1, -1, -1))
    top = _first((y, ((x, y) for x in range(w))) for y in range(h))
    bottom = _first((y, ((x, y) for x in range(w))) for y in range(h - 1, -1, -1))
    return (min(box[0], left if left is not None else box[0]),
            min(box[1], top if top is not None else box[1]),
            max(box[2], (right + 1) if right is not None else box[2]),
            max(box[3], (bottom + 1) if bottom is not None else box[3]))


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
    """
    w, h = rgb.size
    if w < 8 or h < 8:
        return None
    scale = _SIDE / max(w, h)
    small = (rgb.resize((max(8, round(w * scale)), max(8, round(h * scale))),
                        Image.BOX) if scale < 1 else rgb).convert("RGB")
    sw, sh = small.size

    cells, box = _largest_region(_content(small))
    if not box:
        log.info("art border: no content found — keeping the photo as shot")
        return None
    bw, bh = box[2] - box[0], box[3] - box[1]
    fill = cells / (bw * bh) if bw and bh else 0.0
    area = (bw * bh) / (sw * sh)

    if fill < _MIN_RECT_FILL:
        # Found something, but it is not a rectangle: two objects spanning a
        # box between them, or a picture the content mask broke into pieces.
        log.info("art border: shape fills %.2f of its box, not a picture "
                 "— keeping the photo as shot", fill)
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

    # Back to full resolution, then outward. Rounded out on every side (floor
    # the near edges, ceil the far ones) so the scaling itself never shaves a
    # row off the artwork.
    k = 1 / scale if scale < 1 else 1.0
    left, top = box[0] * k, box[1] * k
    right, bottom = box[2] * k, box[3] * k
    mx, my = (right - left) * _MARGIN, (bottom - top) * _MARGIN
    return (max(0, int(left - mx)), max(0, int(top - my)),
            min(w, int(right + mx + 0.999)), min(h, int(bottom + my + 0.999)))


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

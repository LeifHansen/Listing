"""Framed, matted, or a bare sheet -- and whether the margin can be read.

Turns what the art lookup reported into a models.Presentation, and derives the
one consequence that cannot be left to a prompt: A MAT COVERS THE LOWER
MARGIN, and the lower margin is where the pencil signature and the edition
fraction are.

ART_RULE has always said the right thing about this -- never claim a mark that
is not in the photos, and never DENY one either, because "unsigned" about a
margin under a mat is the same false claim in the cheaper direction. But it
said it to the MODEL, and a model that writes "unsigned" into a description is
not something the server can retract afterwards. `_art_markers` only knew a
mark was unseen when the model said the words "not visible", via a regex on
its phrasing.

A mat is different in kind from a phrasing: it is OBJECTIVE EVIDENCE that the
margin cannot have been read, visible in any whole-frame photo. So when one is
seen, the caveat is produced here, in code, from the fact -- not hoped for
from the wording.

Nothing here imports anything heavy: it is called from main with a dict and
returns a model, and the tri-state logic is the part worth asserting on.
"""
from __future__ import annotations

from typing import Optional

# The mounts, in the words the schema asks for them in. A value that is not
# one of these is dropped rather than guessed at -- "presentation" is read by
# the shipping estimate, and a mount nobody recognises must not become one
# that changes a parcel.
MOUNTS = ("framed", "float_mounted", "matted", "shrink_wrapped", "rolled",
          "loose_sheet", "stretched_canvas", "canvas_board")

# The mounts that put something between the camera and the lower margin.
# `matted` is the obvious one; `framed` earns its place because a frame is
# nearly always matted and a frame's rebate covers the sheet's edge even when
# it is not. `shrink_wrapped` does not -- film is transparent, the margin
# reads straight through it.
COVERS_THE_MARGIN = ("framed", "matted", "float_mounted")

GLAZINGS = ("glass", "acrylic", "none")


def _tristate(value) -> Optional[bool]:
    """yes / no / unknown -> True / False / None.

    UNKNOWN IS THE DEFAULT AND THE POINT. Anything unrecognised -- an empty
    string, a hedge, a sentence, a missing key -- is None, never False. False
    is a claim ("there is no mat"), None is the absence of one, and on art the
    whole difference between a $15 poster and a $1,500 signed edition lives in
    keeping those apart.
    """
    text = str(value or "").strip().lower()
    if text in ("yes", "true", "y", "1"):
        return True
    if text in ("no", "false", "n", "0"):
        return False
    return None


def _inches(text: str) -> tuple[float, float]:
    """\"24 x 18 in\" -> (24.0, 18.0). (0.0, 0.0) for anything unparseable.

    Deliberately narrow: it reads two numbers either side of an x. A size
    feeds a shipping box, so a misread is a parcel that does not fit, and
    zero means "nobody said" -- which the estimator already handles.
    """
    import re
    match = re.search(r"(\d+(?:\.\d+)?)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)",
                      str(text or ""))
    if not match:
        return (0.0, 0.0)
    try:
        w, h = float(match.group(1)), float(match.group(2))
    except (TypeError, ValueError):
        return (0.0, 0.0)
    # A picture measured in feet or millimetres is a misread, not a picture.
    if not (1.0 <= w <= 120.0 and 1.0 <= h <= 120.0):
        return (0.0, 0.0)
    return (w, h)


def from_lookup(found: dict):
    """A models.Presentation from the art lookup's answer, or None.

    None when the lookup said nothing usable -- which is most non-art and any
    photo that could not settle it. A Presentation of all-blanks and all-Nones
    would be indistinguishable from one nobody filled in, and would put an
    empty card in the editor for every listing in the app.
    """
    from ....models import Presentation

    mount = str(found.get("presentation") or "").strip().lower().replace(" ", "_")
    if mount not in MOUNTS:
        mount = ""
    glazing = str(found.get("glazing") or "").strip().lower()
    if glazing not in GLAZINGS:
        glazing = ""
    frame = str(found.get("frame") or "").strip()[:120]
    width, height = _inches(found.get("outer_size"))

    matted = _tristate(found.get("matted"))
    # A mount of "matted" IS a mat, whatever the separate question answered.
    if mount == "matted":
        matted = True

    margin = _tristate(found.get("margin_visible"))
    # ...and a mount that covers the margin settles it when nothing else did.
    # Only downward: a lookup that says the margin IS visible has looked, and
    # this inference has not.
    if margin is None and mount in COVERS_THE_MARGIN:
        margin = False

    if not any((mount, glazing, frame, width, height)) and \
            matted is None and margin is None:
        return None

    return Presentation(
        mount=mount, glazing=glazing, frame_material=frame,
        outer_width_in=width, outer_height_in=height,
        matted=matted, margin_visible=margin,
    )


# What the seller is told to do about a covered margin. A physical
# instruction, not a wish: the difference between "the signature could not be
# seen" and "lift the mat at the lower corners" is whether anything happens.
_MARGIN_ASK = (
    "The {cover} covers the lower margin, where the pencil signature and the "
    "edition number are -- so this piece is not unsigned and not an open "
    "edition, it is one whose margin has not been seen. {fix} at both lower "
    "corners and photograph them close up before listing it as either."
)

# What is actually in the way, and what to do about that specific thing. A
# seller told to "lift the mat" on a frame with no mat in it reads the caveat
# as boilerplate and stops reading the next one.
_COVERS = {
    "matted": ("mat", "Lift the mat"),
    "float_mounted": ("mount", "Lift the sheet"),
    "framed": ("frame, and probably a mat inside it,",
               "Take the piece out of the frame and lift the mat"),
}
_DEFAULT_COVER = ("mount", "Take the piece out of its mount")


def margin_ask(presentation) -> str:
    """The covered-margin caveat, naming what is doing the covering."""
    mount = getattr(presentation, "mount", "") or ""
    cover, fix = _COVERS.get(mount, _DEFAULT_COVER)
    return _MARGIN_ASK.format(cover=cover, fix=fix)

GLAZING_ASK = (
    "Glass or acrylic could not be told apart in these photos, and they ship "
    "completely differently -- tap it: glass rings and sits dead flat, "
    "acrylic thuds and flexes. Glass needs a double box, taped glazing and "
    "corner protectors, or take the glass out and say so in the listing."
)

# Said once the answer IS glass, because at that point it stops being a
# question and becomes the most expensive thing about the parcel. A framed
# picture behind glass is the worst thing in resale to post: heavy, rigid,
# oversized, and when it breaks the glass cuts through the artwork on its way.
# The advice is what an experienced seller does and a new one finds out about
# afterwards.
GLASS_PACKING = (
    "This is framed behind glass -- the most expensive thing in resale to get "
    "wrong. Tape the glass in an X so a break cannot travel, corner-protect "
    "the frame, and double-box it{size}. Cheaper and safer: take the glass "
    "out, ship without it, and say \"glass removed for safe shipping\" in the "
    "listing -- buyers of framed art expect it and it is a selling point, not "
    "a defect."
)

# Over 108 inches of length plus girth, USPS and the cheaper courier services
# stop carrying a parcel at all -- which a seller discovers at the counter,
# having already sold it at a flat rate.
OVERSIZE_WARNING = (
    "At about {w:.0f} x {h:.0f} in over the frame, the box for this is around "
    "{length:.0f} in on its longest side and {girth:.0f} in of length plus "
    "girth. Past 108 in the usual services will not carry it at any price, so "
    "price the postage -- or the freight -- before you list it, not after it "
    "sells."
)

# What a frame needs around it in the box: two inches of protection on each
# side, and a double box adds another inch each way.
_PACKING_MARGIN_IN = 6.0


def _box_advice(presentation) -> str:
    """" -- it is about 24 x 18 in, so a box of at least 30 x 24 in", or ""."""
    w, h = presentation.outer_width_in, presentation.outer_height_in
    if not (w and h):
        return ""
    return (f" -- it is about {w:.0f} x {h:.0f} in over the frame, so the box "
            f"wants to be at least {w + _PACKING_MARGIN_IN:.0f} x "
            f"{h + _PACKING_MARGIN_IN:.0f} in")


def cautions(presentation) -> list:
    """What the seller must be told, from the presentation alone.

    Derived from the FACT rather than from the model's phrasing, which is the
    whole reason this module exists -- see `hides_the_margin` on the model.
    """
    if presentation is None:
        return []
    out = []
    if presentation.hides_the_margin():
        out.append(margin_ask(presentation))
    framed = presentation.mount in ("framed", "float_mounted")
    if framed and not presentation.glazing:
        out.append(GLAZING_ASK)
    elif presentation.glazing == "glass":
        out.append(GLASS_PACKING.format(size=_box_advice(presentation)))

    # The parcel nobody can post. Checked on the box, not the frame, because
    # it is the box the carrier measures.
    w, h = presentation.outer_width_in, presentation.outer_height_in
    if w and h:
        box_w, box_h = w + _PACKING_MARGIN_IN, h + _PACKING_MARGIN_IN
        depth = max(presentation.depth_in, 3.0) + 2.0
        length = max(box_w, box_h)
        girth = 2 * (min(box_w, box_h) + depth)
        if length + girth > 108.0:
            out.append(OVERSIZE_WARNING.format(w=w, h=h, length=length,
                                               girth=length + girth))
    return out

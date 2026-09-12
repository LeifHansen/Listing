"""Which way is up, for the ITEM — whatever the item is.

EXIF records how the camera was held, and the photo pass honours it. It says
nothing about the item: a shirt laid on the floor with its collar to the
left, a boot photographed lying on its side, a box shot from above with its
text running down the frame. The only way to know is to look at the picture,
so this asks a vision model — and HOW it asks is the whole point.

The pass this replaces worked on shirts and was wrong on most other things,
for reasons that are all in its prompt. Its reference image was "a shirt laid
flat, collar at the top", so every other kind of item was judged as a worse
shirt. It had no idea that many items HAVE no wrong way up — a coin, a plate,
a wallet, a bracelet, a knife on a table, shot from above — so asked which way
to turn one it invented an answer. It saw 384px thumbnails, too small to read
the label, box or logo that is the one reliable cue on an object. And its
safety net was a yes/no question about the turned photo, told to answer "no"
when unsure, which cancelled correct turns on anything that was not a shirt
and confirmed wrong ones by the same single-image guesswork that proposed
them. Sellers saw exactly that: shirts came out right, objects came out
sideways, and the objects were the point.

So, two looks, neither of them about shirts:

  1. SCREEN. Each photo, large enough to read text, with a rubric that
     starts from what the item is and how it sits in the frame. Readable
     text wins. A standing item stands on its base. A hanging or worn item
     has its top at the top. A garment laid flat has its collar at the top.
     Anything else laid flat and shot from above has no wrong orientation
     unless it carries text, and a detail close-up has none at all — those
     answer 0 by RULE, enforced here, not left to the model's judgement. The
     model names the item, says how it sits, says whether it can read
     anything, and only then gives a turn and whether it is sure. Only a
     sure turn with a basis goes forward.

  2. CONFIRM, BY COMPARISON. For each proposed turn the model is shown the
     same photo at all four quarter-turns, lettered, and asked which one is
     upright — or that none of them is more upright than the others. Picking
     the natural one out of four is a far easier question than judging one
     image in isolation, and it is a different question from the screen's,
     so a wrong proposal has to survive two unrelated mistakes to ship. The
     letters do not sit in the same order for every photo, so a model that
     favours a position adds noise rather than a bias. A turn is applied
     only when the pick IS the proposal.

The two directions of error are not equal, and every rule above leans the
same way: a photo left as shot costs the seller nothing, and a photo turned
wrongly is a listing that looks broken and a seller who stops trusting the
pass. Entirely best-effort: any failure, timeout or budget overrun answers
"no rotation" for the photos it covers, which is exactly what the pass does
with the API off. A wrong turn, when one gets through, is one tap of the
rotate button on the tile or the card, and Restore original goes back to
the photo as shot.

The screen also reports something it is not named for and that nothing used
to read: whether a photo is a CLOSE-UP OF PART of an item rather than a photo
of the whole thing. The cutout needs that answer — run on a close-up of a
tag it deletes the garment and keeps the label — and this pass is the only
thing that looks at the photo before the cutout does. See _details.

Runs on the ORIGINALS, before the cutout, so the contact shadow falls below
an upright item. Never on the upload request itself: every path that
optimizes photos is a background job the client polls.
"""
from __future__ import annotations

import base64
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from .. import config
from ..config import log

# Photos per screen call. Fewer than the pass this replaced sent (12): each
# photo is larger, and the model's attention is per call. A 250-photo batch
# is ~32 calls, four at a time.
_SCREEN_BATCH = 8
# Proposals per confirm call. Each is four images, so this is 24 per call.
_CONFIRM_BATCH = 6
_WORKERS = 4
# The screen has to READ: a label, a logo, the text on a box. 384px could
# not, and text is the one cue that works on every kind of item. 640px
# reads a product label in a whole-item shot at a few hundred visual tokens.
_SCREEN_SIDE = 640
# The confirm compares four versions of the same photo; a coarser copy is
# enough for "which of these is the right way up".
_CONFIRM_SIDE = 448
_VALID = (0, 90, 180, 270)
_LETTERS = "ABCD"
# Its own budget, and no retries: a coarse whole-image judgement is not
# worth asking twice, and the shared client's 120s x 3 attempts could hold a
# batch for six minutes to decide whether a mug is upside down.
_CALL_TIMEOUT = float(os.getenv("ORIENT_TIMEOUT_SECONDS", "60") or 60)
# ...and a ceiling on the pass as a whole. Past it, remaining batches are
# skipped and their photos stay as shot — the right outcome for an
# enhancement, and far better than a batch that never finishes. The ceiling
# is a floor that grows with the pile: 150s covers a listing's forty photos
# with room to spare, and a 250-photo bulk batch — which nobody is watching
# a request for, and whose cutouts alone take a quarter of an hour — gets
# time to answer for all of them rather than straightening the first
# hundred and leaving the rest as they were.
_TOTAL_BUDGET = float(os.getenv("ORIENT_BUDGET_SECONDS", "150") or 150)
_PER_PHOTO = float(os.getenv("ORIENT_SECONDS_PER_PHOTO", "2") or 2)

# How the item sits in the frame, as the screen reports it. Only these can
# propose a turn on their own; anything else needs readable text to go on,
# because a flat-lay or a close-up has no "up" for the model to find and an
# answer for one is an invention.
_TURNS_ON_ITS_OWN = ("standing", "hanging", "worn", "garment_flat")

# The one "sits" answer that means there is no whole item in the frame. The
# screen has always collected it -- a close-up of a label, a tag, a stitch or
# a texture -- and used it only to refuse a turn. It answers a second question
# the photo pass badly needs and had no way to ask; see _details.
_DETAIL = "detail"


class Screened(NamedTuple):
    """What one look at a pile of photos found.

    `rotations` is {filename: clockwise degrees} for the items lying sideways
    or on their head. `details` is the filenames that are close-ups of part of
    an item, which the cutout must leave alone -- see _details. `art` is the
    filenames whose item IS A PICTURE, which take the cutout's rectangle path
    instead of the model -- see _art.
    """
    rotations: dict[str, int]
    details: frozenset[str]
    art: frozenset[str]


_NOTHING = Screened({}, frozenset(), frozenset())

_SCREEN_RULES = """
These are photos 1 to {n} of secondhand items being listed for sale. The
camera's own orientation has already been corrected. What is left to decide
is whether the ITEM was photographed lying sideways or upside down in the
frame, and only that.

For EVERY photo, in this order:
1. "item": what it is, in a few words.
2. "sits": how it sits in the frame —
   "standing": on its base, as it would stand on a shelf or a table (a
     bottle, a mug, a boot, a lamp, a figurine, a camera, a box on end);
   "hanging": on a hanger, a hook or a mannequin;
   "worn": on a person;
   "garment_flat": clothing laid out flat for the photo;
   "flat": anything else laid flat and shot from above — a coin, a plate, a
     wallet, a watch, a bracelet, a knife, a tool, folded fabric, a book
     lying on a table;
   "detail": a close-up of part of an item — a label, a tag, a stitch, a
     mark, a texture, a logo without the rest of the item in view.
3. "art": true when the ITEM BEING SOLD is a picture — a painting, print,
   poster, drawing, watercolour, gouache, etching, lithograph, photograph,
   canvas, framed or unframed, hanging or laid flat. The test is whether the
   thing's own front surface IS an image: a painting of a boat is art; a mug
   with a boat printed on it is a mug. Say true for the FRONT of one, and for
   a close-up of part of one (a signature, a corner, an edition number) —
   false for its BACK, its packaging, or a bare stretched canvas.
   When in doubt about a flat rectangular thing whose face is a picture,
   answer true: the only thing this changes is that the photo keeps
   everything inside the picture's edge.
4. "text": whether there is readable printed text or a logo, and which way it
   reads as the photo stands now: "none", "upright" (reads left to right,
   the right way up), "sideways" or "upside_down".
5. "rotate": the CLOCKWISE turn, 0, 90, 180 or 270, that makes the photo
   upright, and "sure": true only when the evidence is unmistakable.

Which way is up:
- Readable text wins over everything. Labels, packaging, book covers, box
  art, screens, dials and logos read left to right and horizontally when the
  photo is upright. Text that already reads normally means the photo IS
  upright: answer 0, however the item looks. Text reading downwards, the
  tops of its letters pointing right, needs 270. Text reading upwards, the
  tops of its letters pointing left, needs 90. Text upside down needs 180.
- A STANDING item stands: base at the bottom; cap, lid or opening at the top;
  shoes sole down; a mug's opening up; a lamp's shade up; a figurine's feet
  down; the table or shelf it stands on is BELOW it, never beside it.
- A HANGING or WORN item: hanger, hook, shoulders or head at the top; hem,
  feet or fringe at the bottom.
- A GARMENT LAID FLAT: collar and shoulders at the top, hem at the bottom,
  sleeves out to the sides. A collar pointing left needs 90; pointing right
  needs 270; a collar at the bottom needs 180.
- Everything else laid FLAT and shot from above has NO wrong orientation
  unless it carries readable text: the seller framed a coin, a plate, a
  wallet or a tool the way they wanted it, and turning it cannot make it
  more upright. Answer 0, sure=false.
- A DETAIL close-up is 0, sure=false, unless readable text settles it.
- 180 needs text reading upside down, or a standing, hanging or worn item
  unmistakably on its head. Never 180 because a graphic or a pattern looks
  odd.
- Judge each photo on its own; one set often mixes orientations.
- When torn, answer 0 with sure=false. A photo left as shot costs the seller
  nothing. A photo turned wrongly is a listing that looks broken.

Return ONLY a JSON object, no markdown fences, one entry per photo:
{{"photos": [{{"photo": 1, "item": "...", "sits": "standing", "art": false,
             "text": "none", "rotate": 0, "sure": false}}]}}
"""

_CONFIRM_RULES = """
Above are photos 1 to {n} of secondhand items. Each photo is shown four
times, lettered A to D: the same photo at each of its four quarter-turns, in
no particular order.

For each photo, pick the ONE version that is upright — the way this item is
shown in a shop's product photo: any text reading left to right and the
right way up, a standing item on its base with the surface below it, a
hanging or worn item with its top at the top, a garment laid flat with its
collar at the top. If no version is more upright than the others — an item
laid flat and shot from above with no text, a close-up of a detail — answer
"none". Answer "none" too when you cannot tell: a photo left as shot costs
the seller nothing, and a wrong turn breaks the listing.

Return ONLY a JSON object, no markdown fences, one entry per photo:
{{"photos": [{{"photo": 1, "upright": "A"}}]}}
where "upright" is "A", "B", "C", "D" or "none".
"""


def _enabled() -> bool:
    flag = os.getenv("AUTO_ORIENT", "on").strip().lower()
    return flag not in ("off", "0", "false", "no") and config.anthropic_ready()


def _model() -> str:
    """The identify model unless ORIENT_MODEL says otherwise. The pass this
    replaced ran on the smallest model to keep a batch cheap; the identify
    pass already spends several times more per photo on this one, and a
    turn it gets wrong costs a seller far more than the difference."""
    return os.getenv("ORIENT_MODEL", "").strip() or config.VISION_MODEL


def _is_true(value) -> bool:
    return value is True or str(value).strip().lower() == "true"


# The affirmatives a model writes when it means yes. Deliberately NOT folded
# into _is_true, which reads the "sure" flag on a proposed TURN: there a loose
# reading applies a rotation nobody vouched for, and a photo turned wrongly is
# a listing that looks broken. Here the asymmetry runs the other way — see
# _art — so "yes" and 1 have to count.
_AFFIRMATIVE = frozenset({"true", "yes", "y", "1"})


def _says_yes(value) -> bool:
    return value is True or str(value).strip().lower() in _AFFIRMATIVE


def _image_block(data: bytes) -> dict:
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.standard_b64encode(data).decode("ascii")}}


def _ask(content: list[dict], max_tokens: int) -> dict:
    """One vision call, answered as the JSON object it was asked for. The one
    place this module touches the SDK — imported here rather than at module
    scope, so the rules below stay importable, and testable, without an LLM
    client: the photo gate in CI runs on Pillow alone."""
    from . import claude_ai
    resp = claude_ai.client().with_options(
        timeout=_CALL_TIMEOUT, max_retries=0).messages.create(
        model=_model(), max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in resp.content if b.type == "text")
    return claude_ai.extract_json(text)


# --- the screen ---------------------------------------------------------------

def _screen_batch(batch: list[Path]) -> Screened:
    """What the screen makes of one batch: the turns it proposes, and the
    photos it says are close-ups. _NOTHING on any failure — these photos stay
    as shot, and are cut out as usual."""
    from . import images
    # A file that cannot be read — truncated past what Pillow forgives, or
    # not an image at all — is skipped by the photo pass anyway. It costs
    # itself its turn, not the seven photos beside it: the ones that could
    # be read are sent, numbered as sent.
    sent: list[Path] = []
    content: list[dict] = []
    for path in batch:
        try:
            data = images.thumb_jpeg(path, side=_SCREEN_SIDE, quality=80)
        except Exception as exc:  # noqa: BLE001 - the pass will report it
            log.info("auto-orient: %s not read (%s)", path.name, exc)
            continue
        sent.append(path)
        content.append({"type": "text", "text": f"Photo {len(sent)}:"})
        content.append(_image_block(data))
    if not sent:
        return _NOTHING
    try:
        content.append({"type": "text",
                        "text": _SCREEN_RULES.format(n=len(sent))})
        # ~80 tokens per entry with the item named; headroom so the answer
        # for the last photo is never the one that gets cut off.
        answer = _ask(content, max_tokens=200 + 120 * len(sent))
    except Exception as exc:  # noqa: BLE001 - orientation is an enhancement
        log.info("auto-orient: screen batch skipped (%s)", exc)
        return _NOTHING
    return Screened(_proposals(answer, sent), _details(answer, sent),
                    _art(answer, sent))


def _proposals(data: dict, batch: list[Path]) -> dict[str, int]:
    """The turns the screen's answer proposes, after the rules that do not
    depend on the model's judgement: a turn it is not sure of is no turn; an
    item laid flat or a detail close-up can only be turned on the strength of
    readable text; and text that already reads the right way up settles it
    the other way — the photo is upright, whatever the model made of the
    item."""
    out: dict[str, int] = {}
    for entry in (data.get("photos") or []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("photo", 0)) - 1
            deg = int(entry.get("rotate", 0)) % 360
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(batch)) or deg not in _VALID or not deg:
            continue
        if not _is_true(entry.get("sure")):
            continue
        sits = str(entry.get("sits", "")).strip().lower()
        text = str(entry.get("text", "none")).strip().lower() or "none"
        if sits not in _TURNS_ON_ITS_OWN and text == "none":
            continue
        if text == "upright":
            continue
        out[batch[idx].name] = deg
        log.info("auto-orient: %s — %d° proposed (%s; %s; text %s)",
                 batch[idx].name, deg, entry.get("item", "?"),
                 sits or "?", text)
    return out


def _details(data: dict, batch: list[Path]) -> frozenset[str]:
    """The photos in the batch that are CLOSE-UPS OF PART OF AN ITEM.

    The screen has always been asked this — "detail": a label, a tag, a
    stitch, a mark, a texture, a logo without the rest of the item in view —
    and has always thrown the answer away after using it to refuse a turn.
    The cutout needs it, because on one of these photos the cutout has nothing
    to do and no way to know that.

    The report, with a screenshot: a vintage Hawaiian shirt, four photos,
    cutouts on. The two whole-garment shots came out right. The two close-ups
    of its tags came back as fragments of a label floating on white — the
    brand tag torn in half, the care label with a bite out of it and a smear
    of the shirt left beside it. The draft that followed named a brand that
    does not exist, because the identify pass reads the OPTIMIZED photos and
    the tag it had to read had been deleted.

    That is the salient-object model working exactly as built. Handed a photo
    whose every pixel is the item — a label filling the frame, the garment
    behind it — it still answers the only question it knows, "which part of
    this is the subject", and dutifully deletes the rest. The rest was the
    shirt.

    Nothing downstream can catch it. The three guards in services/images all
    read the ALPHA, and on a photo like this the alpha is beyond reproach: the
    matte of that torn label is one connected region, opaque throughout, and
    fills its own bounding box — coverage 0.18, solidity 1.00, largest region
    0.82, box fill 0.84. It is arithmetically indistinguishable from a perfect
    cutout of a framed picture. The information that separates them is not in
    the matte at all; it is in the photo, and the only thing that ever looks
    at the photo before the cutout runs is this pass.

    So it is answered here, where it is already being asked and already paid
    for, and the cutout is simply not run on these photos.

    Unconfirmed, unlike a turn, and deliberately so: the two errors are not
    equal and do not need the same evidence. A whole-item shot wrongly called
    a close-up keeps its background, which costs one photo an opt-in feature
    and is what this file does with every photo it cannot answer for anyway.
    A close-up missed here is exactly today's behaviour — the guards get their
    turn, same as before. Neither is worth a second call.
    """
    out = set()
    for entry in (data.get("photos") or []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("photo", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(batch)):
            continue
        if str(entry.get("sits", "")).strip().lower() != _DETAIL:
            continue
        out.add(batch[idx].name)
        log.info("auto-orient: %s — a close-up of %s; the cutout will be "
                 "skipped for it", batch[idx].name, entry.get("item", "?"))
    return frozenset(out)


def _art(data: dict, batch: list[Path]) -> frozenset[str]:
    """The photos in the batch whose ITEM IS A PICTURE.

    The report, with a screenshot: a Marcia Alpert gouache, "Baby in a
    Basket". The listing came back with the baby cut out of the painting --
    lifted off the teal water and the patchwork quilt it is painted on,
    floating on white. Another photo kept the turtle and the signature and
    deleted everything else. The seller's word for it was "horrifically bad",
    and it is: the item they are selling had been erased from the photos of
    it.

    That is the same failure as a close-up of a tag (see _details), one step
    worse. A salient-object model handed a photograph OF A PICTURE answers the
    only question it knows -- which part of this is the subject -- and for a
    painting there is no honest answer. It finds the baby, or the tree, or the
    boat, and deletes the artwork around it.

    And nothing downstream can catch it. Every guard in services/images reads
    the ALPHA: is it one connected region, does it fill its own bounding box,
    is it solid through the middle. A baby lifted out of a painting is all
    three. It is arithmetically indistinguishable from a perfect cutout of a
    figurine, because the thing that separates them is not in the matte -- it
    is the fact that the item is itself an image. The only pass that ever
    looks at the photo before the cutout runs is this one.

    So it is answered here, in the call already being made and already paid
    for, and the cutout takes a different path entirely: the picture's outer
    border, kept whole, with the model never asked (services/artwork,
    images.art_cutout).

    Unconfirmed, like the close-ups and for the same reason -- the two errors
    are wildly unequal. Something wrongly called a picture is cut to its own
    outer rectangle instead of to its silhouette, so it keeps a margin of
    background: a slightly worse cutout of an intact item. A picture MISSED
    here is a painting with a hole in it, on a listing, silently. Nothing
    about that trade is close, which is also why the rule above tells the
    model to answer true when it is torn.
    """
    out = set()
    for entry in (data.get("photos") or []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("photo", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(batch)):
            continue
        if not _says_yes(entry.get("art")):
            continue
        out.add(batch[idx].name)
        log.info("auto-orient: %s — a picture (%s); it will be cut to its own "
                 "border, not by the model", batch[idx].name,
                 entry.get("item", "?"))
    return frozenset(out)


# --- the confirm --------------------------------------------------------------

def _candidate_order(k: int) -> tuple[int, ...]:
    """The four quarter-turns in the order they are lettered A-D for the k-th
    photo of a confirm call. Rotated by k, so the version as shot is not
    always A and the proposal not always the same letter: a model that
    favours a position then scatters its errors instead of stacking them on
    one answer."""
    return tuple(90 * ((k + j) % 4) for j in range(4))


def _confirm_batch(proposals: list[tuple[Path, int]]) -> dict[str, int]:
    """Second, independent look at each proposed turn: the photo at all four
    quarter-turns, and the model must pick the upright one. Only a pick that
    IS the proposal survives — a different pick, "none", or no answer cancels
    it. {} on any failure (= apply nothing from this batch)."""
    from . import images
    orders: list[tuple[int, ...]] = []
    try:
        content: list[dict] = []
        for k, (path, _deg) in enumerate(proposals):
            turns = images.quarter_turns_jpeg(path, side=_CONFIRM_SIDE)
            order = _candidate_order(k)
            orders.append(order)
            for letter, deg in zip(_LETTERS, order):
                content.append({"type": "text",
                                "text": f"Photo {k + 1}, version {letter}:"})
                content.append(_image_block(turns[deg]))
        content.append({"type": "text",
                        "text": _CONFIRM_RULES.format(n=len(proposals))})
        data = _ask(content, max_tokens=100 + 40 * len(proposals))
    except Exception as exc:  # noqa: BLE001 - no confirmation = no rotation
        log.info("auto-orient: confirm batch failed (%s) — applying nothing", exc)
        return {}
    return _confirmed(data, proposals, orders)


def _confirmed(data: dict, proposals: list[tuple[Path, int]],
               orders: list[tuple[int, ...]]) -> dict[str, int]:
    picks: dict[int, str] = {}
    for entry in (data.get("photos") or []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        try:
            picks[int(entry.get("photo", 0)) - 1] = \
                str(entry.get("upright", "")).strip().upper()
        except (TypeError, ValueError):
            continue
    out: dict[str, int] = {}
    for i, (path, deg) in enumerate(proposals):
        pick = picks.get(i, "")
        picked = orders[i][_LETTERS.index(pick)] if pick in _LETTERS else None
        if picked == deg:
            out[path.name] = deg
        else:
            log.info("auto-orient: %s — %d° cancelled (the second look "
                     "picked %s)", path.name, deg,
                     f"{picked}°" if picked is not None else "none")
    return out


# --- the pass -----------------------------------------------------------------

def _budget_for(photos: int) -> float:
    """Seconds the whole pass may take for a pile this size: the floor, or
    the per-photo allowance for a pile big enough to need more."""
    return max(_TOTAL_BUDGET, _PER_PHOTO * photos)


def detect_rotations(paths: list[Path],
                     should_stop: Optional[Callable[[], bool]] = None
                     ) -> dict[str, int]:
    """{filename: clockwise degrees needed} for the photos whose ITEM lies
    sideways or on its head — `screen`'s turns on their own, for callers that
    want nothing else."""
    return screen(paths, should_stop=should_stop).rotations


def screen(paths: list[Path],
           should_stop: Optional[Callable[[], bool]] = None) -> Screened:
    """One look at `paths`: which photos need turning, and which are close-ups
    of part of an item rather than photos of the whole thing.

    Photos already upright, and photos the pass could not answer for, are
    simply absent from both halves. Never raises.

    `should_stop`, asked before each call, lets a cancelled batch stop paying
    for a pass it will never use. Names are what the answer is keyed by, so
    `paths` must come from one directory."""
    files = [p for p in paths if p.is_file()]
    if not files or not _enabled():
        return _NOTHING
    deadline = time.monotonic() + _budget_for(len(files))

    def _guarded(fn, empty):
        """Skip a call once the pass has spent its budget or the batch was
        called off. The photos in it stay as shot, which is the right
        answer for an enhancement: an upload must not wait on orientation,
        and a batch of 250 photos should not be able to spend ten minutes
        deciding which way up they are."""
        def _call(batch):
            if time.monotonic() > deadline:
                return empty
            if should_stop is not None and should_stop():
                return empty
            return fn(batch)
        return _call

    batches = [files[i:i + _SCREEN_BATCH]
               for i in range(0, len(files), _SCREEN_BATCH)]
    proposed: dict[str, int] = {}
    details: set[str] = set()
    art: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(batches))) as pool:
        for part in pool.map(_guarded(_screen_batch, _NOTHING), batches):
            proposed.update(part.rotations)
            details.update(part.details)
            art.update(part.art)
    if details:
        log.info("auto-orient: %d of %d photo(s) are close-ups — those keep "
                 "their background", len(details), len(files))
    if art:
        log.info("auto-orient: %d of %d photo(s) are pictures — those are cut "
                 "to their own border, never by the model", len(art), len(files))
    # The turns still have to survive the second look; the close-ups and the
    # pictures are already final and are carried through whatever it says.
    if not proposed:
        log.info("auto-orient: nothing to turn (of %d photos)", len(files))
        return Screened({}, frozenset(details), frozenset(art))
    by_name = {p.name: p for p in files}
    items = [(by_name[n], deg) for n, deg in proposed.items() if n in by_name]
    chunks = [items[i:i + _CONFIRM_BATCH]
              for i in range(0, len(items), _CONFIRM_BATCH)]
    rotations: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(chunks))) as pool:
        for part in pool.map(_guarded(_confirm_batch, {}), chunks):
            rotations.update(part)
    log.info("auto-orient: %d proposed, %d confirmed, %d cancelled by the "
             "second look (of %d photos)", len(proposed), len(rotations),
             len(proposed) - len(rotations), len(files))
    return Screened(rotations, frozenset(details), frozenset(art))

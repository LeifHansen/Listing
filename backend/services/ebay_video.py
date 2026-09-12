"""Listing videos: eBay's Media API, and the checks worth running first.

A photo and a video reach eBay by opposite routes, and that is the whole
reason this module exists. Photos are PULLED: the publish hands eBay a
`<PictureURL>` and eBay fetches it from R2 or from /media (services/ebay.py).
There is no `<VideoURL>`. A video has to be PUSHED — created as a resource on
eBay, uploaded byte for byte, moderated by eBay, and only then referenced from
the listing by the id eBay minted:

    POST   {media}/video                 -> 201, Location: .../video/{id}
    POST   {media}/video/{id}/upload     -> 200   (application/octet-stream)
    GET    {media}/video/{id}            -> status: PENDING_UPLOAD | PROCESSING
                                                    | LIVE | BLOCKED
                                                    | PROCESSING_FAILED
    <Item><VideoDetails><VideoID>{id}</VideoID></VideoDetails></Item>

Three consequences the rest of the app is built around:

  * It needs a connected eBay account. Photos can be added to a draft by
    someone who has never linked eBay; a video cannot reach eBay until they
    have. So the file is stored locally first and pushed after — a draft is
    never blocked on a connection it may not have yet.

  * eBay MODERATES it, and says so in hours or days, not seconds. Nothing here
    waits for LIVE; the status is recorded and shown, and a listing publishes
    with a video still in PROCESSING exactly as eBay intends.

  * A rejection arrives long after the seller has stopped looking. That is
    what `probe` is for: the size, the container and the duration are the
    three things eBay refuses over that can be checked in a millisecond
    locally, and a refusal now is worth a great deal more than the same
    refusal in 48 hours.

The host is NOT api.ebay.com (see config.EBAY_MEDIA_BASE). The scope is
sell.inventory, which every connected seller already granted — this needs no
reconnect.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional

import httpx

from .. import config
from ..config import log

_TIMEOUT = 30
# The upload itself gets its own, much longer budget: 150MB over a seller's
# connection is minutes, and the 30s above would abandon every real video.
_UPLOAD_TIMEOUT = 600

# eBay's own ceilings, and the reason each is enforced here rather than left
# to eBay:
#
# MAX_VIDEOS_PER_LISTING -- eBay allows ONE video per listing. The plumbing
# either side of this carries a LIST (models.Listing.videos, the <VideoID>
# elements in the Trading request, which the schema does repeat), so the day
# eBay raises the limit this number is the change. It is not a guess dressed
# up as a constant: eBay's seller help says "you can only add one video per
# listing", and a second one is not rejected so much as ignored, which is the
# worst kind of failure -- the seller sees an upload succeed and a listing
# without their video.
MAX_VIDEOS_PER_LISTING = 1
# 150MB exactly (157,286,400 bytes) -- eBay's documented maximum, and the
# `size` createVideo is told has to match the bytes uploaded afterwards.
MAX_VIDEO_BYTES = 150 * 1024 * 1024
# eBay: "videos should be one minute or less". A little slack, because the
# duration read below is the container's and a phone's idea of 60 seconds is
# routinely 60.04 -- refusing those locally would be this app inventing a
# limit eBay does not enforce.
MAX_VIDEO_SECONDS = 62.0
# The only container eBay takes: MP4 (MPEG-4 Part 10 / AVC). A .mov renamed
# to .mp4 is the common way to fail this, which is why `probe` reads the
# file's own brand rather than its extension.
ACCEPTED_CONTENT_TYPES = ("video/mp4",)
ACCEPTED_EXTENSIONS = (".mp4",)


class VideoError(ValueError):
    """A video call failed — carries a sentence for the seller.

    Everything raised out of this module is one of these, so no raw eBay JSON
    and no httpx exception text ever reaches a route.
    """


class VideoNotSupported(VideoError):
    """eBay has not enabled the Media API for this application keyset.

    Kept apart from an ordinary failure because it is not a fault the seller
    can act on and not one a retry fixes: the app's own keyset needs eBay's
    approval. The caller turns it into "eBay isn't accepting videos from this
    app yet" and keeps the file, rather than telling a seller their video was
    bad.
    """


# --- reading the file itself -------------------------------------------------

# MP4 is a tree of boxes, each `[4-byte big-endian length][4-byte type]`. The
# two this cares about are `ftyp` (the container's brand, always first) and
# `moov/mvhd` (the header carrying timescale + duration). Parsing them takes
# about forty lines and no dependency; the alternative is ffprobe, which is a
# binary this image does not ship and would not be worth adding for two
# numbers.
_BOX_HEADER = 8
# An `ftyp` brand belonging to something that is not MP4. QuickTime ("qt  ")
# is the one that actually happens: a Mac or an iPhone hands over a .mov, the
# seller renames it, and eBay rejects it days later without saying why.
_MP4_BRANDS = frozenset({
    b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"avc1", b"mp41", b"mp42",
    b"mp71", b"MSNV", b"M4V ", b"dash", b"f4v ",
})


def _boxes(fh, end: int, start: int = 0):
    """Walk one level of the box tree, yielding (type, payload_start, payload_end)."""
    pos = start
    while pos + _BOX_HEADER <= end:
        fh.seek(pos)
        header = fh.read(_BOX_HEADER)
        if len(header) < _BOX_HEADER:
            return
        size = struct.unpack(">I", header[:4])[0]
        kind = header[4:8]
        body = pos + _BOX_HEADER
        if size == 1:                       # 64-bit size in the next 8 bytes
            raw = fh.read(8)
            if len(raw) < 8:
                return
            size = struct.unpack(">Q", raw)[0]
            body += 8
        elif size == 0:                     # "to the end of the file"
            size = end - pos
        if size < (body - pos):             # malformed — stop rather than loop
            return
        yield kind, body, pos + size
        pos += size


def _mvhd_duration(fh, end: int) -> Optional[float]:
    """Seconds, read from moov/mvhd, or None when the file does not say."""
    for kind, body, stop in _boxes(fh, end):
        if kind != b"moov":
            continue
        for inner, ibody, _istop in _boxes(fh, stop, body):
            if inner != b"mvhd":
                continue
            fh.seek(ibody)
            version = fh.read(1)
            if not version:
                return None
            # version + 3 flag bytes, then the timestamps whose width the
            # version decides.
            fh.seek(ibody + 4 + (16 if version[0] == 1 else 8))
            raw = fh.read(12 if version[0] == 1 else 8)
            try:
                if version[0] == 1:
                    timescale, duration = struct.unpack(">IQ", raw)
                else:
                    timescale, duration = struct.unpack(">II", raw)
            except struct.error:
                return None
            # 0xFFFFFFFF is MP4's "unknown duration"; a zero timescale is a
            # broken file. Either way the honest answer is "not stated".
            if not timescale or duration in (0, 0xFFFFFFFF):
                return None
            return duration / timescale
    return None


def probe(path: Path) -> dict:
    """What can be learned about a video file without asking eBay.

    {"size": bytes, "mp4": bool, "brand": str, "seconds": float|None}. Nothing
    here raises: a file this cannot parse is reported as unparsed and left to
    eBay, because a local reader refusing a video eBay would have accepted is
    the worse error of the two. `check` below is what turns this into a
    refusal, and only over what it positively knows.
    """
    out = {"size": 0, "mp4": False, "brand": "", "seconds": None}
    try:
        out["size"] = path.stat().st_size
        with path.open("rb") as fh:
            end = out["size"]
            for kind, body, stop in _boxes(fh, end):
                if kind != b"ftyp":
                    break               # ftyp is always the first box
                fh.seek(body)
                brand = fh.read(4)
                out["brand"] = brand.decode("latin-1").strip()
                compatible = {brand}
                # The compatible-brands list after major_brand + minor_version
                # is what a file with an odd major brand declares itself by.
                extra = fh.read(max(0, stop - body - 8))
                compatible |= {extra[i:i + 4] for i in range(4, len(extra), 4)}
                out["mp4"] = bool(compatible & _MP4_BRANDS)
                break
            out["seconds"] = _mvhd_duration(fh, end)
    except Exception as exc:  # noqa: BLE001 - a probe must never fail a request
        log.info("video: couldn't read %s (%s)", path.name, exc)
    return out


def check(path: Path) -> Optional[str]:
    """The reason eBay would refuse this file, or None to go ahead.

    Only the three things a local read can be SURE of. Everything else eBay
    judges — content, resolution, whether the item is the thing on screen —
    and this deliberately does not guess at any of it.
    """
    facts = probe(path)
    if not facts["size"]:
        return "That video file is empty."
    if facts["size"] > MAX_VIDEO_BYTES:
        return (f"That video is {facts['size'] / 1e6:.0f}MB — eBay's limit is "
                f"{MAX_VIDEO_BYTES // (1024 * 1024)}MB. Trim it or export it "
                "at a lower quality and try again.")
    # `brand` empty means the file had no readable ftyp box at all — not an
    # MP4 by any reading, and the case a .mov or a .webm lands in.
    if not facts["mp4"]:
        named = f" (this one is “{facts['brand']}”)" if facts["brand"] else ""
        return ("eBay only takes MP4 video" + named + ". Export it as .mp4 "
                "(H.264) and try again.")
    seconds = facts["seconds"]
    if seconds is not None and seconds > MAX_VIDEO_SECONDS:
        return (f"That video is {seconds:.0f} seconds long — eBay's limit is "
                "one minute. Trim it and try again.")
    return None


# --- talking to eBay ---------------------------------------------------------

def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json",
            "Content-Type": "application/json"}


def _reason(resp: httpx.Response) -> str:
    """eBay's own words for a refusal, or the status code when it gave none.

    eBay's REST errors arrive as {"errors": [{message, longMessage, ...}]};
    longMessage is the one written for a person.
    """
    try:
        errs = resp.json().get("errors") or []
    except Exception:  # noqa: BLE001 - not every failure is JSON
        errs = []
    for err in errs:
        text = (err.get("longMessage") or err.get("message") or "").strip()
        if text:
            return text
    return f"eBay returned {resp.status_code}."


def _unavailable(resp: httpx.Response) -> bool:
    """Is this eBay saying the Media API is not open to this app?

    The Media API is not on by default for every keyset, and the refusal is a
    403 (or a 404 on the whole path) rather than anything about the video. A
    seller must not be told their file was rejected when the app was.
    """
    if resp.status_code == 404:
        return True
    if resp.status_code != 403:
        return False
    body = resp.text.lower()
    return "scope" in body or "permission" in body or "not authorized" in body


def create_video(token: str, title: str, size: int,
                 description: str = "") -> str:
    """Reserve a video on eBay and return its id.

    `size` must be the exact byte count uploaded afterwards — eBay matches the
    two and fails the upload if they differ, which is why the caller passes
    the stat of the file it is about to send rather than anything remembered.
    """
    payload = {
        # eBay caps the title at 80 characters and rejects an empty one.
        "title": (title or "Listing video").strip()[:80] or "Listing video",
        "size": int(size),
        # ITEM is what makes the video attachable to a listing; the other
        # classifications are for other parts of eBay entirely.
        "classification": ["ITEM"],
    }
    if description:
        payload["description"] = description.strip()[:1000]
    try:
        resp = httpx.post(f"{config.EBAY_MEDIA_BASE}/video",
                          headers=_headers(token), json=payload,
                          timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - network/timeout
        raise VideoError(f"Couldn't reach eBay: {exc}") from exc
    if resp.status_code not in (200, 201):
        if _unavailable(resp):
            raise VideoNotSupported(
                "eBay isn't accepting videos from this app yet. Your video is "
                "saved here and will go up with the listing once it is.")
        raise VideoError(_reason(resp))
    # eBay answers 201 with the id ONLY in the Location header; the body is
    # empty. A body-first read returns None here and every later call goes to
    # /video/None.
    video_id = (resp.headers.get("location") or "").rstrip("/").rsplit("/", 1)[-1]
    if not video_id:
        try:
            video_id = str((resp.json() or {}).get("videoId") or "")
        except Exception:  # noqa: BLE001 - an empty body is the norm here
            video_id = ""
    if not video_id:
        raise VideoError("eBay accepted the video but didn't say which id it got.")
    return video_id


def upload_video(token: str, video_id: str, path: Path) -> None:
    """Send the bytes. eBay begins processing the moment this returns.

    The file is handed to httpx as an open handle rather than read into
    memory: 150MB on a 4GB box that is also holding a cutout model is not
    somewhere to spend a buffer, and the whole point of streaming it to disk
    on the way in was not to.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "Accept": "application/json",
    }
    try:
        with path.open("rb") as fh:
            resp = httpx.post(
                f"{config.EBAY_MEDIA_BASE}/video/{video_id}/upload",
                headers=headers, content=fh, timeout=_UPLOAD_TIMEOUT)
    except OSError as exc:
        raise VideoError("That video file is no longer on the server — "
                         "add it again.") from exc
    except Exception as exc:  # noqa: BLE001 - network/timeout
        raise VideoError(f"Couldn't send the video to eBay: {exc}") from exc
    if resp.status_code not in (200, 201, 204):
        if _unavailable(resp):
            raise VideoNotSupported(
                "eBay isn't accepting videos from this app yet. Your video is "
                "saved here and will go up with the listing once it is.")
        raise VideoError(_reason(resp))


def get_video(token: str, video_id: str) -> dict:
    """Where eBay has got to with a video.

    {"status": one of the STATUS_* values below, "message": eBay's reason,
    "play_url": ""}. A status this does not recognise comes back as it
    arrived rather than being mapped to something friendlier — a state nobody
    anticipated must not read as a state somebody did.
    """
    try:
        resp = httpx.get(f"{config.EBAY_MEDIA_BASE}/video/{video_id}",
                         headers=_headers(token), timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - network/timeout
        raise VideoError(f"Couldn't reach eBay: {exc}") from exc
    if resp.status_code != 200:
        if _unavailable(resp):
            raise VideoNotSupported("eBay isn't accepting videos from this app yet.")
        raise VideoError(_reason(resp))
    try:
        data = resp.json() or {}
    except Exception as exc:  # noqa: BLE001 - a 200 that is not JSON
        raise VideoError("eBay's answer about that video couldn't be read.") from exc
    play = ""
    for entry in data.get("playList") or []:
        play = (entry or {}).get("playUrl") or ""
        if play:
            break
    return {"status": str(data.get("status") or "").strip().upper(),
            "message": str(data.get("statusMessage") or "").strip(),
            "play_url": play}


# eBay's own status vocabulary, held here so the model, the routes and the
# editor all read the same words. PENDING_UPLOAD is the gap between create
# and upload; LIVE, BLOCKED and PROCESSING_FAILED are terminal.
STATUS_PENDING = "PENDING_UPLOAD"
STATUS_PROCESSING = "PROCESSING"
STATUS_LIVE = "LIVE"
STATUS_BLOCKED = "BLOCKED"
STATUS_FAILED = "PROCESSING_FAILED"
TERMINAL_STATUSES = frozenset({STATUS_LIVE, STATUS_BLOCKED, STATUS_FAILED})
# The statuses a listing may carry the video's id under. eBay attaches a
# video that is still PROCESSING and shows it once moderation clears — which
# is the documented behaviour and the reason a publish never waits. Only the
# two refusals are held back, because sending one of those is a rejection of
# the whole publish rather than a listing without a video.
SENDABLE_STATUSES = frozenset({STATUS_PENDING, STATUS_PROCESSING, STATUS_LIVE})


def send_to_ebay(token: str, path: Path, title: str = "") -> dict:
    """Create + upload in one go, and report where eBay got to.

    Returns {"video_id", "status", "message"}. The status is read back rather
    than assumed: an upload that returns 200 has only started the processing,
    and the difference between "eBay is looking at it" and "eBay refused it"
    is the whole of what the seller wants to know.

    A failure AFTER the create leaves a video resource on eBay with no bytes
    in it. That is deliberately not cleaned up: it is attached to nothing,
    eBay expires it on its own, and a delete call on the failure path is one
    more thing that can fail while the seller waits.
    """
    size = path.stat().st_size
    video_id = create_video(token, title or path.stem, size)
    upload_video(token, video_id, path)
    try:
        state = get_video(token, video_id)
    except VideoError as exc:
        # The bytes are on eBay; only the status read failed. Reporting that
        # as a failed upload would have the seller send 150MB again.
        log.info("video %s uploaded; status read failed: %s", video_id, exc)
        state = {"status": STATUS_PROCESSING, "message": ""}
    return {"video_id": video_id, "status": state.get("status") or STATUS_PROCESSING,
            "message": state.get("message") or ""}

"""Background removal: Photoroom's cutout of a photo, as an RGBA image.

One engine, one shape. It takes a PIL image and returns Photoroom's cutout as
RGBA -- the alpha is the matte -- or None when no key is configured. A
configured engine that FAILS raises PhotoroomError with the actual reason in
the message: there is no local model to fall back to any more, and a silent
"kept as shot" with no reason is how a seller ends up wondering why the
backgrounds stopped coming off.

    photoroom  Photoroom's Remove Background API (the "Basic" plan),
               `POST https://sdk.photoroom.com/v1/segment`. One multipart
               upload in, one transparent PNG out, around $0.02 a photo.
               Authentication is a single `x-api-key` header carrying the
               app's own key (PHOTOROOM_API_KEY): Photoroom has no API for
               creating accounts or keys on a seller's behalf, so the app owns
               one account and each seller switches the feature on for
               themselves (services/bg_removal.py) instead.

THE WHITE CANVAS IS STILL OURS. Photoroom can composite onto a colour and
draw a shadow itself, but those are Image Editing API ("Plus") features at
about five times the price. We ask for the matte alone and put it on white
with our own contact shadow (images.compose), the same as every
engine before this one did.

SANDBOX KEYS. A key that starts with `sandbox_` is free and works, but every
cutout carries a Photoroom watermark. config.PHOTOROOM_SANDBOX names it, the
boot log warns about it and /api/ready says so, because a watermarked listing
photo that reaches eBay is worse than no cutout at all.

LIMITS WORTH KNOWING. A live key answers 402 until the account has an API plan
(or after the plan's images are spent); that is a fact about the account and
is never retried. A 429 is a rate limit and retries with backoff, as does a
5xx and a network blip.

Errors subclass ValueError on purpose: the studio route already maps
ValueError to a 422 carrying the message, so the seller reads the real cause
instead of watching the cutout silently not happen.

httpx is imported INSIDE the call, not at module scope. The `cutout` CI job
runs the photo pass on Pillow, and a module-level import would make every test
that merely reaches services/images depend on the HTTP stack.

No key is ever logged, and no exception MESSAGE is either: httpx puts the
request URL in its messages. The type name and the HTTP status are enough to
debug with and safe to write down.

THE OPERATOR READS THE REAL REASON. A seller reads UNAVAILABLE for a refused
key and for an empty account alike, because neither is theirs to fix. The
operator can fix both, and health() is where they read which one it was --
see "what the operator reads" below.
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
import time
from io import BytesIO
from typing import Callable, Optional

from PIL import Image

from .. import config
from ..config import log

# Cap what we send: the final output is always 1600px, so a full 4032px
# working copy just wastes upload time and RAM -- and on a 250-photo batch
# those add up to minutes and hundreds of MB. 2048px keeps a comfortable
# margin above the output size.
_MAX_SIDE = int(os.getenv("BG_API_MAX_SIDE", "2048") or 2048)

# Total attempts per photo. Big batches WILL trip a per-minute rate limit and
# the odd network blip; those retry with backoff instead of failing the photo.
# Hard failures (bad key, no plan, out of images) never retry -- they would
# fail identically every time. At least one: zero would make no request at
# all and then read the response that never came.
_TRIES = max(1, int(os.getenv("BG_API_TRIES", "4") or 4))

# One cutout answers in a second or three; 30s is generous for a slow day.
# The studio's client deadline (frontend MODEL_TIMEOUT_MS) is sized to outlast
# _TRIES of these plus the backoff between them -- keep the two in step.
_TIMEOUT = float(os.getenv("BG_API_TIMEOUT", "30") or 30)

# How much smaller than what we sent a result may be before we call it a
# preview. A cutout a fraction of the photo's size pasted into a 1600px
# listing looks like a bug in this app rather than a limit on the account.
_PREVIEW_RATIO = 0.6

SEGMENT_URL = "https://sdk.photoroom.com/v1/segment"

# What a seller reads when the problem is the app's account, not their photo.
# The operator-facing detail goes to the log instead.
UNAVAILABLE = ("Background removal is unavailable right now — the photo was "
               "kept as shot. Try again later.")
# ...and when the app's month of Photoroom is spent (PHOTOROOM_MONTHLY_BUDGET_USD).
BUDGET_SPENT = ("Cloud background removal is paused until next month — the "
                "photo was kept as shot.")


class CutoutApiError(ValueError):
    """The engine failed, with a seller-readable reason.

    ValueError so the studio route's existing mapping turns it into a 422 plus
    the message, instead of a 500 or a silent downgrade.
    """


class PhotoroomError(CutoutApiError):
    """Photoroom is configured but the cutout could not be had."""


class NothingFound(PhotoroomError):
    """Photoroom answered, and kept nothing. Not a fault in the account or
    the network: a statement about the photo, which is why the photo pass may
    still try the item's own rectangular outline (images._rectangle_cut)
    where it would not for an outage."""


# --- what the operator reads -------------------------------------------------
#
# The log used to be the only place that said whether a batch came back with
# its backgrounds on because the key was refused, the account was out of
# images, or Photoroom was down -- and Fly's log is a stream that has rolled
# past the batch by the time anyone asks. So the engine keeps the last thing
# that went wrong with the SERVICE, for /api/admin/diagnostics.
#
# The service, not the photo. A photo Photoroom answered for and found nothing
# in (NothingFound) proves the key, the account and the network all work, so
# it counts as a success here. A 400 is about one photo too, but it is kept:
# a 400 on every photo is a request format that changed under us.
#
# In memory, like objstore's latch: the diagnostics endpoint has to answer
# with the database down, production is one process, and a restart clearing
# the record is honest -- the next cutout that fails writes it again.

_health_lock = threading.Lock()
_last_failure: Optional[dict] = None
_last_success_at: Optional[str] = None
_failures_since_success = 0


def _stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _failed(kind: str, reason: str, status: Optional[int] = None,
            detail: str = "") -> None:
    """Remember why the engine just failed. `reason` is the operator's
    sentence; `detail` is Photoroom's own words, from _short_reason or an
    exception's type name, so never a key or a URL."""
    global _last_failure, _failures_since_success
    with _health_lock:
        _last_failure = {"kind": kind, "reason": reason, "status": status,
                         "detail": detail, "at": _stamp()}
        _failures_since_success += 1


def _succeeded() -> None:
    global _last_success_at, _failures_since_success
    with _health_lock:
        _last_success_at = _stamp()
        _failures_since_success = 0


def health() -> dict:
    """Whether Photoroom is working, and if not, why -- for the operator.

    `failing` is whether the most recent call failed. `last_failure` is kept
    after a success, so a fault that has since cleared can still be read;
    compare its `at` with `last_success_at`. Since the last restart only.

    Never on /api/ready, which anyone can read: `detail` is Photoroom's own
    words about the app's account.
    """
    with _health_lock:
        failure = dict(_last_failure) if _last_failure else None
        success, streak = _last_success_at, _failures_since_success
    return {"configured": config.photoroom_ready(),
            "sandbox": config.PHOTOROOM_SANDBOX,
            "failing": streak > 0,
            "failures_since_success": streak,
            "last_failure": failure,
            "last_success_at": success}


# --- what the app spends -----------------------------------------------------
#
# Every Photoroom call is counted, app-wide, in db.cutout_usage under
# ("photoroom", "server") -- the old app builds' uploads, the studio, the
# cloud fallback a device asks for, and the call that only LOCATES a
# picture's edge alike, because Photoroom bills them all the same. The count
# is reserved before the call and given back when Photoroom answered anything
# but a 200 (it charges only for successful calls), and with
# PHOTOROOM_MONTHLY_BUDGET_USD set it is also the cap: a call that would pass
# the month's budget is not made.
#
# Imported lazily and failing open: the `cutout` CI job runs this module on
# Pillow alone, with no database stack, and a counter that cannot be reached
# must not stop a seller's cutout (the per-seller count in bg_removal is the
# backstop that still holds then, as it does for the token charge).

_PROVIDER = "photoroom"
_PLATFORM = "server"


def _period() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")


def _count(photos: int, cap: Optional[int] = None) -> Optional[dict]:
    try:
        from .. import db
    except Exception:  # noqa: BLE001 - no database stack here
        return None
    return db.cutout_usage_add(_period(), _PROVIDER, _PLATFORM, photos, cap=cap)


def _reserve_one() -> bool:
    """Count one call against the month, or raise PhotoroomError when the
    budget is spent. True when something was counted (so must be given back
    on failure)."""
    cap = config.photoroom_budget_photos()
    res = _count(1, cap=cap)
    if res is None:
        return False
    if not res.get("ok", True):
        _failed("budget_spent",
                "PHOTOROOM_MONTHLY_BUDGET_USD is spent for this month "
                f"({res.get('used', 0)} photos at "
                f"${config.PHOTOROOM_COST_PER_IMAGE:.3f}) — cloud cutouts are "
                "paused until the month turns.")
        raise PhotoroomError(BUDGET_SPENT)
    return True


def budget_exhausted() -> bool:
    """Whether the month's Photoroom budget is spent. False with no budget,
    no database, or a count that cannot be read."""
    cap = config.photoroom_budget_photos()
    if cap is None:
        return False
    try:
        from .. import db
        rows = db.cutout_usage(_period())
    except Exception:  # noqa: BLE001
        return False
    if not rows:
        return cap <= 0
    used = sum(r["photos"] for r in rows
               if r["provider"] == _PROVIDER and r["platform"] == _PLATFORM)
    return used >= cap


# At most PHOTOROOM_MAX_CONCURRENCY calls in flight app-wide. Taken around
# each ATTEMPT, not around the retries: a call waiting out a back-off holds no
# slot, so one rate-limited photo cannot starve every other caller.
_slots = threading.BoundedSemaphore(config.PHOTOROOM_MAX_CONCURRENCY)


def _in_slot(send: Callable):
    with _slots:
        return send()


# --- HTTP plumbing ----------------------------------------------------------

def _backoff(resp, attempt: int) -> float:
    """Seconds to wait before retrying: the service's own Retry-After when it
    sends one, else 2s/4s/8s..., capped so a stuck batch photo can't stall a
    worker slot for long."""
    retry_after = ""
    if resp is not None:
        retry_after = (resp.headers.get("retry-after") or "").strip()
    if retry_after.isdigit():
        return min(30.0, max(1.0, float(retry_after)))
    return min(15.0, float(2 ** attempt))


def _send_with_retries(send: Callable):
    """Run `send()` (an httpx call) with retries on 429/5xx/network errors.
    Returns the final response; raises PhotoroomError when Photoroom stayed
    unreachable through every attempt.

    Only 429s and 5xx retry. A 401, 402 or 403 is a fact about the account,
    not a blip, and retrying it three more times just spends three more
    seconds arriving at the same answer.
    """
    resp = None
    for attempt in range(1, _TRIES + 1):
        try:
            resp = send()
        except PhotoroomError:
            raise
        except Exception as exc:  # noqa: BLE001 - network/timeout
            if attempt == _TRIES:
                # The TYPE only, never str(exc): httpx puts the request URL in
                # its messages.
                _failed("unreachable",
                        f"Couldn't reach Photoroom through {_TRIES} attempt(s) "
                        "— an outage at Photoroom or a network problem "
                        "between this server and it.",
                        detail=type(exc).__name__)
                raise PhotoroomError(
                    f"Couldn't reach Photoroom ({type(exc).__name__}) — "
                    "it may be down; the photo was kept as shot.") from exc
            wait = _backoff(None, attempt)
            log.info("Photoroom: network error (%s) — retry %d/%d in %.0fs",
                     type(exc).__name__, attempt, _TRIES - 1, wait)
            time.sleep(wait)
            continue
        retryable = resp.status_code == 429 or resp.status_code >= 500
        if retryable and attempt < _TRIES:
            wait = _backoff(resp, attempt)
            log.info("Photoroom: HTTP %d — retry %d/%d in %.0fs",
                     resp.status_code, attempt, _TRIES - 1, wait)
            time.sleep(wait)
            continue
        break
    return resp


def jpeg_payload(img: Image.Image) -> bytes:
    """The JPEG bytes the engine gets, capped to _MAX_SIDE."""
    rgb = img.convert("RGB")  # always a new image, so safe to shrink in place
    if max(rgb.size) > _MAX_SIDE:
        rgb.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)
    buf = BytesIO()
    rgb.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _decode(body: bytes, sent: Image.Image) -> Image.Image:
    """Photoroom's bytes as an RGBA cutout, or PhotoroomError with the reason.

    Pure: no network, so the tests that matter here need no httpx.
    """
    try:
        cut = Image.open(BytesIO(body)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - whatever came back isn't an image
        raise PhotoroomError("Photoroom returned an unreadable image.") from exc
    if cut.split()[3].getbbox() is None:
        raise NothingFound("Photoroom couldn't find an item in this photo — "
                           "it was kept as shot.")
    _check_not_preview(cut, sent)
    return cut


def _check_not_preview(cut: Image.Image, sent: Image.Image) -> None:
    """Refuse a result that came back far smaller than what we sent.

    Shipping that would upscale a small cutout into a 1600px listing photo,
    which reads as this app being broken. Better to say what came back.
    """
    if max(sent.size) <= 0:
        return
    if max(cut.size) < max(sent.size) * _PREVIEW_RATIO:
        raise PhotoroomError(
            f"Photoroom returned a {cut.width}x{cut.height} preview instead of "
            "the full-size cutout. The photo was kept as shot.")


def _short_reason(resp) -> str:
    """A trimmed, credential-free reason out of an error body.

    Never the request URL, and never more than a sentence of somebody else's
    JSON -- enough to tell two failures apart in a log.
    """
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 - not JSON, fall through to the text
        return (resp.text or "")[:160]
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("message") or value.get("detail")
            if value:
                return str(value)[:160]
    return (resp.text or "")[:160]


# --- the engine -------------------------------------------------------------

def _segment(payload: bytes):
    """One POST to Photoroom's segment endpoint. Separate so the external
    write classification has exactly one function to name."""
    import httpx

    return httpx.post(
        SEGMENT_URL,
        headers={"x-api-key": config.PHOTOROOM_API_KEY},
        files={"image_file": ("photo.jpg", payload, "image/jpeg")},
        data={"format": "png"},
        timeout=_TIMEOUT,
    )


def photoroom_cutout(img: Image.Image) -> Optional[Image.Image]:
    """The subject cut out by Photoroom, as RGBA. None when no key is set.

    Raises PhotoroomError, with a sentence a seller can act on, for every
    other failure: a refused key, an account with no plan or no images left,
    a rate limit that outlasted the retries, a photo it would not take, or an
    answer with nothing kept in it.
    """
    if not config.photoroom_ready():
        return None
    payload = jpeg_payload(img)
    sent = Image.open(BytesIO(payload))
    counted = _reserve_one()
    try:
        resp = _send_with_retries(lambda: _in_slot(lambda: _segment(payload)))
    except BaseException:
        if counted:
            _count(-1)
        raise
    status = resp.status_code
    if counted and status != 200:
        # Photoroom bills successful calls only.
        _count(-1)
    said = _short_reason(resp) if status != 200 else ""
    if status in (401, 403):
        log.error("photoroom: HTTP %d — the PHOTOROOM_API_KEY was refused "
                  "(missing, mistyped or revoked): %s", status, said)
        _failed("key_refused",
                "Photoroom refused the API key. PHOTOROOM_API_KEY is "
                "missing, mistyped or revoked — compare it with the key in "
                "the Photoroom API dashboard.", status, said)
        raise PhotoroomError(UNAVAILABLE)
    if status == 402:
        log.error("photoroom: HTTP 402 — the Photoroom account has no API "
                  "plan or has used its images: %s", said)
        _failed("out_of_credits",
                "The Photoroom account has no API plan, or has used all of "
                "its images — renew or top up the plan in the Photoroom API "
                "dashboard.", status, said)
        raise PhotoroomError(UNAVAILABLE)
    if status == 429:
        _failed("rate_limited",
                f"Photoroom kept rate-limiting through {_TRIES} attempt(s).",
                status, said)
        raise PhotoroomError(
            "Background removal is busy — Photoroom kept rate-limiting us "
            "even after several retries. Try again in a minute.")
    if status == 400:
        log.info("photoroom: HTTP 400: %s", said)
        _failed("photo_refused",
                "Photoroom refused the photo. On one photo that is the photo; "
                "on every photo the request format no longer matches its "
                "API.", status, said)
        raise PhotoroomError(
            "Photoroom couldn't process this photo — it was kept as shot.")
    if status != 200:
        log.warning("photoroom: HTTP %d: %s", status, said)
        _failed("service_error", f"Photoroom answered HTTP {status}.",
                status, said)
        raise PhotoroomError(
            f"Photoroom error {status} — the photo was kept as shot.")
    try:
        cut = _decode(resp.content, sent)
    except NothingFound:
        # Photoroom answered properly about this photo: the service works.
        _succeeded()
        raise
    except PhotoroomError as exc:
        # A 200 that wasn't a usable cutout: unreadable, or a preview.
        _failed("bad_answer", str(exc), status)
        raise
    _succeeded()
    return cut


def engine_state() -> dict:
    """What /api/ready says about the engine: which one, whether it has a
    key, and whether that key only produces watermarked previews."""
    return {"engine": "photoroom", "ready": config.photoroom_ready(),
            "sandbox": config.PHOTOROOM_SANDBOX}

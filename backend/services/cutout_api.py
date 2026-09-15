"""Remote background removal: the cutout a paid API produces, as an RGBA image.

Two engines, one shape. Each takes a PIL image and returns the service's cutout
as RGBA — the alpha is the matte — or None when that engine has no credentials.
A configured engine that FAILS raises, with the actual reason in the message,
because a silent fall back to the weak local model is exactly how mangled
photos kept getting saved without anyone knowing why.

    removebg   remove.bg's API. What the seller asked for by name. Owned by
               Canva, and its standalone API SHUTS DOWN 2026-12-01: after that
               date this engine stops working and leonardo is the successor
               remove.bg itself names. Free keys are capped at `preview` size
               (0.25MP / 625x400), well under this app's 1600px output, so a
               free key is for wiring the integration up, not for serving
               sellers -- _check_not_preview says so out loud rather than
               letting a 625px cutout reach a listing.
    leonardo   Leonardo.Ai, remove.bg's named successor. ~$0.10/image.

Both errors subclass ValueError on purpose: the studio route already maps
ValueError to a 422 carrying the message, so the seller reads the real cause
("out of credits") instead of watching the cutout silently get worse.

httpx is imported INSIDE each call, not at module scope. The `cutout` CI job
installs Pillow and pytest and nothing else, so a module-level import would
break every test that merely reaches services/images. It is also how the
deleted Pixian/Photoroom engines did it.

No key is ever logged, and no exception MESSAGE is either: httpx puts the
request URL in its messages and the URL can carry credentials. The type name
and the HTTP status are enough to debug with and safe to write down.
"""
from __future__ import annotations

import os
import time
from io import BytesIO
from typing import Callable, Optional

from PIL import Image

from .. import config
from ..config import log

# Cap what we send a remote engine: the final output is always 1600px, so a
# full 4032px working copy just wastes upload time and RAM -- and on a
# 250-photo batch those add up to minutes and hundreds of MB. 2048px keeps a
# comfortable margin above the output size.
_MAX_SIDE = int(os.getenv("BG_API_MAX_SIDE", "2048") or 2048)

# Total attempts per photo. Big batches WILL trip an API's per-minute rate
# limit and the odd network blip; those retry with backoff instead of failing
# the photo. Hard failures (bad key, out of credits) never retry -- they would
# fail identically every time.
_TRIES = int(os.getenv("BG_API_TRIES", "4") or 4)

# One cutout answers in a second or three. 30s is generous for a slow day and
# still far inside the studio's own budget.
_TIMEOUT = float(os.getenv("BG_API_TIMEOUT", "30") or 30)

# How much smaller than what we sent a result may be before we call it a
# preview. remove.bg's free tier silently returns 0.25MP whatever you ask for,
# and a 625px cutout pasted into a 1600px listing looks like a bug in this app
# rather than a plan limit on the key.
_PREVIEW_RATIO = 0.6


class CutoutApiError(ValueError):
    """A configured remote engine failed, with a seller-readable reason.

    ValueError so the studio route's existing mapping turns it into a 422 plus
    the message, instead of a 500 or a silent downgrade.
    """


class RemoveBgError(CutoutApiError):
    """remove.bg is configured but the call failed."""


class LeonardoError(CutoutApiError):
    """Leonardo.Ai is configured but the call failed."""


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


def _post_with_retries(name: str, send: Callable, exc_cls: type):
    """Run `send()` (an httpx POST) with retries on 429/5xx/network errors.
    Returns the final response; raises `exc_cls` when the service stayed
    unreachable through every attempt.

    Only 429 and 5xx retry. A 402 or 403 is a fact about the account, not a
    blip, and retrying it three more times just spends three more seconds
    arriving at the same answer.
    """
    resp = None
    for attempt in range(1, _TRIES + 1):
        try:
            resp = send()
        except Exception as exc:  # noqa: BLE001 - network/timeout
            if attempt == _TRIES:
                # The TYPE only, never str(exc): httpx puts the request URL in
                # its messages and the URL can carry the key.
                raise exc_cls(
                    f"Couldn't reach {name} ({type(exc).__name__}) — "
                    "it may be down; the photo was kept as shot.") from exc
            wait = _backoff(None, attempt)
            log.info("%s: network error (%s) — retry %d/%d in %.0fs",
                     name, type(exc).__name__, attempt, _TRIES - 1, wait)
            time.sleep(wait)
            continue
        if (resp.status_code == 429 or resp.status_code >= 500) \
                and attempt < _TRIES:
            wait = _backoff(resp, attempt)
            log.info("%s: HTTP %d — retry %d/%d in %.0fs",
                     name, resp.status_code, attempt, _TRIES - 1, wait)
            time.sleep(wait)
            continue
        break
    return resp


def jpeg_payload(img: Image.Image) -> bytes:
    """The JPEG bytes a remote engine gets, capped to _MAX_SIDE."""
    rgb = img.convert("RGB")
    if max(rgb.size) > _MAX_SIDE:
        rgb = rgb.copy()
        rgb.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)
    buf = BytesIO()
    rgb.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _decode(body: bytes, sent: Image.Image, name: str, exc_cls: type,
            ) -> Image.Image:
    """The service's bytes as an RGBA cutout, or exc_cls with the reason.

    Pure: no network, so the tests that matter here need no httpx.
    """
    try:
        cut = Image.open(BytesIO(body)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - whatever came back isn't an image
        raise exc_cls(f"{name} returned an unreadable image.") from exc
    if cut.split()[3].getbbox() is None:
        raise exc_cls(f"{name} couldn't find an item in this photo — "
                      "it was kept as shot.")
    _check_not_preview(cut, sent, name, exc_cls)
    return cut


def _check_not_preview(cut: Image.Image, sent: Image.Image, name: str,
                       exc_cls: type) -> None:
    """Refuse a result that came back far smaller than what we sent.

    This is remove.bg's free tier: it answers every request at `preview` size
    (0.25MP) no matter what `size` asked for. Shipping that would upscale a
    625px cutout into a 1600px listing photo, which reads as this app being
    broken. Better to say the key is on the free plan.
    """
    if max(sent.size) <= 0:
        return
    if max(cut.size) < max(sent.size) * _PREVIEW_RATIO:
        raise exc_cls(
            f"{name} returned a {cut.width}x{cut.height} preview instead of "
            f"the full-size cutout — that key is on the free plan, which only "
            "returns low-resolution previews. The photo was kept as shot.")


def removebg_cutout(img: Image.Image) -> Optional[Image.Image]:
    """The subject cut out by remove.bg, as RGBA. None when unconfigured."""
    if not config.removebg_ready():
        return None
    import httpx
    sent = img.convert("RGB")
    payload = jpeg_payload(sent)
    resp = _post_with_retries("remove.bg", lambda: httpx.post(
        "https://api.remove.bg/v1.0/removebg",
        headers={"X-Api-Key": config.REMOVEBG_API_KEY},
        files={"image_file": ("image.jpg", payload, "image/jpeg")},
        # size=auto asks for the full resolution the plan allows; format=png
        # because the alpha channel IS the product here.
        data={"size": "auto", "format": "png"},
        timeout=_TIMEOUT,
    ), RemoveBgError)
    if resp.status_code in (401, 403):
        raise RemoveBgError(
            "remove.bg rejected the API key — check the REMOVEBG_API_KEY "
            "secret on the server (it may be missing, mistyped, or expired).")
    if resp.status_code == 402:
        raise RemoveBgError(
            "The remove.bg account is out of credits — top it up at "
            "remove.bg, or switch BG_ENGINE to another engine.")
    if resp.status_code == 429:
        raise RemoveBgError(
            "remove.bg kept rate-limiting us even after several retries — "
            "try again in a minute.")
    if resp.status_code != 200:
        raise RemoveBgError(f"remove.bg error {resp.status_code}: "
                            f"{_short_reason(resp)}")
    charged = resp.headers.get("x-credits-charged")
    if charged:
        log.info("remove.bg: %s credit(s) charged", charged)
    return _decode(resp.content, sent, "remove.bg", RemoveBgError)


def leonardo_cutout(img: Image.Image) -> Optional[Image.Image]:
    """The subject cut out by Leonardo.Ai, as RGBA. None when unconfigured.

    Uses the SYNC endpoint Leonardo's own remove.bg migration guide points at:
    one request, the image in the response, no polling. The older
    /v1/variations/nobg path needs the image uploaded first for an ID and then
    a poll loop, which is the wrong shape twice over here -- optimize() walks a
    batch serially and the studio route answers a single synchronous POST.
    """
    if not config.leonardo_ready():
        return None
    import httpx
    sent = img.convert("RGB")
    payload = jpeg_payload(sent)
    resp = _post_with_retries("Leonardo.Ai", lambda: httpx.post(
        "https://cloud.leonardo.ai/api/rest/v2/generationssync",
        headers={"Authorization": f"Bearer {config.LEONARDO_API_KEY}",
                 "Content-Type": "application/json"},
        json=leonardo_request(payload),
        timeout=_TIMEOUT,
    ), LeonardoError)
    if resp.status_code in (401, 403):
        raise LeonardoError(
            "Leonardo.Ai rejected the API key — check the LEONARDO_API_KEY "
            "secret on the server (it may be missing, mistyped, or expired).")
    if resp.status_code == 402:
        raise LeonardoError(
            "The Leonardo.Ai account is out of credits — top it up at "
            "leonardo.ai.")
    if resp.status_code == 429:
        raise LeonardoError(
            "Leonardo.Ai kept rate-limiting us even after several retries — "
            "try again in a minute.")
    if resp.status_code not in (200, 201):
        raise LeonardoError(f"Leonardo.Ai error {resp.status_code}: "
                            f"{_short_reason(resp)}")
    return _decode(leonardo_image_bytes(resp), sent, "Leonardo.Ai",
                   LeonardoError)


# --- Leonardo request/response shape ---------------------------------------
# Kept as two small pure functions, the same split services/imagesearch.py
# uses for parse_leads: the wire format is the part most likely to need a
# correction, and this way correcting it touches no network code and is
# testable without httpx.
#
# VERIFY BEFORE RELYING ON IN PRODUCTION: docs.leonardo.ai could not be
# reached from the machine this was written on, so the v2 generationssync
# field names below follow Leonardo's documented conventions but have not been
# checked against a live response. The two things to confirm are the request
# key that carries the source image, and whether the result arrives as base64
# or as a URL that must then be fetched -- leonardo_image_bytes handles both,
# so a correction should be a small edit here rather than anywhere else.

def leonardo_request(payload: bytes) -> dict:
    """The JSON body for a remove-bg generation, given the source JPEG."""
    import base64
    return {
        "model": "remove-bg",
        "image": base64.b64encode(payload).decode("ascii"),
    }


def leonardo_image_bytes(resp) -> bytes:
    """The cutout bytes out of a generationssync response.

    Accepts either an inline base64 image or a URL to fetch, because which one
    v2 returns is the open question above. Raises LeonardoError when the body
    carries neither.
    """
    import base64
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise LeonardoError(
            "Leonardo.Ai returned a response that wasn't JSON.") from exc
    node = data
    for key in ("generations_by_pk", "sdGenerationJob", "generation", "data"):
        if isinstance(node, dict) and isinstance(node.get(key), dict):
            node = node[key]
    images = node.get("generated_images") or node.get("images") or []
    first = images[0] if isinstance(images, list) and images else node
    if not isinstance(first, dict):
        first = {}
    for key in ("image_base64", "imageBase64", "b64_json", "base64"):
        if first.get(key):
            try:
                return base64.b64decode(first[key])
            except Exception as exc:  # noqa: BLE001
                raise LeonardoError(
                    "Leonardo.Ai returned base64 that wouldn't decode.") from exc
    for key in ("url", "image_url", "imageUrl"):
        if first.get(key):
            return _fetch(str(first[key]))
    raise LeonardoError(
        "Leonardo.Ai's response carried no image — the API shape may have "
        "changed; the photo was kept as shot.")


def _fetch(url: str) -> bytes:
    """GET an image Leonardo handed us a URL for."""
    import httpx
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise LeonardoError(
            f"Couldn't download the cutout Leonardo.Ai produced "
            f"({type(exc).__name__}).") from exc
    return resp.content


def _short_reason(resp) -> str:
    """A trimmed, credential-free reason out of an error body.

    Never the request URL, and never more than a sentence of somebody else's
    JSON -- enough to tell two failures apart in a log.
    """
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 - not JSON, fall through to the text
        return (resp.text or "")[:160]
    errors = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("title") or errors[0].get("code") or "")[:160]
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])[:160]
    return (resp.text or "")[:160]


# The remote engines a chain entry can name. "local" is special-cased where the
# chain is walked, because it runs in-process behind the inference lock.
ENGINES: dict[str, Callable[[Image.Image], Optional[Image.Image]]] = {
    "removebg": removebg_cutout,
    "leonardo": leonardo_cutout,
}

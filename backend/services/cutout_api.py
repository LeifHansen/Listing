"""Remote background removal: the cutout Canva's API produces, as an RGBA image.

One remote engine, one shape. It takes a PIL image and returns the service's
cutout as RGBA — the alpha is the matte — or None when it has no credentials.
A configured engine that FAILS raises, with the actual reason in the message,
because a silent fall back to the weak local model is exactly how mangled
photos kept getting saved without anyone knowing why.

    canva      Canva's REST API (the "Connect API"). Canva owns remove.bg, and
               this is where its background remover lives now that the
               standalone remove.bg API shuts down on 2026-12-01. The remover
               is the `background_removal` IMAGE TRANSFORMATION, a preview API
               that has to be switched on for the app in Canva's developer
               portal, and the connected Canva account needs a plan with the
               remover on it (Canva Pro or above). Each cutout spends one of
               that account's monthly AI credits.

WHAT ONE CUTOUT IS, ON THE WIRE. Canva has no "send a photo, get a PNG back"
call. The remover works on an asset in the account's library and answers with
another asset, and an asset only ever exposes a THUMBNAIL — a few hundred
pixels, useless against this app's 1600px output. The full-size bytes come out
through a design export. So one cutout is four jobs, in order:

    1. POST /asset-uploads            the photo becomes an asset   (poll)
    2. POST /image-transformations    background_removal           (poll)
    3. POST /designs                  a page the size of the result, holding it
    4. POST /exports                  that page as a transparent PNG (poll),
       then GET the signed download URL it hands back.

Both assets are deleted afterwards (best effort) so a 250-photo batch does not
leave 500 files in the Canva library; the design cannot be — the API has no
delete for designs — and Canva removes API-created designs that nobody edits
after 7 days.

VERIFY BEFORE RELYING ON IN PRODUCTION: the one assumption here that could not
be checked against a live account is step 3 — that an image asset placed into
a custom design of the asset's own width and height fills the page edge to
edge. If Canva fits it with a margin instead, the cutout comes back inset, and
the fix is in _export_png alone (export at the asset's size, or crop to the
alpha bbox). _check_not_preview still refuses anything that comes back far
smaller than what was sent.

AUTHENTICATION is OAuth, not an API key: there is no key to paste. An operator
connects the Canva account ONCE with scripts/canva_authorize.py, which prints
a refresh token for CANVA_REFRESH_TOKEN. Access tokens last four hours and
every refresh ROTATES the refresh token — the old one is spent — so the newest
one is persisted under DATA_DIR (the Fly volume), the same way config keeps
.secret_key, and read back on restart. The env var is the seed, tried again
only when the persisted token is refused, which is how re-seeding after a
breakage works. This assumes one machine does the refreshing: two machines
rotating the same token would each invalidate the other's.

LIMITS WORTH KNOWING. Canva rate-limits per user: 30 uploads and 20
transformations a minute, and EXPORTS at 75 per five minutes and 500 a day.
A batch past those is paced by the 429 retries below for a while and then
falls through to the local model for the rest, with the reason logged — never
a lost photo. A 429 that names the AI CREDIT quota is a fact about the
account, not a blip, and does not retry.

Errors subclass ValueError on purpose: the studio route already maps
ValueError to a 422 carrying the message, so the seller reads the real cause
("out of AI credits") instead of watching the cutout silently get worse.

httpx is imported INSIDE each call, not at module scope. The `cutout` CI job
installs Pillow and pytest and nothing else, so a module-level import would
break every test that merely reaches services/images. It is also how the
deleted remove.bg / Leonardo / Pixian / Photoroom engines did it.

No token is ever logged, and no exception MESSAGE is either: httpx puts the
request URL in its messages and a URL can carry credentials (Canva's signed
download URLs do). The type name and the HTTP status are enough to debug with
and safe to write down.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from .. import config
from ..config import log

# Cap what we send a remote engine: the final output is always 1600px, so a
# full 4032px working copy just wastes upload time and RAM -- and on a
# 250-photo batch those add up to minutes and hundreds of MB. 2048px keeps a
# comfortable margin above the output size, and well inside Canva's 10MP cap
# on a transformation's output.
_MAX_SIDE = int(os.getenv("BG_API_MAX_SIDE", "2048") or 2048)

# Total attempts per HTTP request. Big batches WILL trip an API's per-minute
# rate limit and the odd network blip; those retry with backoff instead of
# failing the photo. Hard failures (bad credentials, out of credits) never
# retry -- they would fail identically every time.
_TRIES = int(os.getenv("BG_API_TRIES", "4") or 4)

# One HTTP request answers in a second or three. 30s is generous for a slow
# day and still far inside the studio's own budget.
_TIMEOUT = float(os.getenv("BG_API_TIMEOUT", "30") or 30)

# How long one of Canva's asynchronous jobs may stay in_progress before the
# photo is given up on, and how often it is asked. Uploads and exports finish
# in seconds; the remover itself in a few more.
_JOB_TIMEOUT = float(os.getenv("CANVA_JOB_TIMEOUT", "120") or 120)
_POLL_SECONDS = float(os.getenv("CANVA_POLL_SECONDS", "1.5") or 1.5)

# How much smaller than what we sent a result may be before we call it a
# preview. A cutout a fraction of the photo's size pasted into a 1600px
# listing looks like a bug in this app rather than a limit on the account.
_PREVIEW_RATIO = 0.6

API = "https://api.canva.com/rest/v1"
# Canva's spec names /auth as the token server and /rest as its deprecated
# predecessor; _exchange falls back to the second if the first ever 404s.
TOKEN_URL = "https://api.canva.com/auth/v1/oauth/token"
_TOKEN_URL_LEGACY = "https://api.canva.com/rest/v1/oauth/token"

# The one transformation this engine asks for.
TRANSFORMATION = "background_removal"
# What the two assets a cutout makes are called in the Canva library, so an
# operator looking at the account knows what they are and that they are safe
# to delete. Canva caps asset names at 50 characters.
ASSET_NAME = "ThryftShop photo"
CUTOUT_NAME = "ThryftShop cutout"


class CutoutApiError(ValueError):
    """A configured remote engine failed, with a seller-readable reason.

    ValueError so the studio route's existing mapping turns it into a 422 plus
    the message, instead of a 500 or a silent downgrade.
    """


class CanvaError(CutoutApiError):
    """Canva is configured but the cutout could not be had."""


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


def _error_code(resp) -> str:
    """Canva's machine-readable `code` out of an error body, or ""."""
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 - not JSON
        return ""
    return str(data.get("code") or "") if isinstance(data, dict) else ""


def _is_quota(resp) -> bool:
    """A 429 about the account's AI credits, not about request rate."""
    return resp.status_code == 429 and _error_code(resp).startswith("credit_quota")


def _send_with_retries(name: str, send: Callable, exc_cls: type):
    """Run `send()` (an httpx call) with retries on 429/5xx/network errors.
    Returns the final response; raises `exc_cls` when the service stayed
    unreachable through every attempt.

    Only rate-limit 429s and 5xx retry. A 401, 403 or a credit-quota 429 is a
    fact about the account, not a blip, and retrying it three more times just
    spends three more seconds arriving at the same answer.
    """
    resp = None
    for attempt in range(1, _TRIES + 1):
        try:
            resp = send()
        except exc_cls:
            # One of OUR sentences (a refused refresh token, say), raised from
            # inside the send: already the answer, not a blip to retry.
            raise
        except Exception as exc:  # noqa: BLE001 - network/timeout
            if attempt == _TRIES:
                # The TYPE only, never str(exc): httpx puts the request URL in
                # its messages and the URL can carry credentials.
                raise exc_cls(
                    f"Couldn't reach {name} ({type(exc).__name__}) — "
                    "it may be down; the photo was kept as shot.") from exc
            wait = _backoff(None, attempt)
            log.info("%s: network error (%s) — retry %d/%d in %.0fs",
                     name, type(exc).__name__, attempt, _TRIES - 1, wait)
            time.sleep(wait)
            continue
        retryable = (resp.status_code == 429 and not _is_quota(resp)) \
            or resp.status_code >= 500
        if retryable and attempt < _TRIES:
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

    Shipping that would upscale a small cutout into a 1600px listing photo,
    which reads as this app being broken. Better to say what came back.
    """
    if max(sent.size) <= 0:
        return
    if max(cut.size) < max(sent.size) * _PREVIEW_RATIO:
        raise exc_cls(
            f"{name} returned a {cut.width}x{cut.height} preview instead of "
            f"the full-size cutout — the export came back downscaled (a plan "
            "limit on the connected account, or the design was not the size "
            "of the image). The photo was kept as shot.")


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
        if data.get("message"):
            return str(data["message"])[:160]
        errors = data.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("title") or errors[0].get("code")
                       or "")[:160]
        if data.get("error"):
            return str(data["error"])[:160]
    return (resp.text or "")[:160]


# --- the access token -------------------------------------------------------
# One process-wide token, refreshed under a lock. The lock matters because a
# batch cuts photos in parallel and Canva rotates the refresh token on every
# use: two threads refreshing at once would each spend the one token the other
# then tries, and the second would read as "reconnect Canva" for no reason.

_TOKEN_LOCK = threading.Lock()
_TOKEN: dict = {"access": "", "expires_at": 0.0, "refresh": ""}
# Refresh this long before Canva says the token expires, so a four-job cutout
# that starts near the edge does not have its third call refused.
_EXPIRY_MARGIN = 120.0

RECONNECT = ("Reconnect the Canva account: run `python3 "
             "scripts/canva_authorize.py` and set CANVA_REFRESH_TOKEN to the "
             "refresh token it prints.")


def token_file() -> Path:
    """Where the newest refresh token lives between restarts. Resolved per
    call, not at import: config.DATA_DIR is reloadable (tests)."""
    return config.DATA_DIR / ".canva_token.json"


def _load_saved() -> dict:
    try:
        path = token_file()
        if path.is_file():
            data = json.loads(path.read_text() or "{}")
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def _save(data: dict) -> None:
    """Persist the rotated token. A read-only volume is logged, not fatal:
    the process keeps working until it restarts, and the operator reads why
    it then asked to be reconnected."""
    try:
        path = token_file()
        path.write_text(json.dumps(data))
        path.chmod(0o600)
    except OSError as exc:
        log.warning("Canva: couldn't persist the rotated refresh token at %s "
                    "(%s) — the next restart will need CANVA_REFRESH_TOKEN "
                    "re-seeded.", token_file(), type(exc).__name__)


def forget_token() -> None:
    """Drop the in-process token (tests, and a re-seed at runtime)."""
    with _TOKEN_LOCK:
        _TOKEN.update(access="", expires_at=0.0, refresh="")


def _access_token(force: bool = False) -> str:
    """A bearer token good for at least _EXPIRY_MARGIN more seconds, refreshing
    if needed. `force` discards the cached one — what a 401 asks for."""
    with _TOKEN_LOCK:
        now = time.time()
        if (not force and _TOKEN["access"]
                and _TOKEN["expires_at"] - _EXPIRY_MARGIN > now):
            return _TOKEN["access"]
        # Newest first: what this process rotated to, then what the last
        # process persisted, then the operator's seed. The seed is tried
        # AGAIN as a fallback when the newer one is refused, which is how a
        # re-seeded CANVA_REFRESH_TOKEN takes over after a breakage.
        current = (_TOKEN["refresh"]
                   or str(_load_saved().get("refresh_token") or "")
                   or config.CANVA_REFRESH_TOKEN)
        candidates = [current]
        if config.CANVA_REFRESH_TOKEN and config.CANVA_REFRESH_TOKEN != current:
            candidates.append(config.CANVA_REFRESH_TOKEN)
        last: Optional[CanvaError] = None
        for refresh in candidates:
            try:
                data = _exchange(refresh)
            except CanvaError as exc:
                last = exc
                continue
            _TOKEN.update(
                access=str(data["access_token"]),
                expires_at=now + float(data.get("expires_in") or 0),
                refresh=str(data.get("refresh_token") or refresh))
            _save({"refresh_token": _TOKEN["refresh"],
                   "rotated_at": int(now)})
            return _TOKEN["access"]
        assert last is not None
        raise last


def _exchange(refresh_token: str) -> dict:
    """POST the refresh grant; the token response as a dict, or CanvaError
    saying which of the two credentials Canva refused."""
    import httpx
    auth = (config.CANVA_CLIENT_ID, config.CANVA_CLIENT_SECRET)
    grant = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    resp = _send_with_retries("Canva", lambda: httpx.post(
        TOKEN_URL, auth=auth, data=grant, timeout=_TIMEOUT), CanvaError)
    if resp.status_code == 404:
        resp = _send_with_retries("Canva", lambda: httpx.post(
            _TOKEN_URL_LEGACY, auth=auth, data=grant, timeout=_TIMEOUT),
            CanvaError)
    if resp.status_code in (400, 401):
        code = _error_code(resp)
        if code == "invalid_grant":
            raise CanvaError(
                "Canva no longer accepts this app's refresh token — it was "
                "revoked, has expired, or was already spent by another "
                "machine. " + RECONNECT)
        raise CanvaError(
            "Canva rejected the app credentials — check the CANVA_CLIENT_ID "
            "and CANVA_CLIENT_SECRET secrets on the server (one may be "
            f"missing, mistyped, or regenerated). ({_short_reason(resp)})")
    if resp.status_code == 429:
        raise CanvaError("Canva kept rate-limiting the token refresh even "
                         "after several retries — try again in a minute.")
    if resp.status_code != 200:
        raise CanvaError(f"Canva error {resp.status_code} refreshing the "
                         f"access token: {_short_reason(resp)}")
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise CanvaError("Canva's token response wasn't JSON.") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise CanvaError("Canva's token response carried no access token — "
                         "the API shape may have changed.")
    return data


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _call(send: Callable[[str], object]):
    """Run `send(token)` with a fresh-enough token; on a 401, refresh once and
    try again. Everything else is the caller's to read."""
    token = _access_token()
    resp = _send_with_retries("Canva", lambda: send(token), CanvaError)
    if resp.status_code == 401:
        token = _access_token(force=True)
        resp = _send_with_retries("Canva", lambda: send(token), CanvaError)
    return resp


def _raise_for(resp, what: str) -> None:
    """Turn a non-2xx answer into a CanvaError that says what to do."""
    if 200 <= resp.status_code < 300:
        return
    if resp.status_code == 401:
        raise CanvaError(
            "Canva rejected this app's access token even after refreshing "
            "it — the connection may have been revoked. " + RECONNECT)
    if resp.status_code == 403:
        raise CanvaError(
            f"Canva refused {what} (permission denied). The background "
            "remover is a PREVIEW API that has to be enabled for this app in "
            "Canva's developer portal, and the connected Canva account needs "
            "a plan with the background remover and transparent PNG export "
            f"(Canva Pro). ({_short_reason(resp)})")
    if _is_quota(resp):
        raise CanvaError(
            "The Canva account has used up its AI credits for the month, so "
            "no backgrounds can be removed until they reset — upgrade the "
            "Canva plan, or switch BG_ENGINE to local. "
            f"({_short_reason(resp)})")
    if resp.status_code == 429:
        raise CanvaError(
            f"Canva kept rate-limiting us while {what}, even after several "
            "retries — Canva allows 75 exports per five minutes and 500 a "
            "day on one account; try again in a few minutes.")
    raise CanvaError(f"Canva error {resp.status_code} while {what}: "
                     f"{_short_reason(resp)}")


# --- the four jobs ----------------------------------------------------------

def _job_of(resp, what: str) -> dict:
    try:
        job = resp.json().get("job")
    except Exception as exc:  # noqa: BLE001
        raise CanvaError(f"Canva's answer while {what} wasn't JSON.") from exc
    if not isinstance(job, dict) or not job.get("status"):
        raise CanvaError(f"Canva's answer while {what} carried no job — the "
                         "API shape may have changed; the photo was kept as "
                         "shot.")
    return job


def _poll(kind: str, job: dict, what: str) -> dict:
    """Ask after one of Canva's asynchronous jobs until it is done.

    Polling costs no AI credits. A job that completes immediately comes back
    without an id and is returned as is.
    """
    import httpx
    deadline = time.monotonic() + _JOB_TIMEOUT
    while job.get("status") == "in_progress":
        if time.monotonic() > deadline:
            raise CanvaError(f"Canva took too long {what} — try again in a "
                             "minute; the photo was kept as shot.")
        job_id = str(job.get("id") or "")
        if not job_id:
            raise CanvaError(f"Canva reported a job in progress while {what} "
                             "but gave it no id to poll.")
        time.sleep(_POLL_SECONDS)
        resp = _call(lambda tok: httpx.get(f"{API}/{kind}/{job_id}",
                                           headers=_auth(tok),
                                           timeout=_TIMEOUT))
        _raise_for(resp, what)
        job = _job_of(resp, what)
    if job.get("status") != "success":
        raise CanvaError(_failure(job, what))
    return job


# Canva's job-failure codes, in the seller's words. Anything unlisted gets
# Canva's own message, trimmed.
_FAILURE_WORDS = {
    "no_foreground_found":
        "Canva couldn't find an item in this photo — it was kept as shot.",
    "quota_exceeded":
        "The Canva account has used up its AI credits for the month, so no "
        "backgrounds can be removed until they reset — upgrade the Canva "
        "plan, or switch BG_ENGINE to local.",
    "license_required":
        "Canva wouldn't export the cutout: the connected account's plan "
        "doesn't allow transparent PNG export (it needs Canva Pro).",
    "file_too_big":
        "Canva refused the photo as too large — lower BG_API_MAX_SIDE.",
}


def _failure(job: dict, what: str) -> str:
    err = job.get("error") if isinstance(job.get("error"), dict) else {}
    code = str(err.get("code") or "")
    if code in _FAILURE_WORDS:
        return _FAILURE_WORDS[code]
    message = str(err.get("message") or "")[:160]
    return (f"Canva failed while {what}"
            + (f": {message}" if message else "")
            + " — the photo was kept as shot.")


def _upload(payload: bytes, name: str) -> str:
    """The photo as an asset in the Canva library; its id."""
    import httpx
    meta = json.dumps({"name_base64":
                       base64.b64encode(name.encode()).decode("ascii")})
    resp = _call(lambda tok: httpx.post(
        f"{API}/asset-uploads",
        headers={**_auth(tok),
                 "Content-Type": "application/octet-stream",
                 "Asset-Upload-Metadata": meta},
        content=payload,
        timeout=_TIMEOUT))
    _raise_for(resp, "uploading the photo")
    job = _poll("asset-uploads", _job_of(resp, "uploading the photo"),
                "uploading the photo")
    asset = job.get("asset") if isinstance(job.get("asset"), dict) else {}
    if not asset.get("id"):
        raise CanvaError("Canva's upload finished without an asset id — the "
                         "API shape may have changed.")
    return str(asset["id"])


def _transform(asset_id: str) -> dict:
    """background_removal applied to the asset; the new asset Canva made.

    This is the call that spends an AI credit.
    """
    import httpx
    resp = _call(lambda tok: httpx.post(
        f"{API}/image-transformations",
        headers=_auth(tok),
        json={"asset_id": asset_id,
              "name": CUTOUT_NAME,
              "transformations": [{"type": TRANSFORMATION}]},
        timeout=_TIMEOUT))
    _raise_for(resp, "removing the background")
    job = _poll("image-transformations",
                _job_of(resp, "removing the background"),
                "removing the background")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    asset = result.get("asset") if isinstance(result.get("asset"), dict) else {}
    if not asset.get("id"):
        raise CanvaError("Canva's background removal finished without a "
                         "result asset — the API shape may have changed; the "
                         "photo was kept as shot.")
    return asset


def _export_png(asset: dict, fallback_size: tuple[int, int]) -> str:
    """The cutout asset as a transparent PNG; the signed URL to download it.

    A design the exact size of the asset is created around it and exported
    with no width/height, which Canva documents as "the dimensions of the
    design". The asset's own metadata gives the size; the photo we sent
    stands in when Canva omits it (it is optional in the schema).
    """
    import httpx
    meta = asset.get("metadata") if isinstance(asset.get("metadata"), dict) \
        else {}
    width = int(meta.get("width") or fallback_size[0])
    height = int(meta.get("height") or fallback_size[1])
    # Canva's custom-design bounds: each side 40..8000, area <= 25MP. A
    # transformation's output is capped at 10MP, so only the floor can bite,
    # on a photo nobody should be listing.
    width, height = max(40, min(8000, width)), max(40, min(8000, height))
    resp = _call(lambda tok: httpx.post(
        f"{API}/designs",
        headers=_auth(tok),
        json={"type": "type_and_asset",
              "design_type": {"type": "custom",
                              "width": width, "height": height},
              "asset_id": asset["id"],
              "title": CUTOUT_NAME},
        timeout=_TIMEOUT))
    _raise_for(resp, "preparing the cutout for download")
    try:
        design_id = str(resp.json()["design"]["id"])
    except Exception as exc:  # noqa: BLE001
        raise CanvaError("Canva's design response carried no id — the API "
                         "shape may have changed.") from exc
    resp = _call(lambda tok: httpx.post(
        f"{API}/exports",
        headers=_auth(tok),
        json={"design_id": design_id,
              "format": {"type": "png", "transparent_background": True}},
        timeout=_TIMEOUT))
    _raise_for(resp, "exporting the cutout")
    job = _poll("exports", _job_of(resp, "exporting the cutout"),
                "exporting the cutout")
    urls = job.get("urls")
    if not isinstance(urls, list) or not urls:
        raise CanvaError("Canva's export carried no download URL — the API "
                         "shape may have changed; the photo was kept as shot.")
    return str(urls[0])


def _download(url: str) -> bytes:
    """GET the PNG Canva handed us a signed URL for. No bearer token: the
    URL carries its own authorisation (and so never goes in a message)."""
    import httpx
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise CanvaError(
            f"Couldn't download the cutout Canva produced "
            f"({type(exc).__name__}).") from exc
    return resp.content


def _discard(asset_ids: list[str]) -> None:
    """Delete the assets a cutout made, best effort: the cutout is already in
    hand, so nothing here may raise. A leftover is clutter in OUR library,
    not a wrong photo on a listing."""
    import httpx
    for asset_id in asset_ids:
        try:
            resp = _call(lambda tok, a=asset_id: httpx.delete(
                f"{API}/assets/{a}", headers=_auth(tok), timeout=_TIMEOUT))
            if resp.status_code >= 300 and resp.status_code != 404:
                log.info("Canva: couldn't delete asset (HTTP %d)",
                         resp.status_code)
        except Exception as exc:  # noqa: BLE001 - cleanup only
            log.info("Canva: couldn't delete asset (%s)", type(exc).__name__)


def canva_cutout(img: Image.Image) -> Optional[Image.Image]:
    """The subject cut out by Canva, as RGBA. None when unconfigured."""
    if not config.canva_ready():
        return None
    sent = img.convert("RGB")
    payload = jpeg_payload(sent)
    with Image.open(BytesIO(payload)) as capped:
        capped_size = capped.size
    made: list[str] = []
    try:
        source_id = _upload(payload, ASSET_NAME)
        made.append(source_id)
        cut_asset = _transform(source_id)
        made.append(str(cut_asset["id"]))
        body = _download(_export_png(cut_asset, capped_size))
    finally:
        if made:
            _discard(made)
    return _decode(body, sent, "Canva", CanvaError)


# The remote engines a chain entry can name. "local" is special-cased where the
# chain is walked, because it runs in-process behind the inference lock.
ENGINES: dict[str, Callable[[Image.Image], Optional[Image.Image]]] = {
    "canva": canva_cutout,
}

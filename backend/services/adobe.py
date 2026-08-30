"""Adobe Lightroom + Photoshop APIs (Firefly Services) for listing photos.

The upload pipeline hands every photo to Adobe before local finishing:

  1. the photo is staged to R2 (Adobe's async APIs pull inputs and push
     outputs via presigned URLs — they never accept raw bytes),
  2. the Lightroom API applies our "studio" develop preset to every shot
     (tone, light, color, gentle sharpening — the studio look),
  3. when background removal is on, the Photoshop Remove Background service
     cuts out the subject (same credential and credit pool as Lightroom),
  4. the result is pulled back down and the local pipeline continues as
     usual (square crop, resize, save, identify, listing generation).

Needs ADOBE_CLIENT_ID / ADOBE_CLIENT_SECRET — a server-to-server OAuth
credential from a project on https://developer.adobe.com/console with the
Lightroom and Photoshop APIs enabled — plus R2 as the hand-off storage.

Every failure raises AdobeError (a ValueError) with a user-facing reason;
callers keep the original photo rather than saving a broken one.
"""
from __future__ import annotations

import threading
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from .. import config, objstore
from ..config import log


class AdobeError(ValueError):
    """An Adobe call failed — carries a user-facing reason. Subclasses
    ValueError so route error mapping (422 + toast) surfaces the real cause."""


# The bundled "studio" develop preset (Lightroom XMP). Staged to R2 once per
# process so Lightroom jobs can fetch it; override the preset entirely with
# ADOBE_STUDIO_PRESET_URL (e.g. a preset exported from your own catalog).
_PRESET_PATH = Path(__file__).resolve().parent.parent / "assets" / "studio-preset.xmp"
_PRESET_KEY = "adobe/presets/studio.xmp"

_POLL_INTERVAL = 1.5   # seconds between job status checks
_JOB_TIMEOUT = 150     # seconds before giving up on a single photo job
_URL_TTL = 3600        # presigned URL lifetime (covers queueing + retries)

_token_lock = threading.Lock()
_token: dict[str, Any] = {"value": "", "expires": 0.0}

_preset_lock = threading.Lock()
_preset_staged = False


def _access_token() -> str:
    """IMS server-to-server access token, cached until shortly before expiry."""
    with _token_lock:
        if _token["value"] and time.time() < _token["expires"] - 60:
            return _token["value"]
        try:
            resp = httpx.post(
                config.ADOBE_IMS_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": config.ADOBE_CLIENT_ID,
                    "client_secret": config.ADOBE_CLIENT_SECRET,
                    "scope": config.ADOBE_SCOPES,
                },
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 - network/timeout
            raise AdobeError(f"Couldn't reach Adobe's sign-in service: {exc}") from exc
        if resp.status_code != 200:
            try:
                body = resp.json()
                detail = str(body.get("error_description") or body.get("error") or "")[:120]
            except Exception:  # noqa: BLE001
                detail = resp.text[:120]
            raise AdobeError(
                "Adobe rejected the API credentials — check ADOBE_CLIENT_ID / "
                f"ADOBE_CLIENT_SECRET on the server ({resp.status_code}: {detail}).")
        payload = resp.json()
        token = str(payload.get("access_token") or "")
        if not token:
            raise AdobeError("Adobe sign-in returned no access token.")
        _token["value"] = token
        _token["expires"] = time.time() + float(payload.get("expires_in") or 3600)
        return token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_access_token()}",
        "x-api-key": config.ADOBE_CLIENT_ID,
        "Content-Type": "application/json",
    }


def _api_error(resp: httpx.Response) -> AdobeError:
    if resp.status_code in (401, 403):
        return AdobeError(
            f"Adobe refused the request ({resp.status_code}) — the credential may "
            "not have the Lightroom/Photoshop APIs enabled; check the project on "
            "developer.adobe.com/console.")
    if resp.status_code == 402:
        return AdobeError("The Adobe account is out of API credits.")
    if resp.status_code == 429:
        return AdobeError("Adobe is rate-limiting us — try again in a minute.")
    return AdobeError(f"Adobe error {resp.status_code}: {resp.text[:160]}")


def _submit(path: str, body: dict) -> str:
    """POST an async job; returns the status-poll URL from _links.self."""
    try:
        resp = httpx.post(f"{config.ADOBE_IMAGE_API_BASE}{path}",
                          json=body, headers=_headers(), timeout=30)
    except AdobeError:
        raise
    except Exception as exc:  # noqa: BLE001 - network/timeout
        raise AdobeError(f"Couldn't reach Adobe: {exc}") from exc
    if resp.status_code not in (200, 202):
        raise _api_error(resp)
    try:
        href = str(resp.json()["_links"]["self"]["href"])
    except Exception:  # noqa: BLE001 - unexpected response shape
        href = ""
    if not href:
        raise AdobeError("Adobe accepted the job but returned no status link.")
    return href


def _walk_statuses(node: Any, out: list[str]) -> None:
    """Collect every "status" value in the job payload (Lightroom nests them
    under outputs[], Photoshop under output — tolerate both shapes)."""
    if isinstance(node, dict):
        status = node.get("status")
        if isinstance(status, str):
            out.append(status.lower())
        for value in node.values():
            _walk_statuses(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_statuses(value, out)


def _failure_detail(node: Any) -> str:
    """Best-effort human-readable reason out of a failed job payload."""
    if isinstance(node, dict):
        for key in ("reason", "title", "description", "message"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:160]
        for value in node.values():
            found = _failure_detail(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _failure_detail(value)
            if found:
                return found
    return ""


def _same_origin(href: str) -> bool:
    """Is this poll URL on the endpoint we submitted the job to?"""
    want = urlparse(config.ADOBE_IMAGE_API_BASE)
    got = urlparse(href or "")
    return (bool(got.scheme) and got.scheme == want.scheme
            and bool(got.netloc) and got.netloc.lower() == want.netloc.lower())


def _wait(href: str) -> None:
    """Poll a job's status URL until it succeeds, fails, or times out.

    The href came out of a RESPONSE BODY (_submit reads _links.self.href) and
    every poll carries the IMS bearer token and the client id, so where it
    points decides where a live credential is sent. Adobe is the one who wrote
    it, which makes this hardening rather than a hole — but a response shape we
    do not control choosing the destination for a secret is the wrong default,
    and the correct constraint costs nothing: the poll URL must be on the
    origin we submitted to. An operator pointing ADOBE_IMAGE_API_BASE at a
    different Adobe endpoint still works; what cannot happen is the answer
    moving us off it.
    """
    if not _same_origin(href):
        # Deliberately does not echo the href: it is the untrusted half, and
        # this string reaches a seller as a toast. The log line has it.
        log.warning("adobe: refusing to poll a status URL off %s: %.200r",
                    config.ADOBE_IMAGE_API_BASE, href)
        raise AdobeError("Adobe returned a status link we can't trust.")
    deadline = time.time() + _JOB_TIMEOUT
    while True:
        try:
            resp = httpx.get(href, headers=_headers(), timeout=30)
        except AdobeError:
            raise
        except Exception as exc:  # noqa: BLE001 - network/timeout
            raise AdobeError(f"Lost contact with Adobe mid-job: {exc}") from exc
        if resp.status_code != 200:
            raise _api_error(resp)
        payload = resp.json()
        statuses: list[str] = []
        _walk_statuses(payload, statuses)
        if any(s in ("failed", "error") for s in statuses):
            detail = _failure_detail(payload)
            raise AdobeError("Adobe couldn't process this photo"
                             + (f": {detail}" if detail else "."))
        if statuses and all(s == "succeeded" for s in statuses):
            return
        if time.time() > deadline:
            raise AdobeError("Adobe took too long on this photo — try again.")
        time.sleep(_POLL_INTERVAL)


def _tmp_key(suffix: str) -> str:
    return f"adobe/tmp/{uuid.uuid4().hex}{suffix}"


def _stage_in(data: bytes, key: str, content_type: str) -> str:
    """Upload job input bytes to R2; returns a presigned GET URL for Adobe."""
    try:
        objstore.put_bytes(data, key, content_type)
        return objstore.presigned_get(key, expires=_URL_TTL)
    except Exception as exc:  # noqa: BLE001
        raise AdobeError(
            f"Couldn't stage the photo for Adobe (object storage): {exc}") from exc


def _stage_out(key: str) -> str:
    """Presigned PUT URL Adobe writes the job output to."""
    try:
        return objstore.presigned_put(key, expires=_URL_TTL)
    except Exception as exc:  # noqa: BLE001
        raise AdobeError(
            f"Couldn't prepare output storage for Adobe: {exc}") from exc


def _fetch_result(key: str) -> Image.Image:
    try:
        data = objstore.get_bytes(key)
        img = Image.open(BytesIO(data))
        img.load()
        return img
    except Exception as exc:  # noqa: BLE001
        raise AdobeError(
            f"Adobe finished but the result couldn't be read back: {exc}") from exc


def _studio_preset_url() -> str:
    """URL of the studio develop preset for Lightroom jobs. Uses the operator's
    ADOBE_STUDIO_PRESET_URL when set; otherwise stages the bundled XMP to R2
    (once per process) and presigns it."""
    if config.ADOBE_STUDIO_PRESET_URL:
        return config.ADOBE_STUDIO_PRESET_URL
    global _preset_staged
    try:
        with _preset_lock:
            if not _preset_staged:
                objstore.put_bytes(_PRESET_PATH.read_bytes(), _PRESET_KEY,
                                   "application/rdf+xml")
                _preset_staged = True
        return objstore.presigned_get(_PRESET_KEY, expires=_URL_TTL)
    except AdobeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AdobeError(f"Couldn't stage the studio preset: {exc}") from exc


def _jpeg_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


def apply_studio(img: Image.Image) -> Image.Image:
    """Apply the "studio" develop preset via the Lightroom API.

    Returns the edited image at the input's resolution. Raises AdobeError on
    any failure — callers keep the original photo.
    """
    in_key, out_key = _tmp_key(".jpg"), _tmp_key(".jpg")
    try:
        body = {
            "inputs": {"href": _stage_in(_jpeg_bytes(img), in_key, "image/jpeg"),
                       "storage": "external"},
            "options": {"presets": [{"href": _studio_preset_url(),
                                     "storage": "external"}]},
            "outputs": [{"href": _stage_out(out_key), "storage": "external",
                         "type": "image/jpeg"}],
        }
        _wait(_submit("/lrService/presets", body))
        return _fetch_result(out_key)
    finally:
        # Hand-off objects are transient; best-effort cleanup (never raises).
        objstore.delete(in_key)
        objstore.delete(out_key)


def remove_background(img: Image.Image) -> Image.Image:
    """Cut out the subject via Photoshop's Remove Background service.

    Returns an RGBA cutout (transparent background, soft mask edges) at the
    input's resolution. Raises AdobeError on any failure.
    """
    in_key, out_key = _tmp_key(".jpg"), _tmp_key(".png")
    try:
        body = {
            "input": {"href": _stage_in(_jpeg_bytes(img), in_key, "image/jpeg"),
                      "storage": "external"},
            "output": {"href": _stage_out(out_key), "storage": "external",
                       "mask": {"format": "soft"}},
        }
        _wait(_submit("/sensei/cutout", body))
        return _fetch_result(out_key).convert("RGBA")
    finally:
        objstore.delete(in_key)
        objstore.delete(out_key)

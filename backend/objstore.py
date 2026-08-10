"""Cloudflare R2 (S3-compatible) object storage for optimized images.

Optional and resilient: with no credentials configured everything is a no-op
and images are served from local disk. When configured, optimized photos are
uploaded to R2 so they survive restarts and are reliably fetchable by eBay —
served from the bucket's public URL when R2_PUBLIC_BASE_URL is set, otherwise
via short-lived presigned GETs handed out by the /media route.

The bucket is ensured lazily on first use (created if absent). If that fails —
wrong credentials, an unreachable endpoint — a latch flips the whole module
back to disabled-with-a-reason rather than letting every photo edit degrade
into a 502: misconfigured storage must look exactly like no storage. The
latch expires after a few minutes so a transient blip (DNS at boot, a
Cloudflare hiccup) can't quietly disable storage until the next deploy.
"""
from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Optional

from . import config, storage
from .config import log

_RETRY_AFTER = 600  # seconds a failed init latches storage off before retrying

_lock = threading.Lock()
_client = None
_error: Optional[str] = None    # latest init failure, surfaced in /api/health
_error_at: float = 0.0          # when it happened — the latch expires


def _latched() -> bool:
    return _error is not None and time.time() - _error_at < _RETRY_AFTER


def enabled() -> bool:
    return config.r2_configured() and not _latched()


def last_error() -> Optional[str]:
    """Why object storage shut itself off (None while healthy)."""
    return _error if _latched() else None


def _fail(reason: str) -> None:
    global _error, _error_at
    _error, _error_at = reason, time.time()
    log.warning("objstore: %s — object storage disabled for %d min",
                reason, _RETRY_AFTER // 60)


def _ensure_bucket(client) -> bool:
    """True when the configured bucket is usable (created if absent).

    A 403 on the existence check is NOT a failure: a narrowly-scoped token
    (object read/write only, no bucket-level ops) can't answer head_bucket
    but works fine for every object operation this module does — and such
    tokens predate this check, so treating 403 as fatal would break
    deployments that used to work.
    """
    from botocore.exceptions import ClientError

    try:
        client.head_bucket(Bucket=config.R2_BUCKET)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("403", "AccessDenied"):
            log.info("objstore: token can't head_bucket (scoped to objects?) — "
                     "assuming bucket %r exists", config.R2_BUCKET)
            return True
        if code not in ("404", "NoSuchBucket"):
            _fail(f"bucket check failed: {exc}")
            return False
    try:
        client.create_bucket(Bucket=config.R2_BUCKET)
        log.info("objstore: created R2 bucket %r", config.R2_BUCKET)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            return True  # racing creator — the bucket is there, which is all we need
        _fail(f"couldn't create bucket {config.R2_BUCKET!r}: {exc}. Create it once "
              "in the Cloudflare dashboard, or use an API token with Admin R2 access.")
        return False


def _get_client():
    """The shared S3 client, or None when disabled/latched. First use builds
    the client and ensures the bucket; failures latch the module off until
    the retry window opens."""
    global _client, _error
    if not enabled():
        return None
    if _client is None:
        with _lock:
            if _client is None and not _latched():
                import boto3  # imported lazily so the dep is only needed when used

                candidate = boto3.client(
                    "s3",
                    endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                    aws_access_key_id=config.R2_ACCESS_KEY_ID,
                    aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
                    region_name="auto",
                )
                try:
                    ok = _ensure_bucket(candidate)
                except Exception as exc:  # noqa: BLE001 - endpoint/DNS/etc.
                    _fail(f"R2 unreachable: {exc}")
                    ok = False
                if ok:
                    _client = candidate
                    _error = None
                    log.info(
                        "objstore: R2 active (bucket=%s, urls=%s)", config.R2_BUCKET,
                        "public" if config.r2_public_urls() else "presigned")
    return _client


def probe() -> None:
    """Touch the client once so the bucket check (and any latch) resolves at
    startup, not on a seller's first photo edit; /api/health then reports the
    real state immediately. Best-effort by construction."""
    _get_client()


def key_for(session_id: str, name: str) -> str:
    # Same sanitization as the on-disk session dir, so the key a photo is
    # uploaded under (raw id, e.g. "ebay-123") and the key housekeeping checks
    # (dir name "ebay123") can never diverge.
    return f"sessions/{storage.safe_session_name(session_id)}/optimized/{name}"


def public_url(key: str) -> str:
    return f"{config.R2_PUBLIC_BASE_URL}/{key}"


def url_for(key: str, expires: int = 3600) -> Optional[str]:
    """A browser/eBay-fetchable URL for an object: the bucket's public URL
    when one is configured, else a presigned GET. None when disabled or the
    presign fails."""
    if not enabled():
        return None
    if config.r2_public_urls():
        return public_url(key)
    try:
        return presigned_get(key, expires)
    except Exception as exc:  # noqa: BLE001 - never break the request
        log.warning(f"objstore: presign failed: {exc}")
        return None


def upload(local_path: Path, key: str) -> Optional[str]:
    """Upload a file to R2 and return a fetchable URL. None on failure/disabled."""
    try:
        client = _get_client()
        if client is None:
            return None
        client.upload_file(
            str(local_path), config.R2_BUCKET, key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        return url_for(key)
    except Exception as exc:  # noqa: BLE001 - never break the request
        log.warning(f"objstore: upload failed: {exc}")
        return None


def upload_optimized(session_id: str, local_dir: Path, names: list[str]) -> None:
    """Best-effort upload of a session's optimized images to R2.

    Uploads run concurrently: these are network round trips, and a pile of 40
    photos done one at a time added tens of seconds to whatever was waiting on
    it. boto3 clients are thread-safe for this, and the per-file upload already
    swallows its own failures, so a slow or failing photo can't hold up the
    rest."""
    if not enabled():
        return
    paths = [(local_dir / name, name) for name in names]
    paths = [(p, n) for p, n in paths if p.is_file()]
    if not paths:
        return
    if len(paths) == 1:
        upload(paths[0][0], key_for(session_id, paths[0][1]))
        return
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(paths))) as pool:
        for path, name in paths:
            pool.submit(upload, path, key_for(session_id, name))


# --- Adobe hand-off helpers --------------------------------------------------
# Unlike the best-effort functions above, these RAISE on failure: the Adobe
# pipeline must know a staging step failed so it can keep the original photo
# and surface the reason, not discover it later as a cryptic job error.

def put_bytes(data: bytes, key: str, content_type: str) -> None:
    """Upload raw bytes to R2. Raises when R2 is unconfigured or the put fails."""
    client = _get_client()
    if client is None:
        raise RuntimeError("object storage (R2) is not configured")
    client.put_object(Bucket=config.R2_BUCKET, Key=key, Body=data,
                      ContentType=content_type)


def get_bytes(key: str) -> bytes:
    """Download an object's bytes from R2. Raises on failure."""
    client = _get_client()
    if client is None:
        raise RuntimeError("object storage (R2) is not configured")
    return client.get_object(Bucket=config.R2_BUCKET, Key=key)["Body"].read()


def presigned_get(key: str, expires: int = 3600) -> str:
    """Presigned GET URL (Adobe pulls job inputs from these). Raises on failure."""
    client = _get_client()
    if client is None:
        raise RuntimeError("object storage (R2) is not configured")
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": config.R2_BUCKET, "Key": key},
        ExpiresIn=expires)


def presigned_put(key: str, expires: int = 3600) -> str:
    """Presigned PUT URL (Adobe pushes job outputs to these). No Content-Type
    constraint — Adobe chooses its own header on the PUT. Raises on failure."""
    client = _get_client()
    if client is None:
        raise RuntimeError("object storage (R2) is not configured")
    return client.generate_presigned_url(
        "put_object", Params={"Bucket": config.R2_BUCKET, "Key": key},
        ExpiresIn=expires)


def restore(key: str, local_path: Path) -> bool:
    """Bring an offloaded object back to disk (atomically). Used at publish
    time so eBay always fetches a locally-served file — never a redirect to a
    short-lived signed URL whose handling by eBay's image fetcher we don't
    control. False on any failure; never raises."""
    try:
        client = _get_client()
        if client is None:
            return False
        data = client.get_object(Bucket=config.R2_BUCKET, Key=key)["Body"].read()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = local_path.with_name(local_path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(local_path)
        return True
    except Exception as exc:  # noqa: BLE001 - caller falls back to its clear error
        log.warning(f"objstore: restore of {key} failed: {exc}")
        return False


def exists(key: str) -> bool:
    """True when the object is really in the bucket. Used before freeing a
    local copy — the upload path is best-effort, so "we tried to upload it"
    is NOT proof, and deleting an un-uploaded photo would break a live
    listing's images. False on any doubt (including a client error)."""
    try:
        client = _get_client()
        if client is None:
            return False
        client.head_object(Bucket=config.R2_BUCKET, Key=key)
        return True
    except Exception:  # noqa: BLE001 - missing object or transient error
        return False


def delete(key: str) -> None:
    """Best-effort delete of an object from R2. Never raises."""
    try:
        client = _get_client()
        if client is None:
            return
        client.delete_object(Bucket=config.R2_BUCKET, Key=key)
    except Exception as exc:  # noqa: BLE001 - never break the request
        log.warning(f"objstore: delete failed: {exc}")

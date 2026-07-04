"""Cloudflare R2 (S3-compatible) object storage for optimized images.

Optional and resilient: if not configured, everything is a no-op and images
are served from local disk. When configured, optimized photos are uploaded to
R2 and served via the bucket's public URL, so they survive restarts and are
reliably fetchable by eBay.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import config

_client = None


def enabled() -> bool:
    return config.r2_ready()


def _get_client():
    global _client
    if not enabled():
        return None
    if _client is None:
        import boto3  # imported lazily so the dep is only needed when used

        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=config.R2_ACCESS_KEY_ID,
            aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
    return _client


def key_for(session_id: str, name: str) -> str:
    return f"sessions/{session_id}/optimized/{name}"


def public_url(key: str) -> str:
    return f"{config.R2_PUBLIC_BASE_URL}/{key}"


def upload(local_path: Path, key: str) -> Optional[str]:
    """Upload a file to R2 and return its public URL. None on failure/disabled."""
    try:
        client = _get_client()
        if client is None:
            return None
        client.upload_file(
            str(local_path), config.R2_BUCKET, key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        return public_url(key)
    except Exception as exc:  # noqa: BLE001 - never break the request
        print(f"[objstore] upload failed: {exc}")
        return None


def upload_optimized(session_id: str, local_dir: Path, names: list[str]) -> None:
    """Best-effort upload of a session's optimized images to R2."""
    if not enabled():
        return
    for name in names:
        path = local_dir / name
        if path.is_file():
            upload(path, key_for(session_id, name))

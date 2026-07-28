"""Pull eBay-hosted listing photos into the app's own storage.

The app is the source of truth for every editable image; eBay is only a
publishing destination. EPS images (ebayimg.com) can't be edited in place and
can't be safely loaded into the browser editor (CORS taints the canvas), so an
imported listing's photos are downloaded HERE — server-side, from an
allowlisted host — re-encoded (which strips EXIF), stored in the listing's
session directory like any uploaded photo, and edited as local copies from
then on. The live EPS URLs stay on the listing as sync references only.

A manifest (ebay_images.json in the session dir) records each imported file's
checksum, so publishing can tell "unchanged since import" (reuse the existing
EPS URL — no re-upload churn) from "edited locally" (send our /media URL so
eBay ingests the new version).

This is deliberately NOT a general image proxy: HTTPS only, eBay's image CDN
only, bounded size, bounded redirects, image-decode validation.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .. import storage
from ..config import log

# Only eBay's image CDN. Suffix-matched against the URL host, so i.ebayimg.com
# and thumbs*.ebayimg.com pass but lookalikes (evil-ebayimg.com) don't.
_ALLOWED_HOST_SUFFIXES = (".ebayimg.com",)
_ALLOWED_HOSTS = ("ebayimg.com",)
_MAX_BYTES = 15 * 1024 * 1024
_MAX_REDIRECTS = 3
_TIMEOUT = 15.0
_FETCH_WORKERS = 4
# Working copies match the app's own optimized photos: JPEG, longest side
# capped. No square crop or enhancement — an imported photo must look exactly
# like the live listing until the seller edits it.
_MAX_SIDE = 1600
_MANIFEST = "ebay_images.json"


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host in _ALLOWED_HOSTS or host.endswith(_ALLOWED_HOST_SUFFIXES)


def fetch_ebay_image(url: str) -> bytes:
    """Download one image from eBay's CDN with the full guard rail: HTTPS
    only, allowlisted host (re-checked on every redirect hop), bounded
    redirects, bounded size, and the bytes must decode as an image.
    Raises ValueError with the reason on any violation or failure."""
    import httpx

    for _hop in range(_MAX_REDIRECTS + 1):
        parts = urlparse(url)
        if parts.scheme != "https":
            raise ValueError(f"not https: {url[:120]}")
        if not _host_allowed(parts.hostname or ""):
            raise ValueError(f"host not allowed: {parts.hostname}")
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            url = resp.headers.get("location", "")
            if not url:
                raise ValueError("redirect with no location")
            continue
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}")
        data = resp.content
        break
    else:
        raise ValueError("too many redirects")
    if len(data) > _MAX_BYTES:
        raise ValueError(f"image too large ({len(data)} bytes)")
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and not ctype.startswith("image/"):
        raise ValueError(f"not an image: {ctype}")
    return data


def _manifest_path(record_id: str) -> Path:
    return storage.session_dir(record_id) / _MANIFEST


def _load_manifest(record_id: str) -> dict:
    try:
        p = _manifest_path(record_id)
        if p.is_file():
            return json.loads(p.read_text())
    except Exception as exc:  # noqa: BLE001 - a bad manifest just means "changed"
        log.info("image import: unreadable manifest for %s: %s", record_id, exc)
    return {}


def _save_manifest(record_id: str, manifest: dict) -> None:
    storage.ensure_session(record_id)
    _manifest_path(record_id).write_text(json.dumps(manifest, indent=2))


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_listing_images(record_id: str, urls: list[str]) -> list[str]:
    """Download an imported listing's photos into its session storage and
    return the local working-copy names, in eBay's order. The raw download is
    kept as the immutable original; the working copy is a clean re-encoded
    JPEG (EXIF gone) capped to the app's standard size, visually identical to
    what's live. Photos that fail to download are skipped (logged) so one dead
    URL doesn't block the rest. Returns [] when nothing could be imported."""
    from io import BytesIO
    from PIL import Image, ImageOps

    urls = [u for u in urls if u][:24]
    if not urls:
        return []
    orig = storage.original_dir(record_id)
    opt = storage.optimized_dir(record_id)

    def _one(job: tuple[int, str]) -> Optional[tuple[int, str, str, str]]:
        i, url = job
        try:
            data = fetch_ebay_image(url)
            img = Image.open(BytesIO(data))
            img.load()
            img = ImageOps.exif_transpose(img).convert("RGB")
            if max(img.size) > _MAX_SIDE:
                img.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)
            (orig / f"src_{i:02d}.jpg").write_bytes(data)
            name = f"img_{i:02d}.jpg"
            img.save(opt / name, "JPEG", quality=88, optimize=True)
            return i, name, file_sha(opt / name), url
        except Exception as exc:  # noqa: BLE001 - skip one bad photo
            log.warning("image import: photo %d of %s failed: %s", i, record_id, exc)
            return None

    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(urls))) as pool:
        results = [r for r in pool.map(_one, list(enumerate(urls))) if r]
    results.sort()
    if not results:
        return []
    manifest = {
        "imported_at": int(time.time()),
        "images": {name: {"sha256": sha, "source_url": url}
                   for _i, name, sha, url in results},
    }
    _save_manifest(record_id, manifest)
    log.info("image import: %s — %d/%d photos copied into app storage",
             record_id, len(results), len(urls))
    return [name for _i, name, _sha, _url in results]


def images_changed(record_id: str, names: list[str]) -> bool:
    """True when the listing's local photos differ from what eBay last got —
    an edit, a re-order won't show here (order lives on the listing), an
    added or removed photo, or no baseline at all. Drives publish sync:
    changed → send our /media URLs so eBay ingests fresh copies; unchanged →
    reuse the existing EPS URLs and skip the churn."""
    recorded = (_load_manifest(record_id).get("images") or {})
    if not recorded:
        return True
    # Order matters: a reorder alone must republish (the first photo is the
    # gallery image), and adds/removes obviously do. Manifest dicts keep
    # insertion order, which mark_synced writes in listing order.
    if list(names) != list(recorded.keys()):
        return True
    opt = storage.optimized_dir(record_id)
    for name in names:
        p = opt / name
        if not p.is_file() or file_sha(p) != recorded[name].get("sha256"):
            return True
    return False


def mark_synced(record_id: str, names: list[str]) -> None:
    """Re-baseline the manifest to the current files after eBay successfully
    ingested them, so the next unedited publish reuses EPS URLs again."""
    manifest = _load_manifest(record_id)
    old = manifest.get("images") or {}
    opt = storage.optimized_dir(record_id)
    manifest["images"] = {
        name: {"sha256": file_sha(opt / name),
               "source_url": old.get(name, {}).get("source_url", "")}
        for name in names if (opt / name).is_file()
    }
    manifest["synced_at"] = int(time.time())
    _save_manifest(record_id, manifest)

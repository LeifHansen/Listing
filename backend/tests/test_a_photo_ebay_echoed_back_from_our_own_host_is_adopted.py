"""A mirror whose photo URLs are this app's own /media URLs can still be edited.

eBay does not always copy a picture onto its CDN. A listing this app published
carries the /media URL it handed over, and a store sync reads that URL straight
back into the mirror record's image_urls. Adopting the mirror for editing then
asked the CDN allowlist to fetch app.thryftshop.com — refused, "host not
allowed", twelve times in one day, for photos that were on this very disk.
The mirror stayed read-only and the seller could not fill in its details.

Now the app's own URLs are recognised (on its own origins only) and the bytes
come from the volume, or from R2 once the offload sweep has moved them.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from io import BytesIO  # noqa: E402

from PIL import Image  # noqa: E402

from backend import config, objstore, storage  # noqa: E402
from backend.services import image_import  # noqa: E402


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(config, "APP_ORIGINS",
                        ("https://app.thryftshop.com", "https://listing-lfwjrg.fly.dev"))
    return tmp_path


def _jpeg(color) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.mark.parametrize("url,want", [
    ("https://app.thryftshop.com/media/abc123/optimized/img_000.jpg", ("abc123", "img_000.jpg")),
    ("https://listing-lfwjrg.fly.dev/media/abc123/optimized/img_001.jpg?v=9", ("abc123", "img_001.jpg")),
    ("http://localhost:8000/media/abc123/optimized/img_000.jpg", ("abc123", "img_000.jpg")),
    ("https://i.ebayimg.com/images/g/abc/s-l1600.jpg", None),
    ("https://evil.example.com/media/abc123/optimized/img_000.jpg", None),
    ("https://app.thryftshop.com/media/abc123/original/img_000.jpg", None),
    ("https://app.thryftshop.com/media/abc123/optimized/..", None),
])
def test_only_our_own_media_urls_are_recognised(data_dir, url, want):
    assert image_import.own_media_ref(url) == want


def test_the_photo_is_copied_from_the_volume(data_dir, monkeypatch):
    src = storage.optimized_dir("published-1")
    (src / "img_000.jpg").write_bytes(_jpeg("red"))

    def no_network(url):
        raise AssertionError(f"fetched over the network: {url}")
    monkeypatch.setattr(image_import, "fetch_ebay_image", no_network)

    names = image_import.import_listing_images(
        "ebay-555", ["https://app.thryftshop.com/media/published-1/optimized/img_000.jpg"])

    assert names == ["img_00.jpg"]
    assert (storage.optimized_dir("ebay-555") / "img_00.jpg").is_file()


def test_the_photo_comes_back_from_r2_once_offloaded(data_dir, monkeypatch):
    monkeypatch.setattr(objstore, "enabled", lambda: True)
    keys: list[str] = []

    def fake_get_bytes(key):
        keys.append(key)
        return _jpeg("blue")
    monkeypatch.setattr(objstore, "get_bytes", fake_get_bytes)

    names = image_import.import_listing_images(
        "ebay-556", ["https://app.thryftshop.com/media/published-2/optimized/img_003.jpg"])

    assert names == ["img_00.jpg"]
    assert keys == [objstore.key_for("published-2", "img_003.jpg")]


def test_a_photo_that_is_nowhere_is_one_missing_photo_not_a_crash(data_dir, monkeypatch):
    monkeypatch.setattr(objstore, "enabled", lambda: False)
    names = image_import.import_listing_images(
        "ebay-557", ["https://app.thryftshop.com/media/published-3/optimized/img_000.jpg"])
    assert names == []

"""A photo the reclaim pass moved to object storage is still the seller's photo.

Etsy takes photo BYTES (eBay takes a URL), and the Etsy publish used to read
them from the volume alone. The R2 reclaim pass frees a listing's local
copies once the bucket holds them, so a listing whose photos had been
offloaded uploaded nothing to Etsy and was then refused activation for
having no image — on a listing with twelve. The read now goes through the
same volume-then-bucket path the adoption code uses.
"""
from __future__ import annotations

import httpx
import pytest

from backend import objstore
from backend.marketplaces import etsy_provider
from backend.marketplaces.base import PublishContext
from backend.models import Listing
from backend.services import image_import


@pytest.fixture
def offloaded(monkeypatch, tmp_path):
    """No file on the volume; the bucket has it; the network is off limits."""
    monkeypatch.setattr(image_import.storage, "optimized_path",
                        lambda sid: tmp_path / sid / "optimized")
    monkeypatch.setattr(objstore, "enabled", lambda: True)
    keys = []

    def _get_bytes(key):
        keys.append(key)
        return b"JPEG-FROM-R2"

    monkeypatch.setattr(objstore, "get_bytes", _get_bytes)

    def _never(*a, **k):
        raise AssertionError("no photo should be fetched over the network")

    monkeypatch.setattr(httpx, "get", _never)
    return keys


def _ctx(**listing) -> PublishContext:
    fields = dict(title="Mug", images=["a.jpg"], image_urls=[])
    fields.update(listing)
    return PublishContext(session_id="s-r2", listing=Listing(**fields), mode="live",
                          base_url="https://app.test", uid="u1", prev_record={})


def test_a_photo_in_the_bucket_is_uploaded(offloaded):
    batches = etsy_provider.EtsyProvider()._image_batches(_ctx())
    assert batches == [("a.jpg", b"JPEG-FROM-R2")]
    assert offloaded == [objstore.key_for("s-r2", "a.jpg")]


def test_the_bucket_wins_over_the_ebay_copy(offloaded):
    """image_urls is the fallback for a listing with no photos of its own;
    a photo that exists in the bucket is one of its own."""
    batches = etsy_provider.EtsyProvider()._image_batches(
        _ctx(image_urls=["https://i.ebayimg.com/x.jpg"]))
    assert batches == [("a.jpg", b"JPEG-FROM-R2")]


def test_a_photo_nobody_holds_any_more_is_one_photo_short_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(image_import.storage, "optimized_path",
                        lambda sid: tmp_path / sid / "optimized")
    monkeypatch.setattr(objstore, "enabled", lambda: False)
    ctx = _ctx(images=["gone.jpg", "also-gone.jpg"])
    assert etsy_provider.EtsyProvider()._image_batches(ctx) == []

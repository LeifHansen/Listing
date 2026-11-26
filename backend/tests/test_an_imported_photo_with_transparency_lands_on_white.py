"""An imported photo with transparency lands on white, like every other photo.

The import path decoded and re-encoded with its own `.convert("RGB")` rather
than the photo pass's helpers, and a bare convert paints transparent pixels
BLACK (see test_a_transparent_photo_does_not_go_black.py). It also wrote the
working copy straight to its final name, where a reader -- eBay fetching
/media mid-publish -- could see a torn JPEG. It goes through images.flatten
and images.write_jpeg now, which already answer both.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from io import BytesIO  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

from backend import storage  # noqa: E402
from backend.services import image_import  # noqa: E402


def _cut_out_png() -> bytes:
    img = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((150, 150, 450, 450), fill=(190, 120, 60, 255))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_the_backdrop_is_white_not_black(monkeypatch):
    monkeypatch.setattr(image_import, "fetch_ebay_image", lambda url: _cut_out_png())
    sid = storage.new_session_id()
    names = image_import.import_listing_images(sid, ["https://i.ebayimg.com/a.png"])
    assert names == ["img_000.jpg"]
    with Image.open(storage.optimized_dir(sid) / names[0]) as saved:
        corner = saved.convert("RGB").getpixel((5, 5))
        middle = saved.convert("RGB").getpixel((300, 300))
    assert min(corner) > 240, f"backdrop came out dark: {corner}"
    assert middle[0] > 150 and middle[2] < 100  # the item itself is untouched
    assert not list(storage.optimized_dir(sid).glob("*.tmp"))

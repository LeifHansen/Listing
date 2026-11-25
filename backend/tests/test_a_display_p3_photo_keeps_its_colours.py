"""A photo shot in Display P3 reaches the listing in the colours it was shot in.

The listing JPEG carries no metadata -- so the GPS of the seller's home never
rides along -- and that includes the colour profile. An iPhone shoots Display
P3, and the pass dropped the profile while keeping the P3 numbers. Every
viewer then read them as sRGB, so every listing photo from an iPhone came out
duller than the item: a red jumper a shade of brick.

The fix is to move the pixels into sRGB before the profile goes, which keeps
the no-metadata rule and the colours both.
"""
from __future__ import annotations

import struct

import pytest

pytest.importorskip("PIL")

from io import BytesIO  # noqa: E402

from PIL import Image, ImageCms  # noqa: E402

from backend.services import images  # noqa: E402

SHOT = (40, 160, 60)  # a P3 green; in sRGB it is more saturated still


def _s15(v: float) -> bytes:
    return struct.pack(">i", round(v * 65536))


def _p3_profile() -> bytes:
    """A minimal ICC v2 matrix/TRC profile with Display P3's primaries --
    Pillow ships only sRGB, so the test carries its own."""
    def xyz(x, y, z):
        return b"XYZ " + b"\0" * 4 + _s15(x) + _s15(y) + _s15(z)

    def curv(gamma):
        return b"curv" + b"\0" * 4 + struct.pack(">IH", 1, round(gamma * 256)) + b"\0\0"

    text = b"Display P3 (test)\0"
    desc = (b"desc" + b"\0" * 4 + struct.pack(">I", len(text)) + text
            + struct.pack(">IIHB", 0, 0, 0, 0) + b"\0" * 67)
    desc += b"\0" * (-len(desc) % 4)
    tags = [(b"desc", desc), (b"wtpt", xyz(0.9642, 1.0, 0.8249)),
            (b"rXYZ", xyz(0.5151, 0.2412, -0.0011)),
            (b"gXYZ", xyz(0.2919, 0.6922, 0.0419)),
            (b"bXYZ", xyz(0.1572, 0.0666, 0.7841)),
            (b"rTRC", curv(2.2)), (b"gTRC", curv(2.2)), (b"bTRC", curv(2.2))]
    offset = 128 + 4 + 12 * len(tags)
    table, data = b"", b""
    for sig, body in tags:
        table += sig + struct.pack(">II", offset + len(data), len(body))
        data += body
    header = (struct.pack(">I", offset + len(data)) + b"\0" * 4
              + struct.pack(">I", 0x02100000) + b"mntrRGB XYZ " + b"\0" * 12
              + b"acsp" + b"\0" * 28 + _s15(0.9642) + _s15(1.0) + _s15(0.8249)
              + b"\0" * 48)
    return header + struct.pack(">I", len(tags)) + table + data


def _p3_photo(tmp_path):
    icc = _p3_profile()
    exif = Image.Exif()
    exif[0x8825] = {2: (37.0, 46.0, 30.0)}  # a GPS block that must not survive
    src = tmp_path / "iphone.jpg"
    Image.new("RGB", (800, 600), SHOT).save(src, quality=95, icc_profile=icc,
                                            exif=exif)
    return src, icc


def test_the_listing_photo_shows_the_colour_that_was_shot(tmp_path):
    src, icc = _p3_photo(tmp_path)
    images.optimize(src, tmp_path / "out")
    want = ImageCms.profileToProfile(
        Image.new("RGB", (1, 1), SHOT), ImageCms.ImageCmsProfile(BytesIO(icc)),
        ImageCms.createProfile("sRGB"), outputMode="RGB").getpixel((0, 0))
    with Image.open(tmp_path / "out.jpg") as saved:
        got = saved.convert("RGB").getpixel((400, 300))
    assert want != SHOT  # the conversion is a real change, not a no-op
    assert max(abs(a - b) for a, b in zip(got, want)) <= 4, (got, want)


def test_the_saved_photo_still_carries_no_metadata(tmp_path):
    src, _icc = _p3_photo(tmp_path)
    images.optimize(src, tmp_path / "out")
    with Image.open(tmp_path / "out.jpg") as saved:
        assert not saved.info.get("icc_profile")
        assert not saved.info.get("exif")


def test_an_untagged_photo_is_left_alone(tmp_path):
    src = tmp_path / "plain.jpg"
    Image.new("RGB", (800, 600), SHOT).save(src, quality=95)
    images.optimize(src, tmp_path / "out")
    with Image.open(tmp_path / "out.jpg") as saved:
        got = saved.convert("RGB").getpixel((400, 300))
    assert max(abs(a - b) for a, b in zip(got, SHOT)) <= 3

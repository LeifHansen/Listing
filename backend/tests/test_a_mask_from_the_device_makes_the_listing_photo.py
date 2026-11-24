"""A matte made on the seller's device becomes the listing photo -- the same
one Photoroom's would have made.

The background now comes off on the seller's own phone or browser (Apple
Vision, ML Kit, a model in the page), and each engine answers only one
question: how opaque is every pixel of this photo. services/cutouts turns
that matte into the listing photo with the SAME compositor Photoroom's
cutouts use, so a pile cut by three engines still looks like one shoot.

Pinned here, on Pillow alone (this file runs in the `cutout` CI job):

  * one matte, one photo: a device matte and a Photoroom matte of the same
    shape produce the same listing photo;
  * the matte stays SOFT -- clamped at the extremes, never binarised -- and
    is resized to the photo, feathered only where it was enlarged a lot;
  * a matte of some other frame (wrong aspect), an unreadable one, and one
    that kept nothing are refused with a sentence, never shipped;
  * "kept nothing" still cuts a flat rectangular thing to its own edge;
  * a PICTURE is never cut into by a device matte: the matte may only
    locate its edge, and a subject lifted out of a painting is refused;
  * in device mode the photo pass spends nothing: an ordinary photo waits
    for the device, a picture with a findable border is cut by geometry, a
    close-up is kept as shot -- and no Photoroom call is made for any;
  * every cut keeps the photo it was cut FROM and the matte it was cut
    THROUGH, under the rule that a later edit makes both stale;
  * the seller's background colour reaches the composite;
  * what the screen said about a photo (picture, close-up) is written down
    and travels with it.
"""
from __future__ import annotations

import os
import time
from io import BytesIO
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import cutout_api, cutouts, images  # noqa: E402

W, H = 400, 300


def _photo(bg=(235, 235, 235), item=(180, 40, 40)) -> Image.Image:
    """A red item on a pale backdrop."""
    img = Image.new("RGB", (W, H), bg)
    ImageDraw.Draw(img).ellipse((120, 70, 280, 230), fill=item)
    return img


def _matte(size=(W, H), box=(120, 70, 280, 230), soft=True) -> Image.Image:
    m = Image.new("L", size, 0)
    sx, sy = size[0] / W, size[1] / H
    ImageDraw.Draw(m).ellipse(
        (box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy), fill=255)
    if soft:
        from PIL import ImageFilter
        m = m.filter(ImageFilter.GaussianBlur(3))
    return m


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def no_photoroom(monkeypatch):
    """Nothing in this file may reach Photoroom unless a test says so."""
    def _never(_img):
        raise AssertionError("Photoroom was called")
    monkeypatch.setattr(cutout_api, "photoroom_cutout", _never)


# --- one compositor ---------------------------------------------------------------

def test_a_device_matte_makes_the_photo_photoroom_would(monkeypatch):
    rgb = _photo()
    alpha = _matte()
    device = cutouts.cut_from_mask(rgb, alpha)

    def _photoroom(img):
        out = img.convert("RGBA")
        out.putalpha(cutouts.refine_alpha(alpha, img.size))
        return out
    monkeypatch.setattr(cutout_api, "photoroom_cutout", _photoroom)
    cloud, engine = images.cutout_with_engine(rgb)

    assert engine == "photoroom" and device.engine == "device"
    assert device.image.tobytes() == cloud.tobytes()


def test_the_backdrop_goes_white_and_the_item_stays():
    out = cutouts.cut_from_mask(_photo(), _matte()).image
    assert out.getpixel((10, 10)) == (255, 255, 255)
    r, g, b = out.getpixel((200, 150))
    assert r > 150 and g < 80 and b < 80


# --- soft, never binarised -----------------------------------------------------------

def test_the_soft_band_survives():
    alpha = cutouts.refine_alpha(_matte(soft=True), (W, H))
    values = set(alpha.tobytes())
    assert any(30 < v < 220 for v in values), "the soft edge was binarised"


def test_haze_and_speckle_are_clamped():
    m = Image.new("L", (W, H), 5)                 # faint haze everywhere
    ImageDraw.Draw(m).rectangle((100, 100, 300, 200), fill=250)  # near-opaque
    alpha = cutouts.refine_alpha(m, (W, H))
    assert alpha.getpixel((10, 10)) == 0
    assert alpha.getpixel((200, 150)) == 255


def test_a_small_matte_is_scaled_to_the_photo():
    small = _matte(size=(W // 4, H // 4), soft=False)
    alpha = cutouts.refine_alpha(small, (W, H))
    assert alpha.size == (W, H)
    assert alpha.getpixel((200, 150)) == 255 and alpha.getpixel((5, 5)) == 0


def test_a_square_model_matte_is_not_a_matte_of_this_photo():
    """A 1024x1024 model output must be scaled back to the photo's shape on
    the device; a square one for a 4:3 photo is some other frame."""
    with pytest.raises(cutouts.MaskRefused):
        cutouts.cut_from_mask(_photo(), _matte(size=(256, 256)))


def test_rounding_one_pixel_is_not_a_different_photo():
    cutouts.check_shape(Image.new("L", (401, 300)), (W, H))
    cutouts.check_shape(Image.new("L", (100, 75)), (W, H))


# --- what a matte must be -----------------------------------------------------------

def test_an_unreadable_matte_is_refused_with_a_sentence():
    for data in (b"", b"not a png", b"\x89PNG\r\n\x1a\nbroken"):
        with pytest.raises(cutouts.MaskRefused) as err:
            cutouts.read_mask(data)
        assert "kept as shot" in str(err.value)


def test_a_huge_matte_is_refused():
    with pytest.raises(cutouts.MaskRefused):
        cutouts.read_mask(_png(Image.new("L", (cutouts.MAX_SIDE + 1, 10))))


def test_a_grey_matte_drawn_into_rgb_reads_by_brightness():
    rgb = Image.merge("RGB", [_matte()] * 3)
    got = cutouts.read_mask(_png(rgb))
    assert got.mode == "L" and got.getpixel((200, 150)) == 255


def test_a_cutout_png_reads_by_its_alpha():
    rgba = _photo().convert("RGBA")
    rgba.putalpha(_matte(soft=False))
    got = cutouts.read_mask(_png(rgba))
    assert got.getpixel((200, 150)) == 255 and got.getpixel((5, 5)) == 0


def test_a_matte_that_kept_nothing_is_refused():
    with pytest.raises(cutouts.NothingKept) as err:
        cutouts.cut_from_mask(_photo(), Image.new("L", (W, H), 0))
    assert "kept as shot" in str(err.value)


def test_a_thin_product_is_still_a_product():
    """A chain laid on a sweep keeps ~1.5% of the frame; "found nothing" is
    0.0000-0.0017. Only nothing is refused."""
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).line((20, 150, 380, 160), fill=255, width=6)
    out = cutouts.cut_from_mask(_photo(), m)
    assert out.engine == "device"


def test_kept_nothing_still_cuts_a_flat_rectangle_to_its_edge():
    img = Image.new("RGB", (W, H), (60, 60, 60))
    ImageDraw.Draw(img).rectangle((80, 60, 320, 240), fill=(240, 220, 120))
    out = cutouts.cut_from_mask(img, Image.new("L", (W, H), 0))
    assert out.engine == "border"


# --- a picture is never cut into --------------------------------------------------------

def _painting_on_a_matching_floor() -> Image.Image:
    """A pale print on a pale floor -- the photo whose edge the geometric
    scan cannot find, so a matte is asked where it is."""
    img = Image.new("RGB", (W, H), (205, 200, 196))
    ImageDraw.Draw(img).rectangle((60, 40, 340, 260), fill=(210, 204, 200))
    ImageDraw.Draw(img).ellipse((170, 120, 230, 180), fill=(40, 90, 160))
    return img


def test_a_device_matte_only_locates_a_pictures_edge(monkeypatch):
    monkeypatch.setattr(images.artwork, "outline", lambda _rgb: None)
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rectangle((60, 40, 340, 260), fill=255)
    cut = cutouts.cut_from_mask(_painting_on_a_matching_floor(), m, art=True)
    assert cut.engine == "art"
    # Solid inside the edge: every pixel of the picture is kept.
    inside = cut.alpha.crop((70, 50, 330, 250))
    assert inside.getextrema() == (255, 255)


def test_the_subject_lifted_out_of_a_painting_is_refused(monkeypatch):
    """What any salient-object engine answers for a painting: the subject.
    It is not a rectangle at any angle, so nothing is cut at all."""
    monkeypatch.setattr(images.artwork, "outline", lambda _rgb: None)
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).ellipse((170, 120, 230, 180), fill=255)
    with pytest.raises(cutouts.MaskRefused) as err:
        cutouts.cut_from_mask(_painting_on_a_matching_floor(), m, art=True)
    assert "picture" in str(err.value)


# --- device mode spends nothing ----------------------------------------------------------

def _session(tmp_path: Path, n: int = 1) -> tuple[Path, Path]:
    src_dir = tmp_path / "sess" / "original"
    opt = tmp_path / "sess" / "optimized"
    src_dir.mkdir(parents=True)
    opt.mkdir(parents=True)
    for i in range(n):
        _photo().save(src_dir / f"src_{i:03d}.jpg", "JPEG", quality=95)
    return src_dir, opt


def test_an_ordinary_photo_waits_for_the_device(tmp_path):
    src_dir, opt = _session(tmp_path)
    out = images.optimize(src_dir / "src_000.jpg", opt / "img_000.jpg",
                          remove_bg=True, device=True)
    assert out["bg_pending"] == "item"
    assert out["background_removed"] is False and "bg_error" not in out
    assert not (opt.parent / "as_shot").exists()


def test_a_close_up_is_kept_as_shot_and_never_waits(tmp_path):
    src_dir, opt = _session(tmp_path)
    out = images.optimize(src_dir / "src_000.jpg", opt / "img_000.jpg",
                          remove_bg=True, device=True, detail=True)
    assert out["bg_error"] == images.DETAIL_KEPT_AS_SHOT
    assert "bg_pending" not in out and out["detail"] is True


def test_a_picture_with_a_findable_edge_is_cut_by_geometry_now(tmp_path):
    src_dir = tmp_path / "sess" / "original"
    opt = tmp_path / "sess" / "optimized"
    src_dir.mkdir(parents=True)
    opt.mkdir(parents=True)
    img = Image.new("RGB", (W, H), (40, 40, 40))
    ImageDraw.Draw(img).rectangle((80, 60, 320, 240), fill=(230, 200, 120))
    img.save(src_dir / "src_000.jpg", "JPEG", quality=95)
    out = images.optimize(src_dir / "src_000.jpg", opt / "img_000.jpg",
                          remove_bg=True, device=True, art=True)
    assert out["background_removed"] is True and out["bg_engine"] == "art"
    assert "bg_pending" not in out


def test_a_picture_without_a_findable_edge_waits_as_art(tmp_path, monkeypatch):
    monkeypatch.setattr(images.artwork, "outline", lambda _rgb: None)
    src_dir, opt = _session(tmp_path)
    out = images.optimize(src_dir / "src_000.jpg", opt / "img_000.jpg",
                          remove_bg=True, device=True, art=True)
    assert out["bg_pending"] == "art" and out["art"] is True


# --- what a cut keeps beside it ------------------------------------------------------------

def test_a_cloud_cut_keeps_the_shot_and_the_matte(tmp_path, monkeypatch):
    def _photoroom(img):
        out = img.convert("RGBA")
        out.putalpha(cutouts.refine_alpha(_matte(), img.size))
        return out
    monkeypatch.setattr(cutout_api, "photoroom_cutout", _photoroom)
    src_dir, opt = _session(tmp_path)
    dst = opt / "img_000.jpg"
    out = images.optimize(src_dir / "src_000.jpg", dst, remove_bg=True)
    assert out["background_removed"] is True
    assert images.current_mask(dst) is not None
    assert images.as_shot(dst) != dst
    assert cutouts.parts(dst) is not None


def test_write_cut_keeps_the_source_byte_for_byte_and_newer(tmp_path):
    _src_dir, opt = _session(tmp_path)
    dst = opt / "img_000.jpg"
    _photo().save(dst, "JPEG", quality=90)
    source = dst.read_bytes()
    cut = cutouts.cut_from_mask(cutouts.decode(source), _matte())
    cutouts.write_cut(dst, source, cut)

    kept = opt.parent / "as_shot" / "img_000.jpg"
    assert kept.read_bytes() == source
    assert images.as_shot(dst) == kept
    assert images.current_mask(dst) is not None
    rgb, alpha = cutouts.parts(dst)
    assert alpha.size == rgb.size == (W, H)


def test_a_second_cut_starts_from_the_shot_not_the_composite(tmp_path):
    _src_dir, opt = _session(tmp_path)
    dst = opt / "img_000.jpg"
    _photo().save(dst, "JPEG", quality=90)
    first = dst.read_bytes()
    cutouts.write_cut(dst, first, cutouts.cut_from_mask(cutouts.decode(first),
                                                        _matte()))
    # The source a second matte must be made from is the shot again.
    assert cutouts.source_path(dst).read_bytes() == first


def test_an_edit_after_the_cut_makes_the_kept_files_stale(tmp_path):
    _src_dir, opt = _session(tmp_path)
    dst = opt / "img_000.jpg"
    _photo().save(dst, "JPEG")
    source = dst.read_bytes()
    cutouts.write_cut(dst, source, cutouts.cut_from_mask(cutouts.decode(source),
                                                         _matte()))
    later = time.time() + 5
    images.write_jpeg(Image.new("RGB", (W, H), (1, 2, 3)), dst)
    os.utime(dst, (later, later))
    assert images.as_shot(dst) == dst
    assert images.current_mask(dst) is None
    assert cutouts.parts(dst) is None


def test_the_transparent_export_is_the_item_on_nothing(tmp_path):
    png = cutouts.transparent_png(_photo(), cutouts.refine_alpha(_matte(), (W, H)))
    with Image.open(BytesIO(png)) as im:
        assert im.mode == "RGBA"
        assert im.getpixel((5, 5))[3] == 0
        assert im.getpixel((200, 150))[3] == 255


# --- the seller's colour --------------------------------------------------------------------

def test_the_sellers_colour_is_the_background():
    colour = cutouts.parse_colour("#1e90ff")
    out = cutouts.cut_from_mask(_photo(), _matte(), background=colour).image
    assert out.getpixel((5, 5)) == colour


def test_a_colour_is_hex_or_nothing():
    assert cutouts.parse_colour("ff0000") == (255, 0, 0)
    assert cutouts.colour_hex((255, 0, 0)) == "#ff0000"
    for bad in ("", "red", "#fff", "#12345g", None, "#1234567"):
        assert cutouts.parse_colour(bad) is None, bad


def test_the_device_path_honours_the_colour_for_a_picture(tmp_path):
    src_dir = tmp_path / "sess" / "original"
    opt = tmp_path / "sess" / "optimized"
    src_dir.mkdir(parents=True)
    opt.mkdir(parents=True)
    img = Image.new("RGB", (W, H), (40, 40, 40))
    ImageDraw.Draw(img).rectangle((80, 60, 320, 240), fill=(230, 200, 120))
    img.save(src_dir / "src_000.jpg", "JPEG", quality=95)
    images.optimize(src_dir / "src_000.jpg", opt / "img_000.jpg",
                    remove_bg=True, device=True, art=True,
                    background=(0, 0, 0))
    with Image.open(opt / "img_000.jpg") as out:
        r, g, b = out.convert("RGB").getpixel((3, 3))
        assert max(r, g, b) < 30


# --- what the screen said is written down ------------------------------------------------------

def test_the_screens_answers_are_kept_and_travel(tmp_path):
    _src_dir, opt = _session(tmp_path)
    dst = opt / "img_003.jpg"
    _photo().save(dst, "JPEG")
    images.note_flags(dst, art=True)
    assert images.photo_flags(dst) == {"art": True, "detail": False}

    item = tmp_path / "item" / "optimized"
    item.mkdir(parents=True)
    images.carry_flags(opt, item, {"img_003.jpg": "img_000.jpg"})
    assert images.photo_flags(item / "img_000.jpg")["art"] is True

    images.forget_flags(dst)
    assert images.photo_flags(dst) == {"art": False, "detail": False}


def test_a_plain_photo_writes_nothing_down(tmp_path):
    _src_dir, opt = _session(tmp_path)
    dst = opt / "img_000.jpg"
    images.note_flags(dst)
    assert not (opt.parent / "photo_flags.json").exists()


def test_the_batch_tells_the_caller_as_each_photo_finishes(tmp_path):
    src_dir, opt = _session(tmp_path, n=3)
    seen = []
    images.optimize_all(src_dir, opt, remove_bg=True, device=True,
                        on_result=lambda dst, res: seen.append(
                            (dst.name, res.get("bg_pending"))))
    assert seen == [("img_000.jpg", "item"), ("img_001.jpg", "item"),
                    ("img_002.jpg", "item")]

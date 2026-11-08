"""The photo pass does three things, and the seller does the rest.

It keeps a photo as shot, it takes the background off when asked -- one
Photoroom cutout, the item on white under a soft contact shadow -- and it turns
the photo the way the camera meant. The pipeline this replaced also guessed
whether the ITEM lay sideways (two vision calls, written around a shirt),
rebuilt the matte's border, repaired holes in it, cropped square around a
detected subject, sharpened, and could hand the photo to three paid APIs.

The orientation guess came back on its own, rebuilt for objects and held to
its own rules -- test_the_item_is_turned_upright_whatever_it_is.py. It is
off throughout this file (conftest), so everything here is the pass with no
model asked which way is up.

The contact shadow went out with them and was asked for back on its own: it
is drawn from the silhouette the engine already produced, so unlike the rest
it costs no extra call and cannot change what the item looks like -- and an
item on flat white with a hard edge reads as a sticker pasted down. Every photo cost a minute and the
result was a surprise; the seller asked for the photos as shot or cut out,
and to do any further editing themselves.

Photoroom is stood in for by a matte the test draws itself, so this runs
without a key or the network: what is under test is what the pass does with a
matte, not the matte.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from pathlib import Path  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import cutout_api, images  # noqa: E402

BACKDROP = (238, 236, 232)
ITEM = (40, 50, 70)


def _photo(tmp: Path, size=(2400, 1800), name="src.jpg") -> Path:
    """A dark item in the middle third of a light backdrop."""
    img = Image.new("RGB", size, BACKDROP)
    w, h = size
    ImageDraw.Draw(img).rounded_rectangle(
        (w // 3, h // 3, 2 * w // 3, 2 * h // 3), radius=30, fill=ITEM)
    src = tmp / name
    img.save(src, "JPEG", quality=92)
    return src


def _matte_from_darkness(rgb: Image.Image) -> Image.Image:
    """What a good model would say about that photo: the dark item is
    subject, the light backdrop is not."""
    return rgb.convert("L").point(lambda v: 255 if v < 128 else 0)


@pytest.fixture()
def model(monkeypatch):
    """Stand in for Photoroom. Set `model.matte` to choose its answer."""
    state = {"matte": _matte_from_darkness, "calls": 0}

    def _photoroom(rgb):
        state["calls"] += 1
        cut = rgb.convert("RGBA")
        cut.putalpha(state["matte"](rgb))
        return cut

    monkeypatch.setattr(cutout_api, "photoroom_cutout", _photoroom)

    class _Model:
        @property
        def matte(self):
            return state["matte"]

        @matte.setter
        def matte(self, fn):
            state["matte"] = fn

        @property
        def calls(self):
            return state["calls"]
    return _Model()


def _px(path: Path, xy):
    with Image.open(path) as img:
        return img.convert("RGB").getpixel(xy)


# --- as shot ---------------------------------------------------------------

def test_as_shot_is_the_photo_sized_for_ebay_and_nothing_else(tmp_path, model):
    out = images.optimize(_photo(tmp_path), tmp_path / "out")
    assert out["output_size"] == (1600, 1200)
    assert out["original_size"] == (2400, 1800)
    assert out["background_removed"] is False and "bg_error" not in out
    assert model.calls == 0, "a photo nobody asked to cut was sent to Photoroom"
    # Backdrop in the corners, item in the middle: the seller's frame.
    assert min(_px(tmp_path / "out.jpg", (4, 4))) > 200
    assert max(_px(tmp_path / "out.jpg", (800, 600))) < 100


def test_a_big_phone_photo_comes_through_at_the_same_frame(tmp_path, model):
    """The decoder is asked for a reduced-scale read of a large JPEG, so a
    12MP photo never exists at full size in memory. The frame must not move
    for it: same item, same place, same share of the picture."""
    out = images.optimize(_photo(tmp_path, size=(4800, 3600)), tmp_path / "out")
    assert out["output_size"] == (1600, 1200)
    assert out["original_size"] == (4800, 3600)
    # The item spans the middle third, so 1/3 in is backdrop's last pixels
    # and 1/3 + a little is item.
    assert min(_px(tmp_path / "out.jpg", (520, 600))) > 200
    assert max(_px(tmp_path / "out.jpg", (545, 600))) < 100


# --- cut out ---------------------------------------------------------------

def test_the_cutout_is_the_item_on_white(tmp_path, model):
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    assert out["background_removed"] is True
    assert out["bg_engine"] == "photoroom"
    assert "bg_error" not in out
    assert model.calls == 1
    assert _px(tmp_path / "out.jpg", (4, 4)) == (255, 255, 255)
    assert max(_px(tmp_path / "out.jpg", (800, 600))) < 100


def test_the_cutout_sits_on_a_shadow_not_on_flat_white(tmp_path, model):
    """The shadow is under the item and down-right of it: a band that is
    darker than the white backdrop but nowhere near as dark as the item."""
    images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    out = tmp_path / "out.jpg"
    # The item spans the middle third of a 1600x1200 frame: x 533-1066,
    # y 400-800. Just past its bottom-right corner is where the shadow falls.
    shade = _px(out, (1080, 815))
    assert shade != (255, 255, 255), "no shadow: the item is on flat white"
    assert min(shade) < 250, f"shadow too faint to see: {shade}"
    assert min(shade) > 120, f"shadow reads as a second item: {shade}"
    # Up and LEFT of the item stays clean — the light comes from that side.
    assert min(_px(out, (500, 380))) > 250


def test_the_shadow_never_touches_the_item_or_the_far_corners(tmp_path, model):
    images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    out = tmp_path / "out.jpg"
    assert max(_px(out, (800, 600))) < 100      # the item, unchanged
    assert min(_px(out, (4, 4))) > 250          # corners still white
    assert min(_px(out, (1595, 1195))) > 250


def test_the_shadow_is_drawn_small_and_scaled_up(model):
    """It is low-frequency by construction, so it is blurred on a copy no
    larger than _SHADOW_SIDE and scaled back — which is what keeps it cheap
    on a 12MP photo."""
    big = Image.new("L", (4032, 3024), 0)
    big.paste(Image.new("L", (1600, 1200), 255), (1200, 900))
    assert images._contact_shadow(big).size == (4032, 3024)
    # Same answer either way: the shape is the silhouette, softened.
    assert images._contact_shadow(Image.new("L", (900, 900), 0)).getbbox() is None


def test_a_matte_that_found_nothing_keeps_the_photo_as_shot(tmp_path, model):
    """A white square is not a listing photo. The engine's opinion that there
    is no item is recorded, the seller's photo ships, and the charge for a
    cutout that did not happen is the caller's to give back (bg_error)."""
    model.matte = lambda rgb: Image.new("L", rgb.size, 0)
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    assert out["background_removed"] is False
    assert "found no item" in out["bg_error"]
    assert min(_px(tmp_path / "out.jpg", (4, 4))) > 200, "the backdrop was lost"


def test_an_engine_that_fails_keeps_the_photo_as_shot_and_says_why(
        tmp_path, model):
    """Whatever Photoroom does -- out of images, rate limited, down -- the
    photo comes through, carrying the sentence the engine wrote for the
    seller. It is the one thing the pass must never lose."""
    def _refused(rgb):
        raise cutout_api.PhotoroomError(cutout_api.UNAVAILABLE)
    model.matte = _refused
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    assert out["background_removed"] is False
    assert out["bg_error"] == cutout_api.UNAVAILABLE
    assert (tmp_path / "out.jpg").is_file()


def test_anything_else_going_wrong_still_keeps_the_photo(tmp_path, model):
    def _boom(rgb):
        raise RuntimeError("something nobody planned for")
    model.matte = _boom
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    assert out["background_removed"] is False
    assert "kept as shot" in out["bg_error"]
    assert (tmp_path / "out.jpg").is_file()


def test_no_key_keeps_the_photo_as_shot_and_says_so(tmp_path, monkeypatch):
    """The route refuses a cutout with no key configured, so this is the
    belt to that pair of braces: the pass itself never ships a photo it
    silently did nothing to."""
    monkeypatch.setattr(cutout_api.config, "PHOTOROOM_API_KEY", "")
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    assert out["background_removed"] is False
    assert out["bg_error"] == cutout_api.UNAVAILABLE


# --- the studio ------------------------------------------------------------

def test_the_studio_gets_the_cutout_or_a_reason(model):
    photo = Image.new("RGB", (600, 400), BACKDROP)
    ImageDraw.Draw(photo).rectangle((200, 100, 400, 300), fill=ITEM)
    out, engine = images.remove_background_white(photo)
    assert engine == "photoroom"
    assert out.getpixel((5, 5)) == (255, 255, 255)
    assert out.getpixel((300, 200)) == ITEM

    # ...and the reason, when there is nothing to cut -- a ValueError, which
    # the studio route answers as a 422 carrying the sentence.
    model.matte = lambda rgb: Image.new("L", rgb.size, 0)
    with pytest.raises(ValueError, match="found no item"):
        images.remove_background_white(Image.new("RGB", (600, 400), BACKDROP))

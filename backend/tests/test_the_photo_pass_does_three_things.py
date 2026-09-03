"""The photo pass does three things, and the seller does the rest.

It keeps a photo as shot, it takes the background off when asked -- one run
of the model, the item on white -- and it turns the photo the way the camera
meant. The pipeline this replaced also guessed whether the ITEM lay
sideways (two vision calls), rebuilt the matte's border, repaired holes in
it, drew a shadow, cropped square around a detected subject, sharpened, and
could hand the photo to three paid APIs. Every photo cost a minute and the
result was a surprise; the seller asked for the photos as shot or cut out,
and to do any further editing themselves.

The model is stood in for by a matte the test draws itself, so this runs
without rembg or a download: what is under test is what the pass does with a
matte, not the matte.
"""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("PIL")

from pathlib import Path  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images  # noqa: E402

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
    """Stand in for the model. Set `model.matte` to choose its answer."""
    state = {"matte": _matte_from_darkness, "calls": 0}

    def _mask(rgb, wait=None):
        state["calls"] += 1
        return state["matte"](rgb)

    monkeypatch.setattr(images, "_mask", _mask)

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
    assert model.calls == 0, "the model ran on a photo nobody asked it to touch"
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
    assert out["bg_engine"] == "local"
    assert "bg_error" not in out
    assert model.calls == 1
    assert _px(tmp_path / "out.jpg", (4, 4)) == (255, 255, 255)
    assert max(_px(tmp_path / "out.jpg", (800, 600))) < 100


def test_a_matte_that_found_nothing_keeps_the_photo_as_shot(tmp_path, model):
    """A white square is not a listing photo. The model's opinion that there
    is no item is recorded, the seller's photo ships, and the charge for a
    cutout that did not happen is the caller's to give back (bg_error)."""
    model.matte = lambda rgb: Image.new("L", rgb.size, 0)
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    assert out["background_removed"] is False
    assert "found no item" in out["bg_error"]
    assert min(_px(tmp_path / "out.jpg", (4, 4))) > 200, "the backdrop was lost"


def test_a_model_that_fails_keeps_the_photo_as_shot(tmp_path, model):
    """Whatever the model does -- an OOM, a corrupt session, a busy slot --
    the photo comes through. It is the one thing the pass must never lose."""
    def _boom(rgb):
        raise RuntimeError("onnxruntime fell over")
    model.matte = _boom
    out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    assert out["background_removed"] is False
    assert "onnxruntime fell over" in out["bg_error"]
    assert (tmp_path / "out.jpg").is_file()


def test_the_matte_is_hardened_not_shipped_soft(model):
    """The model's soft alpha ghosts the old backdrop through as grey fuzz.
    Below the low threshold is gone, above the high one is solid, and only
    the band between is an edge."""
    rgb = Image.new("RGB", (4, 1), ITEM)
    model.matte = lambda rgb: Image.frombytes("L", (4, 1), bytes([10, 100, 250, 255]))
    out = images.cutout(rgb)
    assert out.getpixel((0, 0)) == (255, 255, 255), "faint alpha kept the backdrop"
    assert out.getpixel((3, 0)) == ITEM
    assert out.getpixel((2, 0)) == ITEM, "near-solid alpha was not made solid"


def test_a_busy_slot_inside_a_batch_keeps_the_photo_rather_than_dropping_it(
        tmp_path, monkeypatch):
    """Inside a batch nobody is watching, so 'busy' must not cost the photo:
    it is saved as shot with the reason, exactly like any other failure."""
    monkeypatch.setattr(images, "BATCH_INFER_WAIT_SECONDS", 0.05)
    images._INFER_LOCK.acquire()
    try:
        out = images.optimize(_photo(tmp_path), tmp_path / "out", remove_bg=True)
    finally:
        images._INFER_LOCK.release()
    assert out["background_removed"] is False
    assert "another batch" in out["bg_error"]
    assert (tmp_path / "out.jpg").is_file()


# --- the studio ------------------------------------------------------------

def test_the_studio_gets_the_cutout_or_a_reason(model):
    photo = Image.new("RGB", (600, 400), BACKDROP)
    ImageDraw.Draw(photo).rectangle((200, 100, 400, 300), fill=ITEM)
    out, engine = images.remove_background_white(photo)
    assert engine == "local"
    assert out.getpixel((5, 5)) == (255, 255, 255)
    assert out.getpixel((300, 200)) == ITEM

    model.matte = lambda rgb: Image.new("L", rgb.size, 0)
    with pytest.raises(ValueError, match="separate"):
        images.remove_background_white(photo)


def test_the_studio_is_told_busy_rather_than_made_to_wait(monkeypatch):
    """A person is watching this spinner: the short deadline, then a 503 the
    editor can retry -- not the batch queue."""
    monkeypatch.setattr(images, "INFER_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(images, "BATCH_INFER_WAIT_SECONDS", 30)
    images._INFER_LOCK.acquire()
    try:
        with pytest.raises(images.CutoutBusy):
            images.remove_background_white(Image.new("RGB", (64, 64), BACKDROP))
    finally:
        images._INFER_LOCK.release()


def test_smart_crop_tightens_on_the_item_and_declines_when_already_tight(model):
    photo = Image.new("RGB", (1200, 900), BACKDROP)
    ImageDraw.Draw(photo).rectangle((400, 300, 800, 600), fill=ITEM)
    crop = images.smart_crop(photo)
    assert crop is not None
    assert crop.size[0] < 1200 and crop.size[1] < 900
    assert crop.getpixel((crop.width // 2, crop.height // 2)) == ITEM

    tight = Image.new("RGB", (400, 300), ITEM)
    assert images.smart_crop(tight) is None


def test_auto_clean_whitens_around_the_item_and_leaves_it_alone(model):
    photo = Image.new("RGB", (600, 400), BACKDROP)
    ImageDraw.Draw(photo).rectangle((200, 100, 400, 300), fill=ITEM)
    out = images.auto_clean(photo)
    assert out.getpixel((5, 5)) == (255, 255, 255)
    assert out.getpixel((300, 200)) == ITEM


# --- one inference at a time ---------------------------------------------

def test_one_inference_runs_at_a_time():
    """Two runs at once double peak memory and kill a small machine. The
    slot is a lock, and engine_state() says when it is taken."""
    assert isinstance(images._INFER_LOCK, type(threading.Lock()))
    assert images.engine_state()["busy"] is False
    images._INFER_LOCK.acquire()
    try:
        assert images.engine_state()["busy"] is True
    finally:
        images._INFER_LOCK.release()

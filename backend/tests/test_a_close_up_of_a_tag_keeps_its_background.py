"""A close-up of a tag has no background to take off.

The report, with a screenshot: a vintage Hawaiian shirt, four photos, cutouts
on. The two whole-garment shots came out right. The two close-ups of its tags
came back as fragments of a label floating on white — the brand tag torn in
half, the care label with a bite out of it and a smear of the shirt left
beside it. Nothing failed, nothing was logged, and the grid said Complete. The
draft that followed named a brand that does not exist, because the identify
pass reads the OPTIMIZED photos and the tag it had to read had been deleted.

That is the salient-object model working exactly as built. Handed a photo
whose every pixel is the item — a label filling the frame, the garment behind
it — it still answers the only question it knows, "which part of this is the
subject", and dutifully deletes the rest. The rest was the shirt.

WHY THIS IS NOT FIXED WHERE THE OTHER TWO WERE. #255 and #256 both added a
guard: measure the matte, refuse the bad ones. Neither can reach this, and the
first test below is the proof — the matte of a torn-out label is one connected
region, opaque throughout, filling its own bounding box, and it passes every
guard in the file with margin. It is arithmetically indistinguishable from a
perfect cutout of a framed picture, because as a matte that is exactly what it
is. The information that separates them is not in the alpha at all. It is in
the photo: this one has no background in it.

So the photo is spared before the model is asked rather than judged after. The
only thing that looks at the photo before the cutout runs is the orientation
screen, which has been asking "is this a detail close-up?" all along — it
needs the answer to refuse a turn — and throwing it away. It is read now.

Unconfirmed, unlike a turn, and the last test pins why: the two errors are not
equal. A whole-item shot wrongly called a close-up keeps its background, which
costs one photo an opt-in feature. A close-up missed is exactly the old
behaviour. Neither is worth a second call.

Pillow only, with the model stood in for: what is under test is what the pass
does with an answer, not the answer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images, orient  # noqa: E402

SIZE = (1200, 900)


# --- the matte the reported photo produced ----------------------------------

def _torn_label() -> Image.Image:
    """What the model kept of the brand tag close-up: the printed label, and
    the tan band above it, with the whole shirt behind them deleted."""
    m = Image.new("L", SIZE, 0)
    d = ImageDraw.Draw(m)
    d.polygon([(330, 300), (880, 270), (900, 560), (350, 590)], fill=255)
    d.polygon([(420, 190), (820, 175), (825, 255), (425, 270)], fill=255)
    return m


def test_no_guard_in_the_file_can_see_this(monkeypatch):
    """The reason this is fixed a layer up. Every measure the cutout has says
    this matte is excellent, because as a matte it is: one opaque blob that
    fills its own box. Shape and opacity cannot answer a question about what
    was in the photo to begin with.

    If a future guard ever does catch it, this test fails and should be
    deleted — but until one does, nothing downstream of the model is load
    bearing here."""
    alpha = images._harden(images._fill_interior(_torn_label()))
    kept = alpha.point(lambda a: 255 if a >= 128 else 0)
    coverage = sum(kept.histogram()[128:]) / (SIZE[0] * SIZE[1])
    total, regions, box_fill = images._kept_shape(kept)

    assert coverage >= images._MIN_FG_COVERAGE          # 0.18 against 0.02
    assert images._interior_solidity(alpha) >= images._MIN_INTERIOR_SOLIDITY
    assert regions[0][0] / total >= images._MIN_LARGEST_REGION
    assert box_fill >= images._MIN_BBOX_FILL
    assert images._kept_is_the_product(total, regions, box_fill)


# --- what the screen reports ------------------------------------------------

def _photo(tmp: Path, name: str) -> Path:
    img = Image.new("RGB", (900, 700), (205, 190, 160))
    ImageDraw.Draw(img).rectangle((260, 220, 640, 480), fill=(238, 230, 212))
    src = tmp / name
    img.save(src, "JPEG", quality=92)
    return src


def _answer(*sits: str) -> dict:
    return {"photos": [{"photo": i + 1, "item": "thing", "sits": s,
                        "text": "none", "rotate": 0, "sure": False}
                       for i, s in enumerate(sits)]}


def test_the_screen_names_the_close_ups(tmp_path):
    """"detail" is the screen's own word for a close-up of part of an item —
    a label, a tag, a stitch, a mark, a texture. Every other answer describes
    a whole item in the frame and is not one."""
    shirt = _photo(tmp_path, "src_000.jpg")
    tag = _photo(tmp_path, "src_001.jpg")
    found = orient._details(_answer("garment_flat", "detail"), [shirt, tag])
    assert found == {"src_001.jpg"}

    for sits in ("standing", "hanging", "worn", "garment_flat", "flat", ""):
        assert orient._details(_answer(sits), [shirt]) == frozenset()


def test_an_answer_that_makes_no_sense_names_nobody(tmp_path):
    """The model writes this JSON, so nothing in it is trusted to be shaped
    the way it was asked for. A photo number out of range, a missing one, a
    malformed entry — none of them may make some OTHER photo a close-up."""
    shirt = _photo(tmp_path, "src_000.jpg")
    for junk in ({"photos": [{"photo": 9, "sits": "detail"}]},
                 {"photos": [{"photo": "x", "sits": "detail"}]},
                 {"photos": [{"sits": "detail"}]},
                 {"photos": ["detail"]},
                 {"photos": None},
                 {},
                 []):
        assert orient._details(junk, [shirt]) == frozenset(), junk


# --- and what the photo pass does with it -----------------------------------

class _Cutout:
    """Stands in for the model, and records whether it was asked at all."""

    def __init__(self):
        self.asked: list[tuple[int, int]] = []

    def __call__(self, img, wait=None):
        self.asked.append(img.size)
        return Image.new("RGB", img.size, (255, 255, 255))


@pytest.fixture()
def never_screened(monkeypatch):
    """No screen by default, so each test below says its own answer."""
    monkeypatch.setattr(
        images, "_screen_for",
        lambda sources, should_stop=None: ({}, frozenset(), frozenset()))


def test_a_close_up_is_never_handed_to_the_model(tmp_path, monkeypatch,
                                                 never_screened):
    """The point of the fix. Not "the cutout is refused for it" — the model is
    not asked, because there is no question to ask: every pixel is the item."""
    model = _Cutout()
    monkeypatch.setattr(images, "cutout", model)
    src = _photo(tmp_path, "src_000.jpg")

    out = images.optimize(src, tmp_path / "img_000.jpg", remove_bg=True,
                          detail=True)

    assert model.asked == []
    assert out["background_removed"] is False
    assert out["bg_error"] == images.DETAIL_KEPT_AS_SHOT
    # And the photo itself is on disk, as shot.
    with Image.open(tmp_path / "img_000.jpg") as saved:
        assert saved.getpixel((10, 10)) != (255, 255, 255)


def test_a_photo_of_the_whole_item_is_still_cut_out(tmp_path, monkeypatch,
                                                    never_screened):
    """The feature is not weakened for the photos it was built for."""
    model = _Cutout()
    monkeypatch.setattr(images, "cutout", model)
    src = _photo(tmp_path, "src_000.jpg")

    out = images.optimize(src, tmp_path / "img_000.jpg", remove_bg=True)

    assert len(model.asked) == 1
    assert out["background_removed"] is True
    assert not out.get("bg_error")


def test_a_close_up_costs_nothing_when_the_seller_never_asked(
        tmp_path, monkeypatch, never_screened):
    """With cutouts off, a close-up is just a photo: no cutout to skip, so
    nothing to say about it and nothing to refund."""
    monkeypatch.setattr(images, "cutout", _Cutout())
    out = images.optimize(_photo(tmp_path, "src_000.jpg"),
                          tmp_path / "img_000.jpg", remove_bg=False,
                          detail=True)
    assert not out.get("bg_error")
    assert out["background_removed"] is False


def test_the_batch_spares_only_the_close_ups(tmp_path, monkeypatch):
    """End to end through the pass the uploads actually take: four photos of
    one shirt, two of them tags, one screen answer, and only the two whole
    garment shots reach the model."""
    model = _Cutout()
    monkeypatch.setattr(images, "cutout", model)
    names = ["src_000.jpg", "src_001.jpg", "src_002.jpg", "src_003.jpg"]
    srcs = [_photo(tmp_path, n) for n in names]
    monkeypatch.setattr(
        images, "_screen_for",
        lambda sources, should_stop=None: (
            {}, frozenset({"src_001.jpg", "src_002.jpg"}), frozenset()))

    results = images.optimize_batch(
        [(src, tmp_path / f"img_{i:03d}.jpg") for i, src in enumerate(srcs)],
        remove_bg=True)

    assert [bool(r["background_removed"]) for r in results] == \
        [True, False, False, True]
    assert len(model.asked) == 2
    # The two that were spared say so, so the charge for them comes back —
    # main.py refunds on bg_error.
    assert [r.get("bg_error") for r in results] == \
        [None, images.DETAIL_KEPT_AS_SHOT, images.DETAIL_KEPT_AS_SHOT, None]


def test_a_screen_that_fails_cuts_out_everything_as_before(tmp_path,
                                                           monkeypatch):
    """The screen is an enhancement and may not become a dependency: with the
    API off, over budget, cancelled or simply broken, every photo goes to the
    model exactly as it did before any of this existed."""
    model = _Cutout()
    monkeypatch.setattr(images, "cutout", model)

    def _explode(paths, should_stop=None):
        raise RuntimeError("no API key")

    monkeypatch.setattr(orient, "screen", _explode)
    srcs = [_photo(tmp_path, f"src_{i:03d}.jpg") for i in range(2)]

    results = images.optimize_batch(
        [(src, tmp_path / f"img_{i:03d}.jpg") for i, src in enumerate(srcs)],
        remove_bg=True)

    assert all(r["background_removed"] for r in results)
    assert len(model.asked) == 2

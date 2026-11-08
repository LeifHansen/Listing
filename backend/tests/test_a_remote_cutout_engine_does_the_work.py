"""Photoroom does the cutting, and there is nothing behind it.

The complaint this answers: background removal "either fails miserably, or
doesn't try". Both halves were real and they had different causes. The matte
came from the on-server model and then had to clear three gates fitted to that
model's failures -- which is what refused necklaces, belts, bangles and
guitars on mattes that were fine -- and one inference took about 104 seconds.
The on-server model is gone. Photoroom supplies the matte, and the gates it
has to answer shrink to the one that is engine-independent: did anything
survive at all.

What is pinned here:

  * a cutout is Photoroom's matte composited on white under our shadow, and
    the engine that ran is named;
  * a matte is not refused for its shape -- only for keeping nothing;
  * a failure is RAISED with its reason, never swallowed: with no local model
    to fall back to, the reason is the only thing the seller gets, and the
    photo pass turns it into the photo's `bg_error`;
  * and a framed print, which gets no cutout at all when its border cannot be
    scanned, gets one -- at whatever angle it was photographed at, and always
    as a SOLID shape, so the thing services/artwork exists to prevent stays
    impossible. Photoroom may say WHERE the picture is; it never says what to
    keep inside it.

No httpx here on purpose. Photoroom is stubbed at cutout_api.photoroom_cutout,
because the `cutout` CI job runs on Pillow and fails on a SKIP. What talks to
the wire is tested in test_a_cutout_api_failure_is_explained.py.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from backend.services import artwork, cutout_api, images  # noqa: E402

BACKDROP = (238, 236, 232)
ITEM = (40, 50, 70)


def _photo(size=(1200, 900)) -> Image.Image:
    """A dark item in the middle third of a light backdrop."""
    img = Image.new("RGB", size, BACKDROP)
    w, h = size
    ImageDraw.Draw(img).rounded_rectangle(
        (w // 3, h // 3, 2 * w // 3, 2 * h // 3), radius=30, fill=ITEM)
    return img


def _cut(size, box) -> Image.Image:
    """What Photoroom returns: RGBA, opaque only inside `box`."""
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]),
                        ITEM + (255,)), (box[0], box[1]))
    return out


def _engine(returns=None, raises=None, log=None):
    """A stand-in Photoroom. Records every photo it was asked about."""
    def _run(rgb):
        if log is not None:
            log.append(rgb.size)
        if raises is not None:
            raise raises
        return returns(rgb) if callable(returns) else returns
    return _run


@pytest.fixture()
def photoroom(monkeypatch):
    """Install a stand-in Photoroom: photoroom(returns=..., raises=..., log=...)."""
    def _set(returns=None, raises=None, log=None):
        monkeypatch.setattr(cutout_api, "photoroom_cutout",
                            _engine(returns=returns, raises=raises, log=log))
    return _set


# --- a cutout ---------------------------------------------------------------

def test_a_cutout_is_composited_on_white(photoroom):
    photoroom(returns=lambda rgb: _cut(rgb.size, (400, 300, 800, 600)))

    out, engine = images.cutout_with_engine(_photo())

    assert engine == "photoroom"
    assert out.mode == "RGB"
    # A corner is backdrop in the source and white in the cutout.
    assert out.getpixel((5, 5)) == images.WHITE
    # The item survived.
    assert out.getpixel((600, 450)) == ITEM


def test_a_matte_that_comes_back_a_different_size_is_scaled_to_the_photo(
        photoroom):
    """A service answers at the size it was sent, and the upload is capped. An
    alpha of one size against a photo of another understates coverage -- which
    would refuse a perfectly good cutout -- and composites at the wrong size."""
    photoroom(returns=lambda rgb: _cut((600, 450), (200, 150, 400, 300)))

    img = _photo((1200, 900))
    out, engine = images.cutout_with_engine(img)

    assert out.size == img.size
    assert engine == "photoroom"


def test_a_matte_is_not_refused_for_its_shape(photoroom):
    """A thin chain -- a necklace -- is what the local model's shape gate
    refused on a flawless matte. A matte answers coverage and nothing else."""
    def _chain_matte(rgb):
        out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
        w, h = rgb.size
        # A 6px-wide loop: one object, filling almost none of its own box.
        ImageDraw.Draw(out).ellipse((w // 3, h // 4, 2 * w // 3, 3 * h // 4),
                                    outline=ITEM + (255,), width=6)
        return out

    photoroom(returns=_chain_matte)

    out, engine = images.cutout_with_engine(_photo())

    assert out is not None and engine == "photoroom"


def test_a_matte_that_keeps_nothing_is_refused_with_a_reason(photoroom):
    """The one gate that survives. An empty matte would erase the item, so it
    is refused -- and said, since there is nothing to fall back to."""
    photoroom(returns=lambda rgb: Image.new("RGBA", rgb.size, (0, 0, 0, 0)))

    with pytest.raises(cutout_api.CutoutApiError, match="found no item"):
        images.cutout_with_engine(_photo())


# --- failures say why, they do not lose the photo ---------------------------

def test_a_failure_reaches_the_seller_as_itself(photoroom):
    """Nothing to fall back to, so the real cause is raised rather than
    swallowed -- "busy, try again in a minute" is something a seller can act
    on; a photo quietly kept as shot is not."""
    photoroom(raises=cutout_api.PhotoroomError("Background removal is busy"))

    with pytest.raises(cutout_api.CutoutApiError, match="busy"):
        images.cutout_with_engine(_photo())


def test_no_key_is_said_rather_than_silently_skipped(photoroom):
    photoroom(returns=None)

    with pytest.raises(cutout_api.CutoutApiError) as err:
        images.cutout_with_engine(_photo())
    assert str(err.value) == cutout_api.UNAVAILABLE


def test_the_photo_pass_keeps_the_photo_and_carries_the_reason(
        photoroom, tmp_path):
    photoroom(raises=cutout_api.PhotoroomError("Background removal is busy"))
    src = tmp_path / "src.jpg"
    _photo().save(src, "JPEG", quality=92)

    out = images.optimize(src, tmp_path / "out", remove_bg=True)

    assert out["background_removed"] is False
    assert out["bg_error"] == "Background removal is busy"
    assert (tmp_path / "out.jpg").is_file()


def test_the_studio_reports_which_engine_answered(photoroom):
    photoroom(returns=lambda rgb: _cut(rgb.size, (400, 300, 800, 600)))
    _out, engine = images.remove_background_white(_photo())
    assert engine == "photoroom"


def test_the_readiness_probe_names_the_engine():
    assert images.engine_state()["engine"] == "photoroom"


# --- the framed print, which is the loudest half of the complaint ----------

def _framed_print(size=(1200, 900), box=(280, 160, 900, 760)):
    """A print in a frame on a surface almost its own colour -- the case
    artwork.outline() is entitled to give up on."""
    img = Image.new("RGB", size, (206, 202, 196))
    draw = ImageDraw.Draw(img)
    draw.rectangle(box, fill=(210, 206, 200), outline=(198, 194, 188), width=3)
    inner = (box[0] + 40, box[1] + 40, box[2] - 40, box[3] - 40)
    draw.rectangle(inner, fill=(250, 248, 244))
    draw.ellipse((inner[0] + 60, inner[1] + 60, inner[2] - 60, inner[3] - 60),
                 fill=(120, 150, 170))
    return img, box


def _tilted_cut(size, deg, inner=(620, 700)):
    """What a remote engine returns for a print lying at `deg` on a floor."""
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    lay = Image.new("RGBA", inner, ITEM + (255,))
    lay = lay.rotate(deg, expand=True, resample=Image.BICUBIC)
    out.paste(lay, ((size[0] - lay.width) // 2, (size[1] - lay.height) // 2))
    return out


def _points_inside(kept, step=40, inset=12):
    """Sample points comfortably inside an opaque region."""
    eroded = kept.filter(ImageFilter.MinFilter(9))
    box = eroded.getbbox()
    pts = []
    for y in range(box[1] + inset, box[3] - inset, step):
        for x in range(box[0] + inset, box[2] - inset, step):
            if eroded.getpixel((x, y)) >= 128:
                pts.append((x, y))
    assert pts, "the fixture produced no interior to check"
    return pts


def test_a_framed_print_gets_a_cutout_when_the_border_cannot_be_scanned(
        photoroom, monkeypatch):
    """The reported bug: "doesn't try for simple objects (rectangular framed
    print)". It didn't try because the geometric scan returned None, which
    correctly means keep-as-shot. A remote engine can find the frame."""
    img, box = _framed_print()
    photoroom(returns=lambda rgb: _cut(rgb.size, box))
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)

    out = images.art_cutout(img)

    assert out is not None, "the print should finally get a cutout"
    # Outside the frame is white; inside is untouched artwork.
    assert out.getpixel((5, 5)) == images.WHITE
    assert out.getpixel(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)) == \
        img.getpixel(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))


def test_a_print_photographed_at_an_angle_is_still_a_print(photoroom,
                                                           monkeypatch):
    """The photos this was actually reported on: prints laid on a wooden floor
    and shot hand-held, 39 of them in one batch, every one kept as shot.

    None of them is square-on, and a rotated rectangle fills its AXIS-ALIGNED
    box badly -- 0.85 at 5 degrees, 0.74 at 10. Judged that way the fix refuses
    the very photos it exists for, so the rectangle is fitted at the best
    angle instead."""
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)

    for deg in (0, 3, 5, 8, 12, 20):
        img, _ = _framed_print()
        photoroom(
            returns=lambda rgb, d=deg: _tilted_cut(rgb.size, d))

        assert images.art_cutout(img) is not None, (
            f"a print at {deg} degrees is still a print")


def test_an_angled_print_keeps_every_pixel_of_itself(photoroom,
                                                     monkeypatch):
    """The invariant, on the angled path: the quad must COVER the print. An
    earlier revision turned the corners the wrong way and clipped one corner
    off while taking floor at the opposite one -- accepted/rejected looked
    perfect throughout, and only measuring the overlap showed it."""
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)

    for deg in (0, 5, 12, 20):
        img, _ = _framed_print()
        matte = _tilted_cut(img.size, deg)
        photoroom(returns=lambda rgb, m=matte: m)

        out = images.art_cutout(img)
        assert out is not None

        kept = matte.split()[3].point(lambda a: 255 if a >= 128 else 0)
        for xy in _points_inside(kept):
            assert out.getpixel(xy) == img.getpixel(xy), (
                f"at {deg} degrees the pixel {xy} inside the print was altered")


def test_a_matte_with_the_mount_dropped_out_is_still_a_print(photoroom,
                                                             monkeypatch):
    """The same hole the geometric scan meets, arriving from a paid engine
    instead: it lets go of a white mount, a pale sky or the glare off the
    glazing and hands back a rectangle with a gap in the middle of it.

    A gap in the middle says nothing about the outer edge, and the outer edge
    is the only thing asked for here — this path takes the matte's SHAPE and
    then fills it solid itself. Measured with the gap still in it, a matted
    print scores below an ellipse and is refused."""
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)
    img, box = _framed_print()

    def _holed(rgb):
        cut = _cut(rgb.size, box)
        ImageDraw.Draw(cut).rectangle(
            (box[0] + 80, box[1] + 80, box[2] - 80, box[3] - 80),
            fill=(0, 0, 0, 0))
        return cut

    photoroom(returns=_holed)

    out = images.art_cutout(img)

    assert out is not None, "a print is a print with or without its mount"
    # ...and what ships is solid: the mount the engine dropped is still there.
    mid = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    assert out.getpixel(mid) == img.getpixel(mid)
    assert out.getpixel((5, 5)) == images.WHITE


def test_the_scanned_border_still_wins(photoroom, monkeypatch):
    """Geometry first: it cannot be wrong about what is inside the box it
    returns, so a remote engine is only ever the second opinion."""
    img, box = _framed_print()
    asked = []
    photoroom(
        returns=lambda rgb: _cut(rgb.size, box), log=asked)
    monkeypatch.setattr(artwork, "outline", lambda rgb: (
        ((10, 10), (100, 10), (100, 100), (10, 100)),))

    images.art_cutout(img)

    assert asked == [], "no remote call when the scan already answered"


def test_a_subject_lifted_out_of_a_painting_is_refused(photoroom,
                                                       monkeypatch):
    """The baby out of the basket. A remote engine handed a photo OF a picture
    answers the only question it knows -- which part of this is the subject --
    and for a painting the honest answer is "all of it". The shape it offers is
    rejected because it is not a rectangle at ANY angle: a round subject scores
    0.785 however it is turned, well under the 0.9 floor."""
    img, _ = _framed_print()

    def _the_baby(rgb):
        out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
        ImageDraw.Draw(out).ellipse((500, 350, 760, 610), fill=ITEM + (255,))
        return out

    photoroom(returns=_the_baby)
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)

    assert images.art_cutout(img) is None, \
        "a round subject is not a picture's border — keep the photo as shot"


def test_nothing_inside_the_border_is_ever_removed(photoroom, monkeypatch):
    """The invariant services/artwork exists for, held on the new path too:
    whatever the remote matte said, what ships is a FILLED RECTANGLE."""
    img, box = _framed_print()

    def _holes_in_the_art(rgb):
        """A matte shaped like the frame but with a piece of the artwork
        punched out of it — exactly the shape that must NOT reach the
        composite. Kept small enough that the box is ACCEPTED (it still fills
        ~0.95 of itself), so this exercises the rectangle rather than the
        shape gate: the hole has to be filled back in by artwork.quad, not
        rejected on its way past."""
        out = _cut(rgb.size, box)
        ImageDraw.Draw(out).ellipse((560, 400, 710, 550), fill=(0, 0, 0, 0))
        return out

    photoroom(returns=_holes_in_the_art)
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)

    out = images.art_cutout(img)

    assert out is not None, "this matte fills its box — the box should stand"
    # Every one of these sits inside the hole the matte tried to punch.
    for xy in ((600, 450), (630, 470), (635, 475), (660, 500)):
        assert out.getpixel(xy) == img.getpixel(xy), \
            f"pixel {xy} inside the border was altered"


def test_a_picture_still_never_gets_the_remote_matte_itself(photoroom,
                                                            monkeypatch):
    """Art takes a BOX from a remote engine and never its matte. Proven by the
    edges: a rectangle's corners are opaque, and the ellipse the engine
    actually returned would have left them white."""
    img, box = _framed_print()

    def _rounded(rgb):
        out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
        ImageDraw.Draw(out).rounded_rectangle(box, radius=120,
                                             fill=ITEM + (255,))
        return out

    photoroom(returns=_rounded)
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)

    out = images.art_cutout(img)
    assert out is not None
    corner = (box[0] + 6, box[1] + 6)
    assert out.getpixel(corner) == img.getpixel(corner), \
        "the corner was rounded off the print — that is the matte, not a box"


def test_photoroom_is_asked_once_about_a_border_the_scan_missed(photoroom,
                                                                 monkeypatch):
    """One call, and only for the picture whose border the geometric scan
    could not find -- the photo that was otherwise getting nothing at all."""
    img, box = _framed_print()
    asked = []
    photoroom(
        returns=lambda rgb: _cut(rgb.size, box), log=asked)
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)

    assert images.art_cutout(img) is not None
    assert asked == [img.size], "Photoroom located the border, once"

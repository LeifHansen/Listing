"""The background remover "works great for shirts. Horrible for square objects."

Reported with a screenshot of one grid: a camp shirt cut out cleanly on
white, next to a bamboo serving tray still sitting on its tile floor and a
framed print still sitting on its wooden table. One feature, two answers,
split by the shape of the thing being sold.

The split was real and it had a cause. A shirt is not a picture, so it goes
to the model and comes back cut out. Anything flat whose face is a picture --
a framed print, a poster, a map, a tray with a map printed on it -- is routed
away from the model on purpose (services/artwork: a salient-object model
handed a photograph OF a picture deletes the picture and keeps whatever it
depicts), and cut instead to its own scanned outer border. When that scan
cannot find an edge, the photo is kept exactly as shot.

There is a second look at that point: a segmentation matte -- Photoroom's
-- is allowed to say WHERE the picture is, checked for being a rectangle,
and never what to keep inside it.

What is pinned here:

  * Photoroom may locate a picture's border when the scan could not;
  * it still never says what to keep INSIDE one -- a subject lifted out of a
    painting is refused, and what ships is always a solid shape;
  * a matte covering the whole frame is not a border: there is no background
    in that photo, so nothing is removed and nothing is charged for;
  * a flat rectangular item Photoroom found nothing in -- a tray, a sign, a
    plaque, a boxed set -- is cut to its own scanned border rather than
    handed back as shot, in the batch and in the studio both;
  * and only when Photoroom found NOTHING: an outage is said, not papered
    over with geometry.

Pillow only, with Photoroom stood in for by mattes the tests draw
themselves: the same constraint the rest of the photo suites work under.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import artwork, cutout_api, images  # noqa: E402

SIZE = (1200, 900)
FLOOR = (206, 202, 196)      # a surface almost the print's own colour
TRAY = (150, 112, 74)        # bamboo, against a pale tile floor
TILE = (238, 236, 232)


# --- fixtures ---------------------------------------------------------------

def _framed_print(size=SIZE, box=(280, 160, 900, 760)):
    """A print in a frame on a surface almost its own colour -- the photo
    artwork.outline() is entitled to give up on."""
    img = Image.new("RGB", size, FLOOR)
    draw = ImageDraw.Draw(img)
    draw.rectangle(box, fill=(210, 206, 200), outline=(198, 194, 188), width=3)
    inner = (box[0] + 40, box[1] + 40, box[2] - 40, box[3] - 40)
    draw.rectangle(inner, fill=(250, 248, 244))
    draw.ellipse((inner[0] + 60, inner[1] + 60, inner[2] - 60, inner[3] - 60),
                 fill=(120, 150, 170))
    return img, box


def _tray(size=SIZE, box=(240, 180, 940, 740)):
    """The serving tray: a flat rectangular object, clearly not its floor,
    with a picture printed on its face. The screen calls this a tray rather
    than a picture, and it is right to -- which is why it reaches the model."""
    img = Image.new("RGB", size, TILE)
    draw = ImageDraw.Draw(img)
    draw.rectangle(box, fill=TRAY)
    inner = (box[0] + 46, box[1] + 46, box[2] - 46, box[3] - 46)
    draw.rectangle(inner, fill=(240, 232, 214))
    for x in range(inner[0], inner[2], 40):
        draw.line([(x, inner[1]), (x, inner[3])], fill=(196, 84, 70), width=5)
    return img, box, inner


def _garment(size=SIZE):
    """A shirt laid out flat: an item that is emphatically not a rectangle,
    so no border can be scanned for it and nothing may be invented."""
    img = Image.new("RGB", size, TILE)
    ImageDraw.Draw(img).polygon(
        [(460, 230), (740, 230), (940, 380), (860, 470), (760, 400),
         (760, 760), (440, 760), (440, 400), (340, 470), (260, 380)],
        fill=(48, 62, 96))
    return img


def _tilted(size, deg, inner=(620, 700)):
    """A matte shaped like a print lying at `deg` on a floor."""
    out = Image.new("L", size, 0)
    lay = Image.new("L", inner, 255).rotate(deg, expand=True,
                                            resample=Image.BICUBIC)
    out.paste(lay, ((size[0] - lay.width) // 2, (size[1] - lay.height) // 2))
    return out


def _nothing(rgb):
    """What Photoroom answers when it finds no item it is sure of."""
    return Image.new("L", rgb.size, 0)


@pytest.fixture()
def no_scan(monkeypatch):
    """artwork.outline() gives up. Patched rather than drawn, because what is
    under test is what happens NEXT -- test_a_painting_is_never_cut_into.py
    is where the scan itself is proved."""
    monkeypatch.setattr(artwork, "outline", lambda rgb: None)


def _model(monkeypatch, matte):
    """Stand in for Photoroom with a matte drawn by `matte`, and record every
    photo it was asked about."""
    asked = []

    def _photoroom(rgb):
        asked.append(rgb.size)
        cut = rgb.convert("RGBA")
        cut.putalpha(matte(rgb))
        return cut

    monkeypatch.setattr(cutout_api, "photoroom_cutout", _photoroom)
    return asked


def _photo_file(tmp_path, img, name="src_000.jpg"):
    path = tmp_path / name
    img.save(path, "JPEG", quality=95)
    return path


# --- Photoroom may say WHERE a picture is -----------------------------

def test_a_picture_gets_a_border_from_photoroom_when_the_scan_found_none(no_scan, monkeypatch):
    """The fix, stated. The scan found no edge, and the photo used to come
    back exactly as it went in."""
    img, _ = _framed_print()
    asked = _model(monkeypatch, lambda rgb: _tilted(rgb.size, 6))

    out = images.art_cutout(img)

    assert out is not None, "the print should finally get a cutout"
    assert asked == [img.size], "Photoroom was asked where the print is"
    assert out.getpixel((5, 5)) == images.WHITE, "the floor is gone"
    assert out.getpixel((600, 450)) == img.getpixel((600, 450)), \
        "the middle of the print was altered"


def test_photoroom_still_never_says_what_to_keep_inside_a_picture(no_scan, monkeypatch):
    """The baby out of the basket. A round subject fills 0.785 of its best rectangle at
    every angle, against a floor of 0.9, so it is not a border -- and the
    photo is kept exactly as shot, which is what it was before."""
    img, _ = _framed_print()

    def _the_baby(rgb):
        out = Image.new("L", rgb.size, 0)
        ImageDraw.Draw(out).ellipse((500, 350, 760, 610), fill=255)
        return out

    _model(monkeypatch, _the_baby)

    assert images.art_cutout(img) is None, \
        "a subject lifted out of a painting is not the painting's border"


def test_nothing_inside_the_border_is_removed_when_the_model_located_it(no_scan, monkeypatch):
    """The invariant services/artwork exists for, on the path that is new:
    whatever the matte said, what ships is a FILLED shape. The model here
    punched a hole through the artwork; the hole must not reach the photo."""
    img, box = _framed_print()

    def _holed(rgb):
        out = Image.new("L", rgb.size, 0)
        draw = ImageDraw.Draw(out)
        draw.rectangle(box, fill=255)
        draw.ellipse((560, 400, 710, 550), fill=0)
        return out

    _model(monkeypatch, _holed)

    out = images.art_cutout(img)

    assert out is not None, "this matte fills its box — the border stands"
    for xy in ((600, 450), (630, 470), (635, 475), (660, 500)):
        assert out.getpixel(xy) == img.getpixel(xy), \
            f"pixel {xy} inside the border was removed"


def test_a_matte_covering_the_whole_photo_is_not_a_border(no_scan,
                                                          monkeypatch):
    """A model that keeps everything has found no background, so there is no
    border in this photo to cut to. Cutting to it anyway would composite the
    photo onto white, re-encode it, change nothing a seller can see -- and
    report that the background was removed."""
    img, _ = _framed_print()
    _model(monkeypatch, lambda rgb: Image.new("L", rgb.size, 255))

    assert images.art_cutout(img) is None


def test_a_scanned_border_is_still_the_answer_when_there_is_one(monkeypatch):
    """Geometry first, unchanged: it cannot be wrong about what is inside the
    box it returns, so the model is only ever the fallback."""
    img, _ = _framed_print()
    monkeypatch.setattr(artwork, "outline", lambda rgb: (
        ((100, 100), (900, 100), (900, 700), (100, 700)),))
    asked = _model(monkeypatch, lambda rgb: _tilted(rgb.size, 6))

    assert images.art_cutout(img) is not None
    assert asked == [], "the model was asked about a border already scanned"


def test_photoroom_failing_on_a_border_look_is_not_an_error(no_scan, monkeypatch):
    """Out of images, down, no key: the photo was about to be kept as shot
    anyway, so this is exactly what would have happened without the call."""
    img, _ = _framed_print()

    def _broken(rgb):
        raise cutout_api.PhotoroomError(cutout_api.UNAVAILABLE)

    monkeypatch.setattr(cutout_api, "photoroom_cutout", _broken)

    assert images.art_cutout(img) is None


# --- ...and a square object that is not a picture at all --------------------

def test_a_flat_rectangular_item_the_model_will_not_keep_is_cut_to_its_border(tmp_path, monkeypatch):
    """The tray. Its face is a printed map, but a tray with a map on it is a
    tray, so the screen does not call it art and it goes to the model like
    any other object -- and when Photoroom comes back with nothing, the
    answer used to be the photo, untouched, plus a sentence.

    The shape of the thing is a rectangle either way, so the same geometry
    that cuts a print answers for it."""
    img, box, _inner = _tray()
    _model(monkeypatch, _nothing)

    out = images.optimize(_photo_file(tmp_path, img),
                          tmp_path / "img_000.jpg", remove_bg=True)

    assert out["background_removed"] is True
    assert out["bg_engine"] == "border"
    assert not out.get("bg_error")
    with Image.open(tmp_path / "img_000.jpg") as saved:
        assert saved.getpixel((5, 5)) == images.WHITE, "the floor is gone"
        # The tray's own rim, which is what a matte of the printed map alone
        # would have deleted.
        assert saved.getpixel((box[0] + 20, (box[1] + box[3]) // 2)) != \
            images.WHITE, "the tray's rim was cut off with its background"


def test_the_studio_button_falls_back_to_the_border_too(monkeypatch):
    """The studio is where this matters most: it is the button a seller
    presses AFTER a batch left the photo as shot, so a second refusal is the
    end of the road for that listing."""
    img, _box, _inner = _tray()
    _model(monkeypatch, _nothing)

    out, engine = images.remove_background_white(img)

    assert engine == "border"
    assert out.getpixel((5, 5)) == images.WHITE


def test_an_item_that_is_not_a_rectangle_is_still_kept_as_shot(tmp_path, monkeypatch):
    """The direction of error is unchanged. A garment Photoroom would not
    keep has no border to scan, and inventing one would cut a listing's item
    down to a box drawn around part of it. Nothing happens, and the seller is
    told."""
    _model(monkeypatch, _nothing)

    out = images.optimize(_photo_file(tmp_path, _garment()),
                          tmp_path / "img_000.jpg", remove_bg=True)

    assert out["background_removed"] is False
    assert out["bg_error"]


def test_a_photo_the_model_does_cut_out_never_reaches_the_border(tmp_path, monkeypatch):
    """The other half of the same promise: the fallback is a fallback. A
    shirt that mattes properly is shipped as Photoroom cut it."""
    img = _garment()

    def _the_shirt(rgb):
        out = Image.new("L", rgb.size, 0)
        ImageDraw.Draw(out).polygon(
            [(460, 230), (740, 230), (940, 380), (860, 470), (760, 400),
             (760, 760), (440, 760), (440, 400), (340, 470), (260, 380)],
            fill=255)
        return out

    _model(monkeypatch, _the_shirt)

    out = images.optimize(_photo_file(tmp_path, img),
                          tmp_path / "img_000.jpg", remove_bg=True)

    assert out["background_removed"] is True
    assert out["bg_engine"] == "photoroom"


def test_an_outage_is_said_not_papered_over_with_a_border(tmp_path, monkeypatch):
    """The rescue answers "Photoroom found nothing", which is a fact about
    the photo. An empty account or an outage is a fact about the service, and
    cutting the trays while the shirts keep their backgrounds would hide it
    behind a pile that half works."""
    img, _box, _inner = _tray()

    def _down(rgb):
        raise cutout_api.PhotoroomError(cutout_api.UNAVAILABLE)

    monkeypatch.setattr(cutout_api, "photoroom_cutout", _down)

    out = images.optimize(_photo_file(tmp_path, img),
                          tmp_path / "img_000.jpg", remove_bg=True)

    assert out["background_removed"] is False
    assert out["bg_error"] == cutout_api.UNAVAILABLE

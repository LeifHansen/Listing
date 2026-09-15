"""A remote engine does the cutting, and the local model is the floor.

The complaint this answers: background removal "either fails miserably, or
doesn't try". Both halves were real and they had different causes. The matte
came from the on-server model and then had to clear three gates fitted to that
model's failures -- which is what refused necklaces, belts, bangles and
guitars on mattes that were fine -- and one inference took about 104 seconds.
So a paid engine supplies the matte instead, and the gates it has to answer
shrink to the one that is engine-independent: did anything survive at all.

What is pinned here:

  * the chain is walked in order, and "local" is always its floor, so an app
    with no key configured behaves exactly as it did and spends nothing;
  * a remote success never touches the local model or its inference lock --
    the lock exists to stop two rembg runs doubling peak RAM, and a remote
    call holds no model here;
  * a remote failure falls THROUGH to the local model rather than losing the
    photo;
  * the engine that actually ran is reported, because a key that is expired
    or mistyped otherwise shows up only as cutouts quietly getting worse;
  * and a framed print, which today gets no cutout at all when its border
    cannot be scanned, gets one -- as a FILLED RECTANGLE, so the thing
    services/artwork exists to prevent stays impossible.

No httpx here on purpose. The engines are stubbed at the cutout_api boundary,
because the `cutout` CI job installs Pillow and pytest and nothing else, and
fails on a SKIP. What talks to the wire is tested in
test_a_cutout_api_failure_is_explained.py, which runs in the full job.
"""
from __future__ import annotations

import threading

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend import config  # noqa: E402
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
    """What a remote engine returns: RGBA, opaque only inside `box`."""
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]),
                        ITEM + (255,)), (box[0], box[1]))
    return out


def _engine(returns=None, raises=None, log=None):
    """A stand-in remote engine. Records every photo it was asked about."""
    def _run(rgb):
        if log is not None:
            log.append(rgb.size)
        if raises is not None:
            raise raises
        return returns(rgb) if callable(returns) else returns
    return _run


@pytest.fixture()
def no_local(monkeypatch):
    """Make the local model fail loudly, so anything that reaches it shows."""
    def _boom(rgb, wait=None):
        raise AssertionError("the local model was asked and should not have been")
    monkeypatch.setattr(images, "_mask", _boom)


@pytest.fixture()
def chain(monkeypatch):
    """Choose the engine chain without touching the environment."""
    def _set(*names):
        monkeypatch.setattr(config, "bg_engine_chain", lambda: list(names))
    return _set


def _remote(monkeypatch, **engines):
    """Install stand-in remote engines under cutout_api.ENGINES."""
    monkeypatch.setattr(cutout_api, "ENGINES", dict(engines))


# --- the chain -------------------------------------------------------------

def test_with_no_key_the_chain_is_the_local_model_alone(monkeypatch):
    """The guarantee that lets this ship: configure nothing, change nothing."""
    monkeypatch.setattr(config, "BG_ENGINE", "auto")
    monkeypatch.setattr(config, "REMOVEBG_API_KEY", "")
    monkeypatch.setattr(config, "LEONARDO_API_KEY", "")
    assert config.bg_engine_chain() == ["local"]


def test_a_configured_engine_goes_in_front_of_the_local_model(monkeypatch):
    monkeypatch.setattr(config, "BG_ENGINE", "auto")
    monkeypatch.setattr(config, "REMOVEBG_API_KEY", "k")
    monkeypatch.setattr(config, "LEONARDO_API_KEY", "")
    assert config.bg_engine_chain() == ["removebg", "local"]

    monkeypatch.setattr(config, "LEONARDO_API_KEY", "k2")
    assert config.bg_engine_chain() == ["removebg", "leonardo", "local"]


def test_naming_an_engine_without_its_key_falls_back_to_what_is_configured(
        monkeypatch):
    """BG_ENGINE is a request, not a promise: a key that isn't there cannot
    strip a background, and failing every photo instead is worse."""
    monkeypatch.setattr(config, "BG_ENGINE", "removebg")
    monkeypatch.setattr(config, "REMOVEBG_API_KEY", "")
    monkeypatch.setattr(config, "LEONARDO_API_KEY", "k")
    assert config.bg_engine_chain() == ["leonardo", "local"]


def test_local_can_be_demanded_outright(monkeypatch):
    monkeypatch.setattr(config, "BG_ENGINE", "local")
    monkeypatch.setattr(config, "REMOVEBG_API_KEY", "k")
    assert config.bg_engine_chain() == ["local"]


def test_the_chain_always_ends_at_local(monkeypatch):
    """Whatever else is true, there is always something that can answer."""
    for engine in ("auto", "removebg", "leonardo", "local", "nonsense"):
        monkeypatch.setattr(config, "BG_ENGINE", engine)
        for key in ("", "k"):
            monkeypatch.setattr(config, "REMOVEBG_API_KEY", key)
            monkeypatch.setattr(config, "LEONARDO_API_KEY", key)
            assert config.bg_engine_chain()[-1] == "local"


# --- a remote engine does the work -----------------------------------------

def test_a_remote_cutout_never_wakes_the_local_model(chain, no_local,
                                                     monkeypatch):
    """The whole point of the latency win: no inference, so no lock either."""
    chain("removebg", "local")
    asked = []
    _remote(monkeypatch, removebg=_engine(
        returns=lambda rgb: _cut(rgb.size, (400, 300, 800, 600)), log=asked))

    img = _photo()
    out, engine = images.cutout_with_engine(img)

    assert out is not None
    assert engine == "removebg"
    assert asked == [img.size], "the remote engine got the photo"
    assert not images._INFER_LOCK.locked()


def test_a_remote_cutout_is_composited_on_white(chain, no_local, monkeypatch):
    chain("removebg", "local")
    _remote(monkeypatch, removebg=_engine(
        returns=lambda rgb: _cut(rgb.size, (400, 300, 800, 600))))

    out, _ = images.cutout_with_engine(_photo())

    assert out.mode == "RGB"
    # A corner is backdrop in the source and white in the cutout.
    assert out.getpixel((5, 5)) == images.WHITE
    # The item survived.
    assert out.getpixel((600, 450)) == ITEM


def test_a_matte_that_comes_back_a_different_size_is_scaled_to_the_photo(
        chain, no_local, monkeypatch):
    """A service answers at the size it was sent, and the upload is capped. An
    alpha of one size against a photo of another understates coverage -- which
    would refuse a perfectly good cutout -- and composites at the wrong size."""
    chain("removebg", "local")
    _remote(monkeypatch, removebg=_engine(
        returns=lambda rgb: _cut((600, 450), (200, 150, 400, 300))))

    img = _photo((1200, 900))
    out, engine = images.cutout_with_engine(img)

    assert out is not None, "a half-size matte must not read as an empty one"
    assert out.size == img.size
    assert engine == "removebg"


def test_a_remote_matte_is_not_put_through_the_local_models_gates(
        chain, no_local, monkeypatch):
    """A thin chain -- a necklace -- is what the shape gate refused on a
    flawless matte. A remote matte answers coverage and nothing else."""
    chain("removebg", "local")

    def _chain_matte(rgb):
        out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(out)
        w, h = rgb.size
        # A 6px-wide loop: one object, filling almost none of its own box.
        draw.ellipse((w // 3, h // 4, 2 * w // 3, 3 * h // 4),
                     outline=ITEM + (255,), width=6)
        return out

    _remote(monkeypatch, removebg=_engine(returns=_chain_matte))

    out, engine = images.cutout_with_engine(_photo())

    assert out is not None, "a remote matte must not be refused for its shape"
    assert engine == "removebg"


def test_a_remote_engine_that_keeps_nothing_is_still_refused(chain,
                                                             monkeypatch):
    """The one gate that survives. An empty matte is an empty matte whoever
    produced it, and shipping it would erase the item — so it is refused and
    the local model gets its turn instead."""
    chain("removebg", "local")
    _remote(monkeypatch, removebg=_engine(
        returns=lambda rgb: Image.new("RGBA", rgb.size, (0, 0, 0, 0))))
    # The local model finds nothing either, so the photo is kept as shot.
    monkeypatch.setattr(images, "_mask",
                        lambda rgb, wait=None: Image.new("L", rgb.size, 0))

    out, engine = images.cutout_with_engine(_photo())

    assert out is None, "an empty remote matte must never ship"
    assert engine == "local"


# --- failures fall through, they do not lose the photo ---------------------

def test_a_failed_remote_engine_falls_through_to_the_next_one(chain, no_local,
                                                              monkeypatch):
    chain("removebg", "leonardo", "local")
    tried = []
    _remote(
        monkeypatch,
        removebg=_engine(raises=cutout_api.RemoveBgError("out of credits"),
                         log=tried),
        leonardo=_engine(returns=lambda rgb: _cut(rgb.size, (400, 300, 800, 600)),
                         log=tried))

    out, engine = images.cutout_with_engine(_photo())

    assert engine == "leonardo", "the second engine answered"
    assert out is not None
    assert len(tried) == 2, "both were asked, in order"


def test_a_failed_remote_engine_falls_back_to_the_local_model(chain,
                                                              monkeypatch):
    """An expired key must not cost the seller their photo."""
    chain("removebg", "local")
    _remote(monkeypatch, removebg=_engine(
        raises=cutout_api.RemoveBgError("rejected the API key")))
    monkeypatch.setattr(
        images, "_mask",
        lambda rgb, wait=None: rgb.convert("L").point(
            lambda v: 255 if v < 128 else 0))

    out, engine = images.cutout_with_engine(_photo())

    assert out is not None, "the local model picked it up"
    assert engine == "local"


def test_with_no_local_floor_the_reason_reaches_the_seller(chain, monkeypatch):
    """When local is not in the chain there is nothing to fall back to, so the
    real cause has to be raised rather than swallowed -- "out of credits" is
    something somebody can act on."""
    chain("removebg")
    _remote(monkeypatch, removebg=_engine(
        raises=cutout_api.RemoveBgError(
            "The remove.bg account is out of credits")))

    with pytest.raises(cutout_api.CutoutApiError, match="out of credits"):
        images.cutout_with_engine(_photo())


def test_the_studio_reports_which_engine_answered(chain, no_local, monkeypatch):
    """remove_background_white's second return value is what the editor shows;
    hardcoding "local" is how a misconfigured key hides."""
    chain("removebg", "local")
    _remote(monkeypatch, removebg=_engine(
        returns=lambda rgb: _cut(rgb.size, (400, 300, 800, 600))))

    _, engine = images.remove_background_white(_photo())

    assert engine == "removebg"


def test_the_readiness_probe_names_the_chain(chain):
    """One field to answer "is the paid engine actually being used?"."""
    chain("removebg", "local")
    assert images.engine_state()["chain"] == ["removebg", "local"]


def test_a_deploy_with_no_paid_engine_is_not_degraded(chain):
    """local IS the engine there, so the studio must not imply anything is
    wrong — saying it on every cutout would be pure noise."""
    chain("local")
    assert images.engine_degraded("local") is False


def test_falling_back_to_local_with_a_key_configured_is_degraded(chain):
    """The case worth a word: somebody is paying for an engine that did not
    answer. Silence here is how an expired key goes unnoticed for weeks."""
    chain("removebg", "local")
    assert images.engine_degraded("local") is True
    assert images.engine_degraded("removebg") is False


# --- the framed print, which is the loudest half of the complaint ----------

def _framed_print(size=(1200, 900), box=(280, 160, 900, 760)):
    """A print in a frame on a surface almost its own colour -- the case
    artwork.border() is entitled to give up on."""
    img = Image.new("RGB", size, (206, 202, 196))
    draw = ImageDraw.Draw(img)
    draw.rectangle(box, fill=(210, 206, 200), outline=(198, 194, 188), width=3)
    inner = (box[0] + 40, box[1] + 40, box[2] - 40, box[3] - 40)
    draw.rectangle(inner, fill=(250, 248, 244))
    draw.ellipse((inner[0] + 60, inner[1] + 60, inner[2] - 60, inner[3] - 60),
                 fill=(120, 150, 170))
    return img, box


def test_a_framed_print_gets_a_cutout_when_the_border_cannot_be_scanned(
        chain, no_local, monkeypatch):
    """The reported bug: "doesn't try for simple objects (rectangular framed
    print)". It didn't try because the geometric scan returned None, which
    correctly means keep-as-shot. A remote engine can find the frame."""
    img, box = _framed_print()
    chain("removebg", "local")
    _remote(monkeypatch, removebg=_engine(returns=lambda rgb: _cut(rgb.size, box)))
    monkeypatch.setattr(artwork, "border", lambda rgb: None)

    out = images.art_cutout(img)

    assert out is not None, "the print should finally get a cutout"
    # Outside the frame is white; inside is untouched artwork.
    assert out.getpixel((5, 5)) == images.WHITE
    assert out.getpixel(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)) == \
        img.getpixel(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))


def test_the_scanned_border_still_wins(chain, no_local, monkeypatch):
    """Geometry first: it cannot be wrong about what is inside the box it
    returns, so a remote engine is only ever the second opinion."""
    img, box = _framed_print()
    chain("removebg", "local")
    asked = []
    _remote(monkeypatch, removebg=_engine(
        returns=lambda rgb: _cut(rgb.size, box), log=asked))
    monkeypatch.setattr(artwork, "border", lambda rgb: (10, 10, 100, 100))

    images.art_cutout(img)

    assert asked == [], "no remote call when the scan already answered"


def test_a_subject_lifted_out_of_a_painting_is_refused(chain, no_local,
                                                       monkeypatch):
    """The baby out of the basket. A remote engine handed a photo OF a picture
    answers the only question it knows -- which part of this is the subject --
    and for a painting the honest answer is "all of it". The box it offers is
    rejected on SHAPE: a picture fills its own bounding box, a baby does not."""
    img, _ = _framed_print()
    chain("removebg", "local")

    def _the_baby(rgb):
        out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
        ImageDraw.Draw(out).ellipse((500, 350, 760, 610), fill=ITEM + (255,))
        return out

    _remote(monkeypatch, removebg=_engine(returns=_the_baby))
    monkeypatch.setattr(artwork, "border", lambda rgb: None)

    assert images.art_cutout(img) is None, \
        "a round subject is not a picture's border — keep the photo as shot"


def test_nothing_inside_the_border_is_ever_removed(chain, no_local, monkeypatch):
    """The invariant services/artwork exists for, held on the new path too:
    whatever the remote matte said, what ships is a FILLED RECTANGLE."""
    img, box = _framed_print()
    chain("removebg", "local")

    def _holes_in_the_art(rgb):
        """A matte shaped like the frame but with a piece of the artwork
        punched out of it — exactly the shape that must NOT reach the
        composite. Kept small enough that the box is ACCEPTED (it still fills
        ~0.95 of itself), so this exercises the rectangle rather than the
        shape gate: the hole has to be filled back in by artwork.mask, not
        rejected on its way past."""
        out = _cut(rgb.size, box)
        ImageDraw.Draw(out).ellipse((560, 400, 710, 550), fill=(0, 0, 0, 0))
        return out

    _remote(monkeypatch, removebg=_engine(returns=_holes_in_the_art))
    monkeypatch.setattr(artwork, "border", lambda rgb: None)

    out = images.art_cutout(img)

    assert out is not None, "this matte fills its box — the box should stand"
    # Every one of these sits inside the hole the matte tried to punch.
    for xy in ((600, 450), (630, 470), (635, 475), (660, 500)):
        assert out.getpixel(xy) == img.getpixel(xy), \
            f"pixel {xy} inside the border was altered"


def test_a_picture_still_never_gets_the_remote_matte_itself(chain, no_local,
                                                            monkeypatch):
    """Art takes a BOX from a remote engine and never its matte. Proven by the
    edges: a rectangle's corners are opaque, and the ellipse the engine
    actually returned would have left them white."""
    img, box = _framed_print()
    chain("removebg", "local")

    def _rounded(rgb):
        out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
        ImageDraw.Draw(out).rounded_rectangle(box, radius=120,
                                             fill=ITEM + (255,))
        return out

    _remote(monkeypatch, removebg=_engine(returns=_rounded))
    monkeypatch.setattr(artwork, "border", lambda rgb: None)

    out = images.art_cutout(img)
    assert out is not None
    corner = (box[0] + 6, box[1] + 6)
    assert out.getpixel(corner) == img.getpixel(corner), \
        "the corner was rounded off the print — that is the matte, not a box"


def test_the_local_model_is_never_asked_about_a_picture(chain, monkeypatch):
    """Unchanged and load-bearing: art must not reach the model, remote engine
    configured or not."""
    img, _ = _framed_print()
    chain("removebg", "local")
    _remote(monkeypatch, removebg=_engine(returns=None))
    calls = []
    monkeypatch.setattr(images, "_mask",
                        lambda rgb, wait=None: calls.append(1) or
                        Image.new("L", rgb.size, 255))
    monkeypatch.setattr(artwork, "border", lambda rgb: None)

    assert images.art_cutout(img) is None
    assert calls == [], "the model was asked about a picture"


def test_the_inference_lock_is_untouched_by_a_remote_run(chain, no_local,
                                                          monkeypatch):
    """A remote engine must work while the single local slot is BUSY: that is
    what turns ~104s serialized into a batch that actually moves."""
    chain("removebg", "local")
    _remote(monkeypatch, removebg=_engine(
        returns=lambda rgb: _cut(rgb.size, (400, 300, 800, 600))))

    held = threading.Lock()
    monkeypatch.setattr(images, "_INFER_LOCK", held)
    held.acquire()
    try:
        out, engine = images.cutout_with_engine(_photo())
    finally:
        held.release()

    assert out is not None, "a remote cutout must not queue behind the model"
    assert engine == "removebg"

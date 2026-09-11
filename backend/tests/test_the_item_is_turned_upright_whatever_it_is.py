"""Auto-orientation, rebuilt for objects.

Reported as: the auto-rotator works for shirts and is horrible for objects.
It was. The pass that decided which way up the ITEM lay was written around a
shirt — "collar at the top, hem at the bottom" was its reference image, every
other item was judged as a worse shirt, and it had no idea that a coin, a
plate or a wallet shot from above has no wrong way up, so it invented one.
Its thumbnails were too small to read the label that is the one reliable cue
on an object, and its safety net, a yes/no about the turned photo told to
say no when unsure, cancelled the right turns and confirmed the wrong ones.

What the rebuilt pass has to keep to (services/orient):

  * a turn the model is not sure of is no turn;
  * an item laid flat, or a close-up, can only be turned on the strength of
    readable text — those photos have no "up" for a model to find;
  * a standing, hanging, worn or laid-flat garment's turn is proposed, then
    CONFIRMED by a different question: the photo at all four quarter-turns,
    and the model must pick the proposal for it to ship;
  * the four versions are not always lettered in the same order, so a model
    that favours a letter cannot systematically confirm;
  * the screen looks at a copy large enough to read a label;
  * the turn is applied before the cutout, so the shadow falls BELOW the
    upright item;
  * the pass is bounded, best-effort, and never costs a photo: past its
    budget, called off, failing, or with the API off, photos ship as shot;
  * the studio's Restore original and every other direct call to optimize()
    ship the photo as shot — only the batch pass decides a turn.

Pillow only, with the model stood in for by canned answers: what is under
test is what the pass does with an answer, not the answer.
"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402

from backend.services import images, orient  # noqa: E402

BACKDROP = (238, 236, 232)
ITEM = (40, 50, 70)


def _photo(tmp: Path, name: str = "src.jpg", size=(1200, 900)) -> Path:
    """A dark item standing in the lower-middle of a light backdrop —
    landscape, so a quarter turn is visible in the output's size."""
    img = Image.new("RGB", size, BACKDROP)
    w, h = size
    ImageDraw.Draw(img).rectangle((w // 3, h // 2, 2 * w // 3, 5 * h // 6), fill=ITEM)
    src = tmp / name
    img.save(src, "JPEG", quality=92)
    return src


def _sizes_sent(content: list[dict]) -> list[tuple[int, int]]:
    """The pixel size of every image block in a request, in order."""
    out = []
    for block in content:
        if block.get("type") == "image":
            data = base64.standard_b64decode(block["source"]["data"])
            with Image.open(BytesIO(data)) as im:
                out.append(im.size)
    return out


def _texts_sent(content: list[dict]) -> str:
    return "\n".join(b["text"] for b in content if b.get("type") == "text")


class _Model:
    """Stand-in for the vision model: records every request, answers from a
    queue. An exception in the queue is raised as the call's failure."""

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.answers: list = []

    def ask(self, content, max_tokens):
        self.calls.append(content)
        if not self.answers:
            return {"photos": []}
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture()
def model(monkeypatch):
    m = _Model()
    monkeypatch.setattr(orient, "_enabled", lambda: True)
    monkeypatch.setattr(orient, "_ask", m.ask)
    monkeypatch.setattr(orient, "_WORKERS", 1)   # calls land in queue order
    return m


def _screen(*entries) -> dict:
    """A screen answer: one entry per photo, a sure standing item with no
    text and no turn unless the entry says otherwise."""
    photos = []
    for i, e in enumerate(entries):
        entry = {"photo": i + 1, "item": "thing", "sits": "standing",
                 "text": "none", "rotate": 0, "sure": False}
        entry.update(e)
        photos.append(entry)
    return {"photos": photos}


def _confirm(*picks) -> dict:
    return {"photos": [{"photo": i + 1, "upright": p}
                       for i, p in enumerate(picks)]}


# --- the rules that do not depend on the model's judgement ------------------

def test_an_unsure_turn_is_no_turn(tmp_path):
    p = _photo(tmp_path)
    assert orient._proposals(_screen({"rotate": 90, "sure": False}), [p]) == {}
    assert orient._proposals(_screen({"rotate": 90, "sure": "false"}), [p]) == {}
    assert orient._proposals(_screen({"rotate": 90, "sure": True}), [p]) == {"src.jpg": 90}
    # The model writes JSON; "true" as a string is still yes.
    assert orient._proposals(_screen({"rotate": 90, "sure": "true"}), [p]) == {"src.jpg": 90}


@pytest.mark.parametrize("sits", ["flat", "detail", "", "something_new"])
def test_a_flat_lay_or_a_close_up_is_only_turned_by_readable_text(tmp_path, sits):
    """A coin, a plate, a wallet or a tool shot from above has no wrong way
    up, and a close-up of a stitch has none at all. The old pass, asked which
    way to turn one, invented an answer — the objects that came back
    sideways. However sure the model says it is, without text these stay as
    shot; with text reading sideways, the text decides."""
    p = _photo(tmp_path)
    sure = {"rotate": 90, "sure": True, "sits": sits}
    assert orient._proposals(_screen({**sure, "text": "none"}), [p]) == {}
    assert orient._proposals(_screen({**sure}), [p]) == {}
    assert orient._proposals(_screen({**sure, "text": "sideways"}), [p]) == {"src.jpg": 90}


@pytest.mark.parametrize("sits", ["standing", "hanging", "worn", "garment_flat"])
def test_an_item_with_an_up_of_its_own_can_be_turned_without_text(tmp_path, sits):
    p = _photo(tmp_path)
    assert orient._proposals(
        _screen({"rotate": 270, "sure": True, "sits": sits, "text": "none"}),
        [p]) == {"src.jpg": 270}


def test_text_that_already_reads_upright_settles_it(tmp_path):
    """Readable text wins in both directions. A label reading the right way
    up says the photo is upright, whatever the model made of the item — so a
    turn proposed against it is refused here, not left to the second look."""
    p = _photo(tmp_path)
    assert orient._proposals(
        _screen({"rotate": 90, "sure": True, "sits": "standing", "text": "upright"}),
        [p]) == {}
    assert orient._proposals(
        _screen({"rotate": 180, "sure": True, "sits": "standing", "text": "upside_down"}),
        [p]) == {"src.jpg": 180}


def test_nonsense_from_the_model_proposes_nothing(tmp_path):
    p = _photo(tmp_path)
    assert orient._proposals({"photos": [
        {"photo": 1, "rotate": 45, "sure": True},        # not a quarter turn
        {"photo": 7, "rotate": 90, "sure": True},        # no such photo
        {"photo": "x", "rotate": 90, "sure": True},      # not a number
        "ninety",                                        # not an entry
        {"photo": 1, "rotate": 0, "sure": True},         # upright already
    ]}, [p]) == {}
    assert orient._proposals({"photos": None}, [p]) == {}
    assert orient._proposals(["not", "a", "dict"], [p]) == {}


# --- the confirm: a different question, answered by comparison ---------------

def test_the_four_versions_are_not_always_lettered_the_same_way():
    """A model that always answers "A" would otherwise always cancel — or, if
    the proposal were always "B", always confirm. Rotating the order per
    photo turns a position habit into noise."""
    orders = {orient._candidate_order(k) for k in range(4)}
    assert len(orders) == 4
    for k in range(8):
        assert sorted(orient._candidate_order(k)) == [0, 90, 180, 270]
    assert orient._candidate_order(0) == (0, 90, 180, 270)
    assert orient._candidate_order(1) == (90, 180, 270, 0)


def test_a_turn_ships_only_when_the_second_look_picks_it(tmp_path):
    a, b, c, d = (_photo(tmp_path, f"{n}.jpg") for n in "abcd")
    proposals = [(a, 90), (b, 90), (c, 90), (d, 180)]
    orders = [orient._candidate_order(k) for k in range(4)]
    picked = orient._confirmed(_confirm(
        "B",      # photo 1: order (0,90,180,270) -> B is 90: confirmed
        "A",      # photo 2: order (90,180,270,0) -> A is 90: confirmed
        "none",   # photo 3: the model saw no upright version: cancelled
        "C",      # photo 4: order (270,0,90,180) -> C is 90, not 180: cancelled
    ), proposals, orders)
    assert picked == {"a.jpg": 90, "b.jpg": 90}


def test_no_answer_for_a_photo_cancels_its_turn(tmp_path):
    a = _photo(tmp_path, "a.jpg")
    assert orient._confirmed({"photos": []}, [(a, 90)], [(0, 90, 180, 270)]) == {}
    assert orient._confirmed({"photos": [{"photo": 1, "upright": "Z"}]},
                             [(a, 90)], [(0, 90, 180, 270)]) == {}
    assert orient._confirmed("garbage", [(a, 90)], [(0, 90, 180, 270)]) == {}


def test_the_confirm_shows_every_quarter_turn_of_the_photo(tmp_path, model):
    """Four images per proposal, lettered, and the letters sit against turns
    in the order _candidate_order says — that mapping is what makes the
    pick mean anything."""
    a = _photo(tmp_path, "a.jpg", size=(1200, 900))
    model.answers = [_confirm("B")]
    assert orient._confirm_batch([(a, 90)]) == {"a.jpg": 90}
    sizes = _sizes_sent(model.calls[0])
    assert len(sizes) == 4
    # Landscape as shot (A), portrait after 90 (B), landscape after 180 (C),
    # portrait after 270 (D).
    assert [w > h for w, h in sizes] == [True, False, True, False]
    text = _texts_sent(model.calls[0])
    for letter in "ABCD":
        assert f"version {letter}:" in text
    assert "none" in text


def test_quarter_turns_are_the_photo_turned_not_resampled(tmp_path):
    turns = images.quarter_turns_jpeg(_photo(tmp_path, size=(1200, 900)), side=400)
    assert set(turns) == {0, 90, 180, 270}
    with Image.open(BytesIO(turns[0])) as im:
        assert im.size == (400, 300)
    with Image.open(BytesIO(turns[90])) as im:
        assert im.size == (300, 400)
        # The item stood in the lower-middle; turned 90 clockwise it is on
        # the left, middle-height. That is the turn the letters name.
        assert max(im.getpixel((60, 200))) < 100
        assert min(im.getpixel((260, 200))) > 200


# --- the screen looks at objects, at a size that can read a label -----------

def test_the_screen_asks_about_objects_and_looks_close_enough_to_read(tmp_path, model):
    a = _photo(tmp_path, "a.jpg", size=(2400, 1800))
    model.answers = [_screen({"rotate": 90, "sure": True, "item": "brown boot"})]
    assert orient._screen_batch([a]).rotations == {"a.jpg": 90}
    (w, h), = _sizes_sent(model.calls[0])
    assert max(w, h) >= 600, "a label is not readable off a smaller copy"
    rules = _texts_sent(model.calls[0])
    # The rubric is about how an ITEM sits, and text comes first.
    for word in ('"standing"', '"hanging"', '"worn"', '"garment_flat"',
                 '"flat"', '"detail"', "text wins"):
        assert word in rules, f"the rubric no longer covers {word}"
    assert "no wrong orientation" in rules.lower()
    assert "sure=false" in rules.lower()


def test_a_file_that_cannot_be_read_costs_only_itself(tmp_path, model):
    """One garbage file in a batch of eight used to skip the whole batch.
    Now the readable photos are sent, numbered as sent, and the answer maps
    back to the right files."""
    a = _photo(tmp_path, "a.jpg")
    junk = tmp_path / "b.jpg"
    junk.write_bytes(b"not a photo at all")
    c = _photo(tmp_path, "c.jpg")
    # The model sees two photos; its "photo 2" is c.jpg, not the junk.
    model.answers = [_screen({}, {"rotate": 90, "sure": True})]
    assert orient._screen_batch([a, junk, c]).rotations == {"c.jpg": 90}
    assert len(_sizes_sent(model.calls[0])) == 2
    assert "photos 1 to 2" in _texts_sent(model.calls[0])
    model.answers = []
    assert orient._screen_batch([junk]) == orient._NOTHING
    assert len(model.calls) == 1, "a batch with nothing readable still asked"


# --- through the photo pass ---------------------------------------------------

def test_a_standing_item_on_its_side_is_turned_upright(tmp_path, model):
    """The whole pass: the screen proposes 90 for a sure standing item, the
    second look picks that version, and the photo ships turned — reported
    as such, with the output's size swapped."""
    src = _photo(tmp_path, size=(1200, 900))
    model.answers = [_screen({"rotate": 90, "sure": True}), _confirm("B")]
    (out,) = images.optimize_batch([(src, tmp_path / "out")])
    assert out["rotated"] == 90
    assert out["output_size"] == (900, 1200)
    with Image.open(tmp_path / "out.jpg") as done:
        assert done.size == (900, 1200)
    assert len(model.calls) == 2, "a screen and a confirm, no more"


def test_a_turn_the_second_look_does_not_pick_is_not_applied(tmp_path, model):
    src = _photo(tmp_path, size=(1200, 900))
    model.answers = [_screen({"rotate": 90, "sure": True}), _confirm("A")]
    (out,) = images.optimize_batch([(src, tmp_path / "out")])
    assert "rotated" not in out
    assert out["output_size"] == (1200, 900)


def test_the_turn_happens_before_the_cutout_so_the_shadow_falls_below(tmp_path, model, monkeypatch):
    """The contact shadow is drawn down and right of the item. Turned after
    the cutout, an upright item would carry its shadow up its left side."""
    monkeypatch.setattr(images, "_mask", lambda rgb, wait=None:
                        rgb.convert("L").point(lambda v: 255 if v < 128 else 0))
    src = _photo(tmp_path, size=(1200, 900))
    model.answers = [_screen({"rotate": 90, "sure": True}), _confirm("B")]
    (out,) = images.optimize_batch([(src, tmp_path / "out")], remove_bg=True)
    assert out["rotated"] == 90 and out["background_removed"]
    with Image.open(tmp_path / "out.jpg") as done:
        assert done.size == (900, 1200)
        # After the turn the item spans x 150-450, y 400-800. Its shadow is
        # just past its bottom-right corner; above-left of it stays white.
        shade = done.getpixel((462, 812))
        assert shade != (255, 255, 255) and 120 < min(shade) < 250, shade
        assert min(done.getpixel((130, 380))) > 250


def test_a_direct_optimize_never_turns_the_photo(tmp_path, model):
    """Restore original re-optimizes the upload directly — and must ship it
    as shot, which is the whole point of restore. So do the studio's edits.
    Only the batch pass asks."""
    src = _photo(tmp_path, size=(1200, 900))
    model.answers = [_screen({"rotate": 90, "sure": True}), _confirm("B")]
    out = images.optimize(src, tmp_path / "out")
    assert "rotated" not in out and out["output_size"] == (1200, 900)
    assert model.calls == [], "a direct call asked the model"


def test_a_nonsense_rotation_is_ignored_rather_than_resampling(tmp_path):
    """Anything but a clean quarter turn would resample — blurring the photo
    and reporting a turn that did not happen."""
    src = _photo(tmp_path, size=(1200, 900))
    out = images.optimize(src, tmp_path / "out", rotate=37)
    assert "rotated" not in out and out["output_size"] == (1200, 900)
    out = images.optimize(src, tmp_path / "out2", rotate=-90)
    assert out["rotated"] == 270


# --- bounded, best-effort, never costs a photo --------------------------------

def test_with_the_api_off_nothing_is_asked_and_photos_ship_as_shot(tmp_path, monkeypatch):
    m = _Model()
    monkeypatch.setattr(orient, "_ask", m.ask)
    monkeypatch.setattr(orient, "_enabled", lambda: False)
    src = _photo(tmp_path, size=(1200, 900))
    (out,) = images.optimize_batch([(src, tmp_path / "out")])
    assert "rotated" not in out and m.calls == []


def test_a_failing_call_costs_no_photo(tmp_path, model):
    """The screen falls over: every photo ships as shot. The confirm falls
    over: nothing it was asked about is applied."""
    a, b = _photo(tmp_path, "a.jpg"), _photo(tmp_path, "b.jpg")
    model.answers = [RuntimeError("the API is down")]
    results = images.optimize_batch([(a, tmp_path / "a_out"), (b, tmp_path / "b_out")])
    assert [r["file"] for r in results] == ["a_out.jpg", "b_out.jpg"]
    assert not any("rotated" in r or "error" in r for r in results)

    model.answers = [_screen({"rotate": 90, "sure": True}, {"rotate": 90, "sure": True}),
                     RuntimeError("the API is down")]
    results = images.optimize_batch([(a, tmp_path / "a2"), (b, tmp_path / "b2")])
    assert not any("rotated" in r for r in results)


def test_past_its_budget_the_pass_stops_asking(tmp_path, model, monkeypatch):
    """A batch of 250 photos must not be able to spend ten minutes deciding
    which way up they are. Past the budget, the remaining calls are skipped
    and their photos stay as shot."""
    paths = [_photo(tmp_path, f"p{i}.jpg") for i in range(20)]
    monkeypatch.setattr(orient, "_TOTAL_BUDGET", -1.0)   # already spent
    monkeypatch.setattr(orient, "_PER_PHOTO", -1.0)
    assert orient.detect_rotations(paths) == {}
    assert model.calls == []


def test_a_bulk_pile_gets_a_budget_that_grows_with_it():
    """A listing's forty photos are bounded by the floor; a 250-photo bulk
    batch, which nobody holds a request open for, gets time to be answered
    for in full rather than straightening the first hundred and leaving the
    rest as they were."""
    assert orient._budget_for(1) == orient._TOTAL_BUDGET
    assert orient._budget_for(40) == orient._TOTAL_BUDGET
    assert orient._budget_for(250) == 250 * orient._PER_PHOTO > orient._TOTAL_BUDGET


def test_a_cancelled_batch_stops_paying_for_orientation(tmp_path, model):
    paths = [_photo(tmp_path, f"p{i}.jpg") for i in range(20)]
    assert orient.detect_rotations(paths, should_stop=lambda: True) == {}
    assert model.calls == []
    # And the photo pass itself does not start on a batch already called off.
    with pytest.raises(images.Stopped):
        images.optimize_batch([(paths[0], tmp_path / "out")], should_stop=lambda: True)


def test_the_pass_has_a_call_timeout_and_a_total_budget():
    """It runs inside a background job, but a wedged call would still pin
    that job. The shared Claude client allows 120s x 3 attempts; this must
    not."""
    assert orient._CALL_TIMEOUT <= 60
    assert orient._TOTAL_BUDGET <= 300
    assert orient._budget_for(250) <= 600


def test_the_suite_runs_with_the_pass_off(monkeypatch):
    """conftest switches the pass off for every other photo test, so a
    developer's real key is never spent on synthetic squares. Pin the
    switch, and that the flag is what _enabled reads."""
    import os
    assert os.environ.get("AUTO_ORIENT") == "off"
    monkeypatch.setenv("AUTO_ORIENT", "on")
    monkeypatch.setattr(orient.config, "anthropic_ready", lambda: False)
    assert orient._enabled() is False
    monkeypatch.setattr(orient.config, "anthropic_ready", lambda: True)
    assert orient._enabled() is True
    monkeypatch.setenv("AUTO_ORIENT", "off")
    assert orient._enabled() is False

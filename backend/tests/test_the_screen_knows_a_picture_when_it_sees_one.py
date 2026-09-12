"""The one pass that can tell a painting from a thing with a painting on it.

The Marcia Alpert report — a gouache whose listing photos came back with the
baby cut out of the painting — is a failure nothing downstream can catch.
Every guard in services/images reads the ALPHA, and the matte of a baby lifted
out of a painting is one connected, solid, box-filling region: arithmetically
a perfect cutout. The fact that separates it from one is that the item IS an
image, and that fact is only in the PHOTO.

services/orient is the only pass that looks at the photo before the cutout
runs, and it is already being made and already paid for. So it is asked one
more question per photo, and its answer routes the picture down a different
path entirely (services/artwork, images.art_cutout).

This file is the question and the answer: what counts as a picture, what does
not, and what happens to each of the ways the model can fail to say.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PIL")
pytest.importorskip("anthropic")

from PIL import Image  # noqa: E402

from backend.services import orient  # noqa: E402


def _photo(dirpath, name="a.jpg") -> "object":
    p = dirpath / name
    Image.new("RGB", (60, 40), (200, 180, 160)).save(p, "JPEG")
    return p


def _screen(*entries) -> dict:
    """A screen answer: one entry per photo, nothing remarkable about any of
    them unless the entry says otherwise."""
    photos = []
    for i, e in enumerate(entries):
        entry = {"photo": i + 1, "item": "thing", "sits": "standing",
                 "art": False, "text": "none", "rotate": 0, "sure": False}
        entry.update(e)
        photos.append(entry)
    return {"photos": photos}


# ------------------------------------------------------------- the question

def test_the_screen_asks_whether_the_item_is_a_picture():
    rules = orient._SCREEN_RULES
    assert '"art"' in rules
    for word in ("painting", "print", "poster", "drawing", "photograph"):
        assert word in rules.lower(), word
    # The distinction that stops a mug with a boat on it being called art.
    assert "front surface IS an image" in rules
    # ...and the answer is in the shape the model is told to return.
    assert '"art": false' in rules


def test_the_rule_tells_the_model_to_answer_true_when_torn():
    """The two errors are wildly unequal: something wrongly called a picture
    keeps a margin of background, and a picture MISSED is a painting with a
    hole in it on a live listing. The prompt has to say which way to lean."""
    assert "answer true" in orient._SCREEN_RULES


# --------------------------------------------------------------- the answer

def test_a_picture_is_routed_away_from_the_model(tmp_path):
    p = _photo(tmp_path)
    assert orient._art(_screen({"art": True, "item": "a gouache"}), [p]) \
        == frozenset({"a.jpg"})


def test_everything_else_is_left_exactly_as_it_was(tmp_path):
    p = _photo(tmp_path)
    assert orient._art(_screen({"art": False}), [p]) == frozenset()


@pytest.mark.parametrize("said", [True, "true", "True", "yes", 1])
def test_the_ways_a_model_says_yes(tmp_path, said):
    """The screen's other booleans go through the same reader (_is_true), and
    a picture missed because the model wrote "yes" instead of true is a
    painting with a hole in it."""
    p = _photo(tmp_path)
    assert orient._art(_screen({"art": said}), [p]) == frozenset({"a.jpg"})


@pytest.mark.parametrize("said", [False, "false", "no", 0, None, "", "maybe"])
def test_anything_that_is_not_a_yes_is_a_no(tmp_path, said):
    p = _photo(tmp_path)
    assert orient._art(_screen({"art": said}), [p]) == frozenset()


def test_a_missing_answer_is_not_a_picture(tmp_path):
    """An older or truncated answer with no "art" key at all. The photo takes
    the path it took before this existed — which is today's behaviour, with
    the shape guards in cutout() standing behind it."""
    p = _photo(tmp_path)
    answer = {"photos": [{"photo": 1, "item": "a mug", "sits": "standing"}]}
    assert orient._art(answer, [p]) == frozenset()


@pytest.mark.parametrize("junk", [
    {}, {"photos": None}, {"photos": [None]}, {"photos": ["nope"]},
    {"photos": [{"photo": "x", "art": True}]},
    {"photos": [{"photo": 99, "art": True}]},     # out of range
])
def test_nonsense_names_no_picture(tmp_path, junk):
    """Never raises. A malformed answer costs the pass, not the upload."""
    assert orient._art(junk, [_photo(tmp_path)]) == frozenset()


def test_each_photo_is_judged_on_its_own(tmp_path):
    """One set mixes the front of a painting with the back of it, its
    packaging and a close-up of the signature — and only some of those are
    pictures."""
    ps = [_photo(tmp_path, f"{n}.jpg") for n in "abcd"]
    found = orient._art(_screen({"art": True}, {"art": False},
                                {"art": False}, {"art": True}), ps)
    assert found == frozenset({"a.jpg", "d.jpg"})


# ------------------------------------------------- and it reaches the pass

def test_the_screen_carries_the_pictures_out_with_the_turns(tmp_path,
                                                            monkeypatch):
    """Whatever else the pass decides, the picture list survives to the
    caller — including through the second look, which is about turns and has
    no opinion on this."""
    monkeypatch.setattr(orient, "_enabled", lambda: True)
    monkeypatch.setattr(orient, "_WORKERS", 1)
    monkeypatch.setattr(orient, "_ask",
                        lambda content, max_tokens: _screen({"art": True}))

    found = orient.screen([_photo(tmp_path)])

    assert found.art == frozenset({"a.jpg"})


def test_with_the_pass_off_nothing_is_a_picture(tmp_path, monkeypatch):
    """The empty answer, and what it means: a picture nobody got to look at
    goes to the model, exactly as it did before any of this. Worth stating,
    because that IS the reported failure — it is why this answer is worth a
    pass rather than a guess in the photo code."""
    monkeypatch.setattr(orient, "_enabled", lambda: False)
    assert orient.screen([_photo(tmp_path)]) == orient._NOTHING
    assert orient._NOTHING.art == frozenset()

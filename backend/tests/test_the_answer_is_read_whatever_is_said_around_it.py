"""Three small ways a good answer was lost after it was paid for.

1. A research or art answer written with a remark after the JSON -- "Note:
   the {first state} is rarer" -- has a closing brace AFTER the object. The
   reader took everything from the first "{" to the LAST "}", the remark came
   with it, and a correct answer was thrown away as unreadable.

2. A refusal (stop_reason "refusal") carries no text. identify, refine and
   fresh_copy said so; grouping, the shelf scan, the tag reader and the three
   item-specifics fills read the empty answer as broken JSON and told the
   seller to try again -- which does not help, because the photos have not
   changed. The tag reader went further and called it "no tags".

3. An art lookup that measured a print in centimetres had the numbers read as
   inches: "50 x 70 cm" became a 50 x 70 inch frame, a box of 56 x 76, and a
   warning that the parcel could not be posted at all.
"""
from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend.models import Listing  # noqa: E402
from backend.services import claude_ai  # noqa: E402
from backend.services.experts.art import presentation  # noqa: E402


# --- 1. the remark after the answer -------------------------------------------

def test_a_remark_after_the_json_does_not_lose_the_answer():
    text = ('I searched the catalogue.\n{"identified": "Butcher cover", '
            '"value_low": 700}\n\nNote: the {first state} is rarer.')
    assert claude_ai._extract_json(text) == {"identified": "Butcher cover",
                                             "value_low": 700}


def test_an_answer_alone_is_read_exactly_as_before():
    assert claude_ai._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert claude_ai._extract_json('{"a": "34\\" x 28\\""}') == {"a": '34" x 28"'}
    with pytest.raises(json.JSONDecodeError):
        claude_ai._extract_json("no json here")


# --- 2. a refusal is a refusal, on every pass ---------------------------------

class _Refusing:
    def __init__(self):
        self.messages = self

    def create(self, **kw):
        return types.SimpleNamespace(stop_reason="refusal", content=[],
                                     usage=None)


@pytest.fixture()
def refused(monkeypatch, tmp_path):
    from PIL import Image
    photo = tmp_path / "img_000.jpg"
    Image.new("RGB", (64, 64), "white").save(photo)
    monkeypatch.setattr(claude_ai, "_client", lambda: _Refusing())
    monkeypatch.setattr(claude_ai, "_image_block",
                        lambda p: {"type": "text", "text": "[photo]"})
    return photo


ASPECTS = [{"name": "Color", "mode": "FREE_TEXT", "values": []}]


@pytest.mark.parametrize("call", [
    lambda p: claude_ai.group_photos([p.read_bytes(), p.read_bytes()]),
    lambda p: claude_ai.scan_shelf([p.read_bytes()]),
    lambda p: claude_ai.read_tag_text([p]),
    lambda p: claude_ai.fill_aspects([p], Listing(title="x"), ASPECTS),
    lambda p: claude_ai.fill_aspects_combined([p], Listing(title="x"), ASPECTS),
    lambda p: claude_ai.fill_missing_aspects([p], Listing(title="x"), ASPECTS),
    lambda p: claude_ai.verify_maker([p], Listing(title="x"), "Fenton"),
], ids=["group", "shelf", "tags", "aspects", "aspects-combined",
        "aspects-missing", "verify-maker"])
def test_a_refusal_is_said_as_a_refusal(refused, call):
    with pytest.raises(claude_ai.AIRefused):
        call(refused)


# --- 3. centimetres are not inches --------------------------------------------

def test_a_size_in_centimetres_is_converted_not_read_as_inches():
    assert presentation._inches("50 x 70 cm") == (19.7, 27.6)
    assert presentation._inches("50cm x 70cm") == (19.7, 27.6)
    # Inches, and a bare pair, are read as they always were.
    assert presentation._inches("24 x 18 in") == (24.0, 18.0)
    assert presentation._inches("24 × 18") == (24.0, 18.0)


def test_millimetres_are_still_a_misread_whatever_the_size():
    assert presentation._inches("610 x 457 mm") == (0.0, 0.0)
    assert presentation._inches("100 x 80 mm") == (0.0, 0.0)

"""What the model sends back is checked for SHAPE before it is used.

_extract_json returned whatever parsed. A bare number or a list parses, and
the next line asked it for keys -- an AttributeError, a 500, for photos the
seller had already paid to have looked at. A title that came back as a list
of candidates did the same one line later, in `.strip()`. And a reply the
API's own classifier stopped (stop_reason "refusal") carries no text at all,
which read as bad JSON and told the seller to try again -- and again, since
retrying does not change the photos.

Each of those is now its own answer: not-an-object is an answer that could
not be read; a field that is not text is an empty field; a refusal is said
as one, with what would change it.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

pytest.importorskip("anthropic")

from backend.models import Listing  # noqa: E402
from backend.services import claude_ai  # noqa: E402


@pytest.mark.parametrize("text", ["42", '"just a title"', "[1, 2, 3]", "null", "true"])
def test_an_answer_that_is_not_an_object_could_not_be_read(text):
    with pytest.raises(json.JSONDecodeError):
        claude_ai.extract_json(text)


def test_and_gets_the_same_sentence_as_any_unreadable_answer():
    try:
        claude_ai.extract_json("[1, 2, 3]")
    except json.JSONDecodeError as exc:
        code, message = claude_ai.ai_error_message(exc)
    assert code == 502 and "couldn't be read" in message


def test_a_field_that_is_not_text_is_an_empty_field_not_a_crash():
    listing = claude_ai._to_listing({
        "title": ["Levi's 501 Jeans 34x32", "Vintage Levi's 501"],
        "description": {"overview": "nested by mistake"},
        "brand": None,
        "condition": ["USED_GOOD"],
        "condition_description": 7,
        "subtitle": True,
        "item_specifics": "Brand: Levi's",
        "missing_info": "size",
        "price": 45,
    }, ["img_000.jpg"])
    assert listing.title == ""
    assert listing.description == ""
    assert listing.brand == ""
    assert listing.condition == "USED_EXCELLENT"
    assert listing.condition_description == "7"     # a scalar is spelt out
    assert listing.subtitle == ""                   # a bool is not a subtitle
    assert listing.item_specifics == []
    assert listing.missing_info == []
    assert listing.price == 44.99
    assert listing.images == ["img_000.jpg"]


def test_text_that_is_text_is_untouched():
    listing = claude_ai._to_listing({
        "title": "  Levi's 501 Jeans 34x32 ", "description": "Straight leg.",
        "brand": "Levi's", "condition": "used_good", "price": 45,
    }, ["img_000.jpg"])
    assert listing.title == "Levi's 501 Jeans 34x32"
    assert listing.brand == "Levi's"
    assert listing.condition == "USED_GOOD"


def _refusing_client():
    reply = types.SimpleNamespace(stop_reason="refusal", content=[], usage=None)
    return types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **kw: reply))


def test_a_refusal_is_said_as_one_not_as_try_again(monkeypatch):
    monkeypatch.setattr(claude_ai, "_client", _refusing_client)
    monkeypatch.setattr(claude_ai, "_image_block",
                        lambda p: {"type": "text", "text": f"[photo {p.name}]"})

    with pytest.raises(claude_ai.AIRefused) as caught:
        claude_ai.identify([Path("img_000.jpg")], ["img_000.jpg"])

    code, message = claude_ai.ai_error_message(caught.value)
    assert code == 422
    assert "declined" in message
    assert "couldn't be read" not in message and "try it again" not in message


def test_a_refine_can_be_refused_too(monkeypatch):
    monkeypatch.setattr(claude_ai, "_client", _refusing_client)
    with pytest.raises(claude_ai.AIRefused):
        claude_ai.refine(Listing(title="Levi's 501", images=["img_000.jpg"]),
                         "make the title shorter")


def test_the_bulk_card_knows_which_errors_have_a_sentence():
    assert claude_ai.is_ai_error(json.JSONDecodeError("x", "", 0))
    assert claude_ai.is_ai_error(claude_ai.AIRefused("no"))
    assert not claude_ai.is_ai_error(RuntimeError("the AI response was too long"))

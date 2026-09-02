"""The model's JSON is read the way a person would read it.

A bulk item died with 'Expecting ',' delimiter: line 1 column 3644 (char
3643)' -- the decoder's complaint, shown on the seller's card. The listing
was a pair of Levi's, and the description said they were 34" x 28": two inch
marks inside a JSON string, never escaped. The model is told not to do that
now, and the reader repairs it when it does anyway -- an inner quote is one
followed by prose, a closing quote is one followed by a comma, a colon, a
bracket or the end. A raw newline inside a string and a trailing comma are
repaired the same way. Anything else still fails, and the failure reaches
the seller as a sentence they can act on rather than a column number.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("anthropic")

from backend.services import claude_ai  # noqa: E402


def test_inch_marks_inside_a_string_are_read_as_inch_marks():
    text = ('{"title": "Levi\'s 550 Jeans 34x28", '
            '"description": "Measurements: waist 34" x inseam 28". Great wash.", '
            '"price": 45}')
    data = claude_ai.extract_json(text)
    assert data["description"] == 'Measurements: waist 34" x inseam 28". Great wash.'
    assert data["price"] == 45


def test_a_quoted_phrase_inside_a_string_is_kept():
    text = '{"description": "Marked "Made in Italy" on the sole", "n": 1}'
    data = claude_ai.extract_json(text)
    assert data["description"] == 'Marked "Made in Italy" on the sole'
    assert data["n"] == 1


def test_a_raw_newline_and_a_trailing_comma_are_repaired():
    text = '{"description": "Line one\nLine two",\n "tags": ["a", "b",],}'
    data = claude_ai.extract_json(text)
    assert data["description"] == "Line one\nLine two"
    assert data["tags"] == ["a", "b"]


def test_valid_json_and_escaped_quotes_pass_through_untouched():
    text = json.dumps({"d": 'He said "hi", then left', "path": "C:\\\\x"})
    assert claude_ai.extract_json(text) == json.loads(text)


def test_a_fenced_answer_is_still_read():
    assert claude_ai.extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_what_cannot_be_repaired_still_fails_as_before():
    with pytest.raises(json.JSONDecodeError):
        claude_ai.extract_json('{"a": [1, 2')


def test_the_seller_reads_a_sentence_not_a_column_number():
    try:
        json.loads('{"a": 1 "b": 2}')
    except json.JSONDecodeError as exc:
        code, message = claude_ai.ai_error_message(exc)
    assert code == 502
    assert "delimiter" not in message
    assert "try" in message.lower()

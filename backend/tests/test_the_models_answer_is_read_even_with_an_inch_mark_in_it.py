"""The model's JSON is read the way a person would read it.

A bulk item died with 'Expecting ',' delimiter: line 1 column 3644 (char
3643)' -- the decoder's complaint, shown on the seller's card. The listing
was a pair of Levi's, and the description said they were 34" x 28": two inch
marks inside a JSON string, never escaped. The model is told not to do that
now, and the reader repairs it when it does anyway.

The first repair said a closing quote is one followed by a comma, a colon, a
bracket or the end -- and measurements are written `Waist 34", Inseam 32"`,
with a comma after every inch mark. Each of those ended the string early, and
the same pair of Levi's failed on every retry. So a quote is now judged by
what VALID JSON needs next at that point: a key closes at a colon, a value in
an object at a `}` or a comma followed by the next key's quote, a value in an
array at a `]` or a comma followed by another value. `Levi\'s`, an escape
JSON does not have, is read as the apostrophe it means. A raw newline inside a
string and a trailing comma are repaired as before. Anything else still fails,
and the failure reaches the seller as a sentence they can act on rather than a
column number.
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


def test_an_inch_mark_before_a_comma_is_still_an_inch_mark():
    # The way measurements are actually written, and the case the first
    # repair got backwards: every `",` here looked like the end of a string.
    text = ('{"title": "Levi\'s 501 Original Fit Jeans Mens 34x32 Blue", '
            '"description": "Measurements: Waist 34", Inseam 32", Rise 11", '
            'Leg opening 8".", "price": 45}')
    data = claude_ai.extract_json(text)
    assert data["description"] == ('Measurements: Waist 34", Inseam 32", '
                                   'Rise 11", Leg opening 8".')
    assert data["price"] == 45


def test_inch_marks_and_commas_in_a_title_and_a_specific():
    text = ('{"title": "Levi\'s 501 W34" L32", Medium Wash", '
            '"item_specifics": [{"name": "Inseam", "value": "32""}, '
            '{"name": "Waist Size", "value": "34""}], "price": 45}')
    data = claude_ai.extract_json(text)
    assert data["title"] == 'Levi\'s 501 W34" L32", Medium Wash'
    assert data["item_specifics"] == [{"name": "Inseam", "value": '32"'},
                                      {"name": "Waist Size", "value": '34"'}]


def test_a_quote_before_a_colon_inside_a_value_is_kept():
    text = '{"description": "Tag reads "Size": 34 regular", "n": 1}'
    data = claude_ai.extract_json(text)
    assert data["description"] == 'Tag reads "Size": 34 regular'
    assert data["n"] == 1


def test_an_escaped_apostrophe_is_an_apostrophe():
    # Not valid JSON ('Invalid \\escape'), and written out of habit by models
    # -- most often in exactly the brand this kept failing on.
    text = '{"title": "Levi\\\'s 501 Original", "brand": "Levi\\\'s"}'
    data = claude_ai.extract_json(text)
    assert data == {"title": "Levi's 501 Original", "brand": "Levi's"}


def test_a_stray_backslash_is_kept_as_a_backslash():
    text = '{"size": "S\\M", "n": 1}'
    assert claude_ai.extract_json(text) == {"size": "S\\M", "n": 1}


def test_a_whole_jeans_draft_with_every_kind_of_slip_is_read():
    text = """```json
{
  "title": "Levi's 501 Original Fit Button Fly Jeans Mens W34" L32", Dark Wash",
  "subtitle": "",
  "brand": "Levi\\'s",
  "condition": "USED_EXCELLENT",
  "condition_description": "Light fading at the knees, no holes or stains.",
  "description": "Key Details:\nClassic Levi's 501 "Original Fit".\n\nMeasurements:\nWaist 34", Inseam 32", Rise 11", Leg opening 8".\n\nWhy You'll Love It: a staple.",
  "item_specifics": [
    {"name": "Inseam", "value": "32""},
    {"name": "Fit", "value": "Original Fit"},
  ],
  "missing_info": ["exact measurements"],
  "price": 45.0,
  "package_length_in": 12,
  "tags": [{"box": [0.1, 0.2, 0.3, 0.4], "label": "waistband "34x32""}],
  "confidence": "high",
  "identifiers": null
}
```"""
    data = claude_ai.extract_json(text)
    assert data["title"] == ('Levi\'s 501 Original Fit Button Fly Jeans Mens '
                             'W34" L32", Dark Wash')
    assert data["brand"] == "Levi's"
    assert 'Waist 34", Inseam 32", Rise 11", Leg opening 8".' in data["description"]
    assert '"Original Fit"' in data["description"]
    assert data["item_specifics"][0] == {"name": "Inseam", "value": '32"'}
    assert len(data["item_specifics"]) == 2
    assert data["tags"][0]["label"] == 'waistband "34x32"'
    assert data["price"] == 45.0 and data["identifiers"] is None


@pytest.mark.parametrize("doc", [
    {"title": "Levi's 501", "description": 'He said "hi", then left: ok',
     "price": 45.5, "n": None, "ok": True, "neg": -3},
    {"a": [1, "two", {"b": ["c", "d,e", 'f", "g']}], "path": "C:\\x\\y"},
    {"u": "caf\u00e9 \u2014 \U0001f456", "nl": "line\nline\ttab"},
    {"nested": {"deeper": {"deepest": [[], {}, [{"k": "v"}]]}}},
])
def test_the_repair_does_not_change_what_valid_json_means(doc):
    # Only ever run on text that already failed, but the state it keeps
    # (key or value, object or array) has to be right on valid input too, or
    # it is guessing on the broken kind.
    for text in (json.dumps(doc), json.dumps(doc, indent=2),
                 json.dumps(doc, ensure_ascii=False)):
        assert json.loads(claude_ai._repair_json(text)) == doc

"""A page that never closes its tags costs a read, not an afternoon.

The reference reader strips a page's furniture -- nav, footer, overlays --
subtree and all, and finds each subtree's end by counting nesting forward
from where it opened. It did that once PER ELEMENT. For an element that never
closes, that count runs to the end of the document and gives up, and the next
unclosed element did the same again: 8,000 unclosed <nav> tags in 40KB took
eleven seconds, and the growth is quadratic, so a 2MB page (under the fetch
cap) was hours of regex on a background thread holding the interpreter lock.
Any signed-in seller can point a reference link at a page like that.

What is pinned here is the time, and that the answer did not change: a
balanced subtree still goes whole, and an unclosed one still costs exactly
its own start tag.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("httpx")

from backend.services import reference_fetch as rf  # noqa: E402

_WORDS = "Fenton glass marks by decade, with the logo dating chart. " * 8


@pytest.mark.parametrize("tag", ["nav", "button", "article"])
def test_thousands_of_unclosed_tags_are_read_in_linear_time(tag):
    page = f"<p>{_WORDS}</p>" + f"<{tag}>" * 8000
    started = time.monotonic()
    text = rf.readable_text(page)
    reason = rf.unreadable_reason(page, text)
    took = time.monotonic() - started
    assert took < 2.0, f"{took:.1f}s for 8,000 unclosed <{tag}> tags"
    assert "Fenton glass marks" in text
    assert reason == ""


def test_a_balanced_chrome_subtree_still_goes_whole():
    page = ("<nav><nav>inner</nav>menu</nav><p>" + _WORDS + "</p>"
            "<footer>copyright <b>us</b></footer>")
    text = rf.readable_text(page)
    assert "Fenton glass marks" in text
    for chrome in ("inner", "menu", "copyright"):
        assert chrome not in text


def test_an_unclosed_chrome_tag_costs_only_its_own_start_tag():
    """The choice the reader already made for an unbalanced document: lose
    one start tag, never everything after it."""
    page = "<nav>" + "<p>" + _WORDS + "</p>"
    assert "Fenton glass marks" in rf.readable_text(page)


def test_nesting_is_counted_the_way_it_was():
    """An outer <nav> that never closes loses its start tag; the inner one it
    holds is closed, and still goes whole -- its close belongs to IT, not to
    the outer element."""
    page = ("<p>" + _WORDS + "</p><nav>kept words<nav>inner menu</nav>"
            "<p>after the menu</p>")
    text = rf.readable_text(page)
    assert "kept words" in text and "after the menu" in text
    assert "inner menu" not in text

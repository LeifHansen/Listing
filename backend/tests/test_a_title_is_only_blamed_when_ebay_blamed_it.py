"""The reported bug: eBay refuses a listing, the app blames the title.

A seller listed a vintage camera as "Miniature Subminiature Spy Camera Made
in Japan with Box" — 56 characters — and was told, three times and on a
rebuilt listing, that there was "a problem with the title" and to "shorten or
fix the title (max 80 characters)". There is nothing to shorten. There was no
way to find out what eBay had actually said, because the branch that produced
that sentence threw eBay's message away.

Two faults, and either one alone is enough to trap a seller in that loop:

  1. The branch matched "title" as a SUBSTRING. "entitled" contains it, and
     so a permission refused on the ACCOUNT — the kind that repeats on every
     listing, which is exactly what "I started over and it still rejects"
     looks like — arrived as a title problem. So did "subtitle", and so would
     "untitled".

  2. It answered every title rejection with the 80-character limit, whatever
     eBay had said. On a title that is not too long that is advice which
     cannot be followed, and it replaced the one sentence that would have
     explained the refusal.
"""
from __future__ import annotations

import json

import pytest

from backend import ebay_errors
from backend.models import TITLE_MAX_CHARS
from backend.services import ebay_trading


def issue(message: str, code: str = "", said: str = "") -> dict:
    return ebay_errors.explain(
        {"errorId": code, "message": message, "longMessage": said})


# --- "entitled" is not "title" ---------------------------------------------

@pytest.mark.parametrize("message", [
    "You are not entitled to use Business Policies.",
    "This user is not entitled to access this feature.",
    "Your account is not entitled to list items at this time.",
    "The seller isn't entitled to list in this category.",
])
def test_a_permission_on_the_account_is_never_the_title(message):
    """Fails against the substring match: every one of these came back
    target="title" with the 80-character advice attached."""
    it = issue(message)
    assert it["target"] == "account"
    # And the advice must not send them back to the title box — the trap was
    # never the target alone, it was being told to edit a field that is fine.
    assert "shorten" not in it["fix"].lower()


def test_the_business_policy_opt_in_is_named_as_the_fix():
    """The one entitlement the seller can act on inside this app."""
    it = issue("You are not entitled to use Business Policies.")
    assert "business polic" in it["title"].lower()
    assert "settings" in it["fix"].lower()


def test_an_account_refusal_does_not_send_the_editor_to_a_field():
    """`fixTargetFor` skips account targets, so nothing gets ringed red. That
    is the whole point of filing it under "account" rather than "title"."""
    issues = ebay_errors.from_trading_error(
        ebay_trading.TradingError("You are not entitled to list this item.",
                                  code="", detail=""))
    assert [i["target"] for i in issues] == ["account"]


# --- "subtitle" is not "title" ---------------------------------------------

def test_a_subtitle_rejection_says_subtitle():
    it = issue("The subtitle exceeds the maximum length allowed.")
    # It shares the Title card, so the target is right; the WORDS were not.
    assert it["target"] == "title"
    assert "subtitle" in it["title"].lower()
    assert str(TITLE_MAX_CHARS) not in it["fix"]      # not the title's limit
    assert "55" in it["fix"]                          # the subtitle's


# --- a real title rejection keeps eBay's words ------------------------------

def test_a_title_rejection_quotes_ebay_rather_than_guessing():
    """The 56-character title. eBay said what was wrong; the app must not
    replace it with a length limit the title is nowhere near."""
    # No "category" in the sentence: that word belongs to an earlier branch,
    # and eBay saying it means eBay is talking about the category.
    it = issue("Title contains characters that are not allowed.")
    assert it["target"] == "title"
    assert "not allowed" in it["title"].lower()
    assert "shorten the title" not in it["fix"].lower()


def test_the_length_advice_survives_where_it_is_true():
    it = issue("The title is too long. It must be 80 characters or less.")
    assert it["target"] == "title"
    assert f"{TITLE_MAX_CHARS} characters or fewer" in it["fix"]


def test_both_transports_agree_about_a_title():
    """Same guard the taxonomy suite already holds for codes: a fix applied
    to one door and not the other is how these get missed."""
    message = "The title is not valid."
    rest = ebay_errors.from_response(
        json.dumps({"errors": [{"errorId": 37, "message": message}]}))[0]
    trading = ebay_errors.from_trading_error(
        ebay_trading.TradingError(message, code="37", detail=""))[0]
    assert rest["target"] == trading["target"] == "title"
    assert rest["title"] == trading["title"]

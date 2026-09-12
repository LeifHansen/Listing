"""A refusal the app explains in full must not read as one it could not explain.

eBay locks parts of a live listing while a Best Offer is waiting on it, or an
auction has a bid, or it ends within twelve hours. `ebay_errors.explain`
recognises that sentence exactly and answers the seller completely: nothing is
missing, the edit is saved here, and it goes over automatically once the lock
clears.

It filed the issue under `target="generic"`, and correctly — target decides
what the SCREEN draws, and there is no field to open, so there must be no "Fix
this" button. But "generic" is also what an eBay sentence the classifier did
NOT recognise gets, and `refusal_key` put both in the error feed under the
same token. The daily triage reads that token and deliberately keeps
"generic": an unrecognised refusal is this tree's to fix. So on 2026-09-12 it
picked `revise (imported) refused over generic: eBay has this listing frozen
for now` as worth a pull request — against code doing exactly the right thing,
and it would have done so again every day a seller edited a listing with an
offer on it.

Two facts, one word. They are two words now: `target` still says "generic" so
the screen is unchanged, and `locked` is what the feed reads.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from backend.ebay_errors import explain
from backend.marketplaces.ebay_provider import refusal_key

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def triage():
    path = REPO_ROOT / ".github" / "scripts" / "triage_errors.py"
    spec = importlib.util.spec_from_file_location("triage_errors", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(message: str) -> dict:
    return {"fingerprint": "abcd1234abcd1234", "severity": "medium",
            "exc_type": "TradingError", "message": message, "count": 26,
            "module": "backend.marketplaces.ebay_provider", "func": "publish"}


# eBay's own wording when a Best Offer is holding the listing.
FROZEN = {"errorId": "21916612",
          "message": "The item specifics cannot be changed.",
          "longMessage": ("The item specifics cannot be changed because a "
                          "Best Offer is pending on this listing.")}
UNRECOGNISED = {"errorId": "99999",
                "message": "Something went wrong with your listing."}


def test_a_frozen_listing_still_draws_no_fix_button():
    """The screen half is unchanged: no field, so no target to open."""
    issue = explain(FROZEN)
    assert issue["target"] == "generic"
    assert not issue.get("fields")


def test_a_frozen_listing_still_tells_the_seller_what_happened():
    issue = explain(FROZEN)
    assert "frozen" in issue["title"].lower()
    assert "saved" in issue["fix"].lower(), (
        "the whole reason this is not a bug is that the seller is told their "
        "edit survives and goes over on its own")


def test_the_feed_gets_its_own_token_for_a_frozen_listing():
    assert refusal_key([explain(FROZEN)]) == "locked"


def test_an_unrecognised_refusal_is_still_generic():
    """The other side of the split, and the one that must keep reporting.

    A sentence the classifier could not place is this tree's gap, and the
    triage has to go on proposing a fix for it.
    """
    assert refusal_key([explain(UNRECOGNISED)]) == "generic"


def test_the_triage_leaves_a_frozen_listing_alone(triage):
    message = ("revise (imported) refused over locked: eBay has this listing "
               "frozen for now | session=ebay-158269755022 item=1582697550")
    why = triage.why_not(_row(message), {}, str(REPO_ROOT))
    assert why, "this is the row that was proposed for a fix every day"
    assert "frozen" in why


def test_the_triage_still_takes_an_unrecognised_one(triage):
    message = ("trading publish refused over generic: eBay rejected the "
               "listing | session=<id>")
    assert triage.why_not(_row(message), {}, str(REPO_ROOT)) is None

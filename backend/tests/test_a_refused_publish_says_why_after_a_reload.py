"""A publish eBay refused still says why once the page has been reloaded.

A seller published a draft, watched it stay in Drafts, and reported the app
as broken: "I published it and it's not clearing." What had actually happened
is that the publish was refused -- which is the seller's to fix -- and the app
said so exactly twice: in a toast that is gone in seconds, and in a React
state map keyed by listing id that is gone on reload. What was left on screen
was a card indistinguishable from a draft nobody had ever tried to publish.

So the reason goes on the record now (Listing.publish_error) and the card
reads it back. The rules are the ones the wording depends on:

  * only a REFUSAL is recorded. A publish whose answer never came back is not
    one -- the listing may well be live, and calling it refused is what sends
    someone to list the same item twice -- and neither is a dry run or a draft
    save.
  * anything going live CLEARS it, including a fan-out where one marketplace
    refused and another did not: the listing is live, and that refusal is
    per-marketplace state rather than a verdict on the draft.
  * the sentence is the one the browser would have picked, so the card reads
    the same before and after a reload.
"""
from __future__ import annotations

import pytest

# backend.main pulls in the whole stack (services.claude_ai imports the
# Anthropic SDK, the photo pass imports Pillow), and the lint gate installs
# neither -- the same three guards every other test that imports main carries.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main  # noqa: E402
from backend.marketplaces.base import PublishOutcome  # noqa: E402
from backend.marketplaces.state import SERVER_OWNED_FIELDS  # noqa: E402
from backend.models import Listing  # noqa: E402


@pytest.fixture()
def recorded(monkeypatch):
    """What _record_publish_verdict wrote, or None when it wrote nothing."""
    seen: dict = {}

    def mutate_listing_data(listing_id, fn, status=None, user_id=None):
        data = fn(dict(seen.get("before") or {}))
        seen["id"] = listing_id
        seen["data"] = data
        return data

    monkeypatch.setattr(main.db, "mutate_listing_data", mutate_listing_data)
    return seen


def _refused(message="", issues=None):
    return PublishOutcome(ok=False, message=message, issues=issues or [])


def _live():
    return PublishOutcome(ok=True, status="published", listing_id="1")


# ------------------------------------------------- which sentence is kept

def test_an_issue_the_app_worked_out_beats_ebays_own_message():
    """eBay's catch-all for an account-level hold blames the title. The app
    already resolves that into a real issue; the record must keep THAT."""
    out = _refused(
        message="The item cannot be listed or modified. The title and/or "
                "description may contain improper words.",
        issues=[{"target": "account", "level": "error",
                 "title": "eBay is holding new listings on this account"}])
    assert main._refusal_sentence(out) == (
        "eBay is holding new listings on this account")


def test_a_placeholder_never_hides_a_real_diagnosis():
    """"The publish stopped and nobody said why" is an account-target issue
    like any other, and it used to win outright."""
    out = _refused(issues=[
        {"target": "account", "level": "error", "placeholder": True,
         "title": "eBay is blocking new listings on this account"},
        {"target": "package_weight_oz", "level": "error",
         "title": "Add a shipping weight"}])
    assert main._refusal_sentence(out) == "Add a shipping weight"


def test_a_warning_is_not_the_reason_a_publish_was_refused():
    out = _refused(message="Not quite ready.",
                   issues=[{"target": "title", "level": "warn",
                            "title": "Title could be longer"}])
    assert main._refusal_sentence(out) == "Not quite ready."


def test_with_nothing_else_to_say_the_marketplaces_own_words_are_kept():
    assert main._refusal_sentence(_refused(message="eBay said no.")) == "eBay said no."
    assert main._refusal_sentence(_refused()) == ""


# ------------------------------------------------------ what gets recorded

def test_a_refusal_is_written_to_the_record(recorded):
    main._record_publish_verdict("s1", "u1", [_refused(message="Add a weight.")],
                                 "live")
    assert recorded["id"] == "s1"
    assert recorded["data"]["publish_error"] == "Add a weight."


def test_going_live_clears_the_last_refusal(recorded):
    recorded["before"] = {"publish_error": "Add a weight."}
    main._record_publish_verdict("s1", "u1", [_live()], "live")
    assert recorded["data"]["publish_error"] == ""


def test_one_marketplace_refusing_does_not_hold_a_live_listing_in_drafts(recorded):
    """The listing IS live. Etsy's refusal is per-marketplace state, and the
    card must not be kept in Drafts by it."""
    main._record_publish_verdict("s1", "u1", [_live(), _refused(message="Etsy no.")],
                                 "live")
    assert recorded["data"]["publish_error"] == ""


def test_two_refusals_are_both_named_once(recorded):
    main._record_publish_verdict(
        "s1", "u1", [_refused(message="eBay no."), _refused(message="Etsy no.")],
        "live")
    assert recorded["data"]["publish_error"] == "eBay no.; Etsy no."
    recorded.clear()
    main._record_publish_verdict(
        "s1", "u1", [_refused(message="Same."), _refused(message="Same.")], "live")
    assert recorded["data"]["publish_error"] == "Same."


def test_the_sentence_is_bounded(recorded):
    main._record_publish_verdict("s1", "u1", [_refused(message="x" * 900)], "live")
    assert len(recorded["data"]["publish_error"]) == 300


# --------------------------------------------- what is deliberately left alone

def test_an_answer_that_never_came_back_is_not_a_refusal(recorded):
    """It may well be live. Painting it refused is what sends a seller to
    publish the same item a second time."""
    main._record_publish_verdict(
        "s1", "u1", [PublishOutcome(ok=False, outcome_unknown=True,
                                    message="No answer.")], "live")
    assert "data" not in recorded


def test_a_dry_run_leaves_the_last_verdict_alone(recorded):
    main._record_publish_verdict(
        "s1", "u1", [PublishOutcome(ok=True, dry_run=True, status="dry_run")], "live")
    assert "data" not in recorded


def test_saving_a_draft_is_not_a_publish_attempt(recorded):
    main._record_publish_verdict("s1", "u1", [_refused(message="no")], "draft")
    assert "data" not in recorded


def test_a_refusal_with_nothing_to_say_writes_nothing(recorded):
    main._record_publish_verdict("s1", "u1", [_refused()], "live")
    assert "data" not in recorded


def test_bookkeeping_never_fails_a_publish(monkeypatch):
    """The publish already happened. It must not be reported as failed
    because the note about it could not be written."""
    def boom(*a, **k):
        raise RuntimeError("database is down")
    monkeypatch.setattr(main.db, "mutate_listing_data", boom)
    main._record_publish_verdict("s1", "u1", [_refused(message="no")], "live")


# ------------------------------------------------------------- who owns it

def test_the_record_owns_the_reason_not_the_browser():
    """A client only ever echoes it back; an echo that invented one would put
    a refusal nobody gave on the card."""
    assert "publish_error" in SERVER_OWNED_FIELDS
    listing = Listing(title="x", publish_error="I made this up")
    from backend.marketplaces import state
    state.restore_server_fields(listing, {"publish_error": "eBay said no."})
    assert listing.publish_error == "eBay said no."


def test_a_listing_starts_with_no_verdict_on_it():
    assert Listing(title="x").publish_error == ""

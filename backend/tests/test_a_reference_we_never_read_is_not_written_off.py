"""Five different failures used to reach the seller as the same two sentences.

    "HTTP 429"
    "that page had nothing usable on it — a login wall, a paywall, or a page
     about something else"

The second also turned the reference OFF. Between them they covered: a museum
rate limiting us, a site refusing an unknown client, a consent prompt standing
in front of the page, our own summariser being unavailable, and the one case
the sentence actually describes -- a page we read and found nothing in.

Only the last of those is a finding about the seller's page. The other four
mean "we have not seen it", and writing a link off on the strength of not
having looked is the bug this file is about. So what is pinned here is not the
wording but the three decisions that follow from it:

    is the reference left ON?     (has anything been learned that says it is bad?)
    is it read AGAIN?             (can time fix this?)
    does the message say what HAPPENED?   (or does it guess?)
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")
pytest.importorskip("fastapi")
# backend.main pulls in the whole app, and the lint job installs a deliberately
# minimal dependency profile. The 109 other test files that import it guard the
# same way rather than turning that job red.
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main  # noqa: E402
from backend.services import reference_fetch as rf  # noqa: E402


class FakeStore:
    """The one row under test, and every write made to it."""

    def __init__(self, **over):
        self.row = {"id": "k1", "expert": "art", "url": "https://ref.test/p",
                    "note": "date Fenton glass", "distillate": "",
                    "enabled": True, "fetch_error": "", **over}
        self.writes = []

    def expert_knowledge_get(self, record_id):
        return dict(self.row)

    def expert_knowledge_update(self, record_id, **fields):
        self.writes.append(fields)
        self.row.update(fields)
        return True

    @property
    def last(self):
        return self.writes[-1] if self.writes else {}

    @property
    def error(self):
        return self.last.get("fetch_error", "")


@pytest.fixture()
def bench(monkeypatch):
    """A reference row, a stood-in fetcher and summariser, and a note of every
    retry that was scheduled instead of actually sleeping for ten minutes."""
    store = FakeStore()
    scheduled = []
    monkeypatch.setattr(main, "db", store)
    monkeypatch.setattr(main, "run_in_background",
                        lambda fn, *a, **k: scheduled.append((fn, a)))
    monkeypatch.setattr(main.claude_ai, "distill_reference",
                        lambda *a, **k: {"summary": "Fenton marked its glass "
                                                    "from 1970.",
                                         "usable": True, "reason": ""})
    store.scheduled = scheduled
    return store


def _fetches(monkeypatch, page="", error=None):
    def _fetch(url):
        if error is not None:
            raise error
        return page
    monkeypatch.setattr(main.reference_fetch, "fetch", _fetch)


# --- busy is not broken -----------------------------------------------------

def test_a_rate_limited_page_is_read_again_and_stays_on(bench, monkeypatch):
    """The Met's 429. It used to land as "HTTP 429" on a reference that had
    just been switched off, which is a claim about a page nobody looked at."""
    _fetches(monkeypatch, error=rf.TemporaryFailure(
        "the site is busy or limiting how often it will answer (HTTP 429)"))

    main._distill_reference_row("k1")

    assert "enabled" not in bench.last, "nothing was learned that turns it off"
    assert "429" in bench.error and "busy" in bench.error
    assert "trying again" in bench.error
    assert bench.scheduled, "a moment that time can fix is tried again"


def test_the_retries_run_out_and_the_seller_is_told_they_have(bench, monkeypatch):
    """Bounded: a site still rate limiting us ten minutes later is not going
    to be talked round by a third attempt, and the seller has a button."""
    _fetches(monkeypatch, error=rf.TemporaryFailure("the site is busy (HTTP 429)"))

    main._distill_reference_row("k1", attempt=len(main.REFERENCE_RETRY_DELAYS))

    assert bench.scheduled == [], "the retries are bounded"
    assert "Try again" in bench.error
    assert "enabled" not in bench.last


def test_the_retry_schedule_is_short_and_finite():
    assert 0 < len(main.REFERENCE_RETRY_DELAYS) <= 3
    assert all(30 <= d <= 3600 for d in main.REFERENCE_RETRY_DELAYS)
    assert list(main.REFERENCE_RETRY_DELAYS) == sorted(main.REFERENCE_RETRY_DELAYS)


def test_a_site_that_refuses_this_reader_makes_no_claim_about_the_page(
        bench, monkeypatch):
    """A 403 tells us about the site's front door, not about what is behind
    it. Recorded, so the seller knows; not retried, because ninety seconds
    will not change it; and not switched off, because we have not looked."""
    _fetches(monkeypatch, error=rf.Blocked(
        "the site refused to serve the page to us (HTTP 403)"))

    main._distill_reference_row("k1")

    assert "403" in bench.error
    assert "enabled" not in bench.last
    assert bench.scheduled == []


def test_a_link_that_is_actually_wrong_is_still_switched_off(bench, monkeypatch):
    """The behaviour that was right all along. A link that can never be read
    -- not https, unresolvable, a PDF -- is the seller's to fix, and a row
    failing forever in the settings screen is worse than one turned off."""
    _fetches(monkeypatch, error=rf.UnsafeURL("a reference link must be https"))

    main._distill_reference_row("k1")

    assert bench.last.get("enabled") is False
    assert "https" in bench.error


# --- a wall is not a paywall ------------------------------------------------

CONSENT_WALL = """<html><head><title>Artist index</title></head><body>
<div id="onetrust-consent-sdk"><h2>We use cookies</h2>
<button>Accept all cookies</button></div>
<div id="root"></div></body></html>"""


def test_a_consent_prompt_is_named_and_never_spends_a_model_call(
        bench, monkeypatch):
    """Asking the summariser to find reference material in a consent banner
    spends a call to be told there is none -- and then that shrug got written
    down as a paywall on a page nobody had seen."""
    _fetches(monkeypatch, page=CONSENT_WALL)
    asked = []
    monkeypatch.setattr(main.claude_ai, "distill_reference",
                        lambda *a, **k: asked.append(a) or None)

    main._distill_reference_row("k1")

    assert asked == [], "there was nothing on the page to summarise"
    assert "privacy prompt" in bench.error
    assert "paywall" not in bench.error
    assert "enabled" not in bench.last
    assert bench.scheduled, "worth another look"


def test_the_page_under_the_popup_is_distilled_normally(bench, monkeypatch):
    """The seller's actual complaint: a page that works, behind a pop-up."""
    _fetches(monkeypatch, page="""<html><head><title>Fenton marks</title></head>
    <body><div class="cookie-consent-banner">Accept all cookies</div>
    <main><p>The oval Fenton logo was moulded into the glass from 1970, and a
    number added inside the logo marks the decade: 8 for the 1980s and 9 for
    the 1990s. Pieces made before 1970 carry a paper label only, so an
    unmarked piece is not by itself evidence of a reproduction.</p>
    </main></body></html>""")

    main._distill_reference_row("k1")

    assert bench.last.get("distillate")
    assert bench.last.get("fetch_error") == ""
    assert "enabled" not in bench.last


def test_our_own_summariser_being_down_is_not_a_verdict_on_their_page(
        bench, monkeypatch):
    """distill_reference returns None when it did not RUN -- no key, an
    overloaded model, a reply that would not parse. That is ours to own."""
    _fetches(monkeypatch, page="<html><body><main><p>" + "Hallmark facts. " * 40
                               + "</p></main></body></html>")
    monkeypatch.setattr(main.claude_ai, "distill_reference", lambda *a, **k: None)

    main._distill_reference_row("k1")

    assert "summarise" in bench.error
    assert "paywall" not in bench.error and "login" not in bench.error
    assert "enabled" not in bench.last
    assert bench.scheduled, "it is worth trying once the model is back"


# --- and the one case that IS a verdict -------------------------------------

def test_a_page_we_read_and_found_nothing_in_is_switched_off_with_its_reason(
        bench, monkeypatch):
    """The sentence was not wrong to exist -- it was wrong to be the only one.
    Here we did read the page, so the reference goes off, and what the seller
    is told is what the model actually found rather than a guess at a cause."""
    _fetches(monkeypatch, page="<html><body><main><p>" + "A. Aabye. B. Aach. " * 40
                               + "</p></main></body></html>")
    monkeypatch.setattr(main.claude_ai, "distill_reference", lambda *a, **k: {
        "summary": "", "usable": False,
        "reason": "an index of artist names, with nothing about any one of them"})

    main._distill_reference_row("k1")

    assert bench.last.get("enabled") is False
    assert "index of artist names" in bench.error
    assert "we read the page" in bench.error
    assert bench.scheduled == [], "nothing here for time to fix"


def test_a_verdict_without_a_reason_does_not_invent_one(bench, monkeypatch):
    _fetches(monkeypatch, page="<html><body><main><p>" + "Nothing much. " * 40
                               + "</p></main></body></html>")
    monkeypatch.setattr(main.claude_ai, "distill_reference", lambda *a, **k: {
        "summary": "", "usable": False, "reason": ""})

    main._distill_reference_row("k1")

    assert bench.last.get("enabled") is False
    assert "no reference material" in bench.error
    for guess in ("paywall", "login wall", "a page about something else"):
        assert guess not in bench.error


def test_a_good_read_clears_whatever_the_last_one_said(bench, monkeypatch):
    bench.row["fetch_error"] = "the site is busy (HTTP 429)"
    _fetches(monkeypatch, page="<html><body><main><p>" + "Hallmark facts. " * 40
                               + "</p></main></body></html>")

    main._distill_reference_row("k1")

    assert bench.last["fetch_error"] == ""
    assert bench.last["distillate"]
    assert bench.last["distilled_at"]

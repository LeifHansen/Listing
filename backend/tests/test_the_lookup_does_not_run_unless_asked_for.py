"""The web-search research pass is off unless the deployment turns it on —
on the backend whose lookup is slow enough for that to be the right default.

The morning it shipped defaulting to "auto" (#233), every identify that
matched its very wide gate — "edition", "rare", "book", "record", "glass",
most of a thrift store — waited on a second vision call with up to six web
searches inside it, in the request, before the draft came back. Identify went
from seconds to a minute or more, bulk batches multiplied that by the item
count, and the seller reported the app as unusable. The pass is still worth
having; it is not worth having in the request path by default.

That cost is Claude's server-tool loop: sequential searches, pausing and
resuming the turn. Gemini's grounded lookup is one call that searches
server-side, which is the shape #233's note said would earn "auto" back — so
the resolved default follows the backend now, and both halves are pinned
here. A future edit that flips Claude's default has to change a test that
says why not.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main
from backend.models import Listing


def test_the_default_is_off_on_the_backend_that_cannot_afford_it(monkeypatch):
    monkeypatch.setattr(main, "RESEARCH_PASS", "")
    monkeypatch.setattr(main.config, "identify_provider", lambda: "anthropic")
    assert main.research_pass() == "off"


def test_the_grounded_lookup_is_on_by_default(monkeypatch):
    # One call, searching server-side, is not the minute-per-item the note
    # above is about — and a price with a source is the reason to identify on
    # Google at all. Off by default it would never run.
    monkeypatch.setattr(main, "RESEARCH_PASS", "")
    monkeypatch.setattr(main.config, "identify_provider", lambda: "google")
    assert main.research_pass() == "auto"


def test_an_explicit_setting_beats_either_default(monkeypatch):
    monkeypatch.setattr(main, "RESEARCH_PASS", "off")
    monkeypatch.setattr(main.config, "identify_provider", lambda: "google")
    assert main.research_pass() == "off"
    monkeypatch.setattr(main, "RESEARCH_PASS", "always")
    monkeypatch.setattr(main.config, "identify_provider", lambda: "anthropic")
    assert main.research_pass() == "always"


def test_a_draft_that_would_have_been_looked_up_is_not(monkeypatch):
    # The listing below trips every branch of the gate at once (a hedge, a
    # signal word, a landmine category). With the pass off the gate never
    # runs — and neither does the call behind it.
    calls = []
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main.claude_ai, "research_item",
                        lambda *a, **k: calls.append(a) or {})
    monkeypatch.setattr(main, "RESEARCH_PASS", "off")
    listing = Listing(title="Fanch Ledan style lithograph, signed, numbered",
                      brand="", price=85, category_suggestion="Art > Prints")
    assert main._research_reason(listing) == ""
    assert main._research_draft(listing, [], "", "low") is None
    assert calls == []
    assert listing.title.startswith("Fanch Ledan style")
    assert listing.price == 85


def test_asking_for_it_still_works(monkeypatch):
    # The knob is a default, not a removal: RESEARCH_PASS=auto brings the gate
    # back exactly as #233 shipped it.
    monkeypatch.setattr(main, "RESEARCH_PASS", "auto")
    listing = Listing(title="Fanch Ledan style lithograph", brand="", price=85)
    assert main._research_reason(listing) != ""

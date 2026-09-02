"""The web-search research pass is off unless the deployment turns it on.

The morning it shipped defaulting to "auto" (#233), every identify that
matched its very wide gate — "edition", "rare", "book", "record", "glass",
most of a thrift store — waited on a second vision call with up to six web
searches inside it, in the request, before the draft came back. Identify went
from seconds to a minute or more, bulk batches multiplied that by the item
count, and the seller reported the app as unusable. The pass is still worth
having; it is not worth having in the request path by default.

This pins the default at the one place it is read, so a future edit that
flips it back to "auto" has to change a test that says why not.
"""
from __future__ import annotations

from backend import main
from backend.models import Listing


def test_the_default_is_off():
    assert main.RESEARCH_PASS == "off"


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

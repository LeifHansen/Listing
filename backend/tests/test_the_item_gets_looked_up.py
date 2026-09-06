"""The app looks an item UP instead of remembering it.

Everything the identifier did before this was a memory test over photos, and
memory failed twice in the direction that costs the seller the item:

  * a hand-signed Fanch Ledan lithograph — a named work — was drafted as a
    "Fanch Ledan style lithograph" at $85; and
  * a genuine Beatles "Yesterday and Today" butcher cover was called a
    "replica", sold for $22, and was worth over $7,000.

Both are one failure: the model half-recognized something, hedged, and the
hedge became the listing. Nothing checked it against the world.

So a draft that shows any sign of turning on an identification now goes
through claude_ai.research_item — Claude with the server-side web search tool
over the same photos — and what comes back is merged under rules that can only
ever help the seller: name the work, fill a blank, raise a price, raise a
question. Never downgrade the item, never lower the price, never overwrite a
title that is already specific.

These tests stub the lookup itself. What they pin is the gate (which drafts
get looked up) and the merge (what a finding is allowed to do to a draft) —
the two halves that decide whether a $7,000 record goes out at $22.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main
from backend.models import ItemSpecific, Listing


def _listing(**over) -> Listing:
    base = dict(title="Beatles Yesterday and Today LP replica butcher cover",
                description="A record sleeve.", price=22.0, quantity=1,
                condition="USED_GOOD", images=["a.jpg"],
                category_suggestion="Music > Records", category_id="176985")
    base.update(over)
    return Listing(**base)


@pytest.fixture()
def lookup(monkeypatch, tmp_path):
    """claude_ai.research_item, stubbed. Returns (install, calls, photo)."""
    calls: list[dict] = []
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"not really a jpeg")

    def _install(answer):
        def research_item(paths, listing, observations=""):
            calls.append({"paths": list(paths), "title": listing.title,
                          "observations": observations})
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(main.claude_ai, "research_item", research_item)
        return calls

    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "RESEARCH_PASS", "auto")
    _install.photo = photo
    _install.calls = calls
    return _install


BUTCHER = {
    "identified": "Beatles Yesterday and Today, first-state stereo butcher cover",
    "maker": "The Beatles",
    "work": "Yesterday and Today",
    "variant": "First-state stereo butcher cover, never pasted over",
    "title": "The Beatles Yesterday and Today Butcher Cover Stereo First State LP",
    "value_low": 4000.0, "value_high": 12000.0,
    "value_basis": "sold auction records",
    "high_value_variant": "",
    "verify": ["Check the cover for paste-over residue under raking light"],
    "sources": ["https://example.test/butcher-covers"],
    "confidence": "high",
}


# --- which drafts get looked up ---------------------------------------------

@pytest.mark.parametrize("draft, why", [
    (dict(title="Fanch Ledan style lithograph"), "a hedge in the title"),
    (dict(title="Beatles LP replica butcher cover"), "a hedge in the title"),
    (dict(title="Chagall hand signed lithograph 84/250"), "a signature"),
    (dict(title="Plain wooden bowl", category_suggestion="Art > Prints"),
     "a category where a variant changes everything"),
    (dict(title="Plain shirt", description="Signed on the label by the maker"),
     "a signature in the description"),
])
def test_a_draft_whose_value_turns_on_an_id_is_looked_up(lookup, draft, why):
    calls = lookup(BUTCHER)
    assert main._research_draft(_listing(**draft), [lookup.photo]), why
    assert len(calls) == 1


def test_an_ordinary_item_is_not_looked_up(lookup):
    """The lookup costs a web-searching model call per item. A t-shirt whose
    price does not turn on an attribution does not need one."""
    calls = lookup(BUTCHER)
    plain = _listing(title="Nike Dri-FIT training t-shirt mens large black",
                     description="A black training tee.",
                     category_suggestion="Clothing > Men > Shirts")

    assert main._research_draft(plain, [lookup.photo]) is None
    assert calls == []


def test_the_gate_is_not_the_price(lookup):
    """The whole problem is that the valuable items came back CHEAP. A gate on
    "is this draft expensive" would have skipped both losses."""
    calls = lookup(BUTCHER)
    cheap = _listing(title="Signed lithograph, artist unknown", price=5.0)

    assert main._research_draft(cheap, [lookup.photo])
    assert len(calls) == 1


def test_the_pass_can_be_forced_on_or_off(lookup, monkeypatch):
    calls = lookup(BUTCHER)
    plain = _listing(title="Nike training t-shirt", description="A tee.",
                     category_suggestion="Clothing > Men > Shirts")

    monkeypatch.setattr(main, "RESEARCH_PASS", "always")
    assert main._research_draft(plain, [lookup.photo])
    monkeypatch.setattr(main, "RESEARCH_PASS", "off")
    assert main._research_draft(_listing(), [lookup.photo]) is None
    assert len(calls) == 1          # the "always" one only


# --- what a finding is allowed to do ----------------------------------------

def test_a_hedged_title_is_replaced_with_what_it_actually_is(lookup):
    lookup(BUTCHER)
    listing = _listing()

    assert main._research_draft(listing, [lookup.photo])

    assert listing.title == BUTCHER["title"]
    assert "replica" not in listing.title.lower()


def test_a_specific_title_is_never_overwritten_only_suggested(lookup):
    """Research is a second opinion, not an authority: a title that already
    names the item keeps its words, and the finding becomes a note."""
    lookup(BUTCHER)
    listing = _listing(title="The Beatles Yesterday and Today stereo LP 1966")

    main._research_draft(listing, [lookup.photo])

    assert listing.title == "The Beatles Yesterday and Today stereo LP 1966"
    assert any("suggests this title" in n for n in listing.missing_info)


def test_the_price_is_raised_to_the_bottom_of_what_it_is_worth(lookup):
    """$22 against sold records of $4,000-$12,000. The floor is the BOTTOM of
    the range — a floor, not a valuation — and the range is said out loud."""
    lookup(BUTCHER)
    listing = _listing(price=22.0)

    main._research_draft(listing, [lookup.photo])

    # The floor as this app prices it: the nearest .99 under it, not a whole
    # $4,000 (money.charm_price). The RANGE in the note is what was researched
    # and is quoted as researched.
    assert listing.price == 3999.99
    note = " ".join(listing.missing_info)
    assert "4,000" in note and "12,000" in note and "22" in note


def test_a_price_already_above_the_floor_is_left_alone(lookup):
    lookup(BUTCHER)
    listing = _listing(price=9000.0)

    main._research_draft(listing, [lookup.photo])

    assert listing.price == 9000.0
    assert any("For reference" in n for n in listing.missing_info)


def test_research_never_lowers_a_price(lookup):
    """Not even when the lookup thinks the item is worth less. Overpriced is
    recoverable; underpriced is a sale at the wrong number."""
    lookup({**BUTCHER, "value_low": 30.0, "value_high": 60.0})
    listing = _listing(price=500.0)

    main._research_draft(listing, [lookup.photo])

    assert listing.price == 500.0


def test_the_expensive_variant_is_flagged_even_at_low_confidence(lookup):
    """The field this whole pass exists for: what is the version of this item
    worth 100x, and could THIS be it."""
    lookup({
        "identified": "Beatles Yesterday and Today LP, trunk cover",
        "maker": "The Beatles", "work": "Yesterday and Today",
        "title": "", "value_low": 25.0, "value_high": 60.0,
        "value_basis": "sold listings",
        "high_value_variant": ("A pasted-over 'butcher cover' hides underneath "
                               "some trunk covers and is worth $2,000-$7,000 — "
                               "hold it to a strong light for the shadow of the "
                               "butcher image behind the trunk photo."),
        "verify": ["Look for a third-state paste-over"],
        "sources": ["https://example.test/butcher"],
        "confidence": "low",
    })
    listing = _listing(price=22.0)

    main._research_draft(listing, [lookup.photo])

    flag = " ".join(listing.missing_info)
    assert "CHECK BEFORE LISTING" in flag
    assert "7,000" in flag or "7000" in flag
    assert "Verify: Look for a third-state paste-over" in listing.missing_info


def test_an_empty_brand_is_filled_but_a_set_one_is_kept(lookup):
    lookup(BUTCHER)
    blank, held = _listing(brand=""), _listing(brand="Capitol Records")

    main._research_draft(blank, [lookup.photo])
    main._research_draft(held, [lookup.photo])

    assert blank.brand == "The Beatles"
    assert held.brand == "Capitol Records"


def test_a_low_confidence_finding_does_not_rewrite_the_listing(lookup):
    """A guess with a source list is still a guess. It may raise a question;
    it may not rename the item."""
    lookup({**BUTCHER, "confidence": "low", "value_low": None,
            "value_high": None, "high_value_variant": ""})
    listing = _listing()
    before = listing.title

    main._research_draft(listing, [lookup.photo])

    assert listing.title == before
    assert listing.brand == ""
    assert any("wasn't sure either" in n for n in listing.missing_info)


def test_what_it_looked_at_is_cited(lookup):
    lookup(BUTCHER)
    listing = _listing()

    main._research_draft(listing, [lookup.photo])

    assert any("example.test" in n for n in listing.missing_info)


# --- it can never take the draft down with it -------------------------------

@pytest.mark.parametrize("answer", [
    None,                                   # the pass ran and found nothing
    {},                                     # ...or came back empty
    RuntimeError("web search is down"),     # ...or fell over
])
def test_a_lookup_that_fails_leaves_the_draft_exactly_as_drafted(lookup, answer):
    lookup(answer)
    listing = _listing(price=22.0)
    before = (listing.title, listing.price, list(listing.missing_info))

    assert main._research_draft(listing, [lookup.photo]) is None
    assert (listing.title, listing.price, listing.missing_info) == before


def test_nothing_runs_without_an_ai_key(lookup, monkeypatch):
    calls = lookup(BUTCHER)
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: False)

    assert main._research_draft(_listing(), [lookup.photo]) is None
    assert calls == []


def test_the_specifics_are_read_for_signals_too(lookup):
    calls = lookup(BUTCHER)
    listing = _listing(
        title="Framed picture", description="A framed picture.",
        category_suggestion="Home > Decor",
        item_specifics=[ItemSpecific(name="Type", value="Limited Edition Print")])

    assert main._research_draft(listing, [lookup.photo])
    assert len(calls) == 1

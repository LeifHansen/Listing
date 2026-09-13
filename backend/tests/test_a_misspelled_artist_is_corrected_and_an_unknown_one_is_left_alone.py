"""Fix "Salvadore Dali". Never touch the artist nobody has heard of.

An attribution is the most valuable thing the art expert produces and the most
dangerous. "Marc Chagall" on a lithograph is most of its price; the same words
on something Chagall never touched is a return, a case, and a seller who
believed us. The lookup already refuses to name anyone it cannot source -- a
guessed attribution is worse than a blank -- but there is a failure it cannot
see from inside one request: a name that is ALMOST right. "Salvadore Dali".
"Piet Mondrain". "Thomas Kincade". Each is one or two letters from a real
artist, each reads as confident, and each is a title no collector will ever
search for, because the search box does not do near-misses.

So the expert carries a roster of artists whose work actually turns over, and
this file is about the four things it may do with a name -- and the one it may
never do.

THE ONE IT MAY NEVER DO IS REJECT. A thousand artists is a rounding error
against the long tail: most of what a reseller finds is a regional painter, a
listed local artist, a signature the lookup read perfectly well off a margin.
A roster that demoted every name it did not hold would be a censor, and it
would break the "NEVER resolve a doubt downward" rule the whole art path is
built on -- using the thing meant to strengthen it. The roster may fix a
spelling and may raise confidence. Absent from it is not a reason to drop a
name.

Both halves are in one file on purpose. The second is what stops the first
becoming the bug.
"""
from __future__ import annotations

import pytest

from backend.services.experts.art import roster


@pytest.fixture(autouse=True)
def _fresh():
    roster.reload()
    yield
    roster.reload()


# --- the roster is actually there -------------------------------------------

def test_the_roster_loads_and_holds_artists_that_trade():
    assert roster.size() > 50
    for who in ("Pablo Picasso", "Salvador Dalí", "Marc Chagall",
                "Katsushika Hokusai", "Andy Warhol"):
        assert roster.lookup(who), f"{who} is not on the roster"


def test_it_holds_the_resale_print_market_and_not_only_the_auction_room():
    """What a reseller actually finds in a thrift store is not the Artnet top
    1000. A roster of blue-chip names alone would miss most real listings."""
    for who in ("LeRoy Neiman", "Thomas Kinkade", "Bev Doolittle",
                "Charles Wysocki", "P. Buckley Moss", "Norman Rockwell"):
        assert roster.lookup(who), f"{who} is not on the roster"


def test_no_entry_claims_a_getty_id_that_nobody_looked_up():
    """An invented ULAN id would be a fabricated record the app then states as
    fact on a listing. The builder script fills them from Getty; until it has
    run, they are empty, and empty is the honest value."""
    import json
    from pathlib import Path
    data = json.loads(
        (Path(roster.__file__).resolve().parent / "data" / "artists.json")
        .read_text(encoding="utf-8"))
    for entry in data["artists"]:
        assert entry["ulan"] == "", f"{entry['name']} carries an unverified id"


# --- normalising, which is most of the value --------------------------------

@pytest.mark.parametrize("reading,expected", [
    ("Salvadore Dali", "Salvador Dali"),      # the commonest art misspelling
    ("Leroy Neiman", "LeRoy Neiman"),         # the capital everybody misses
    ("Thomas Kincade", "Thomas Kinkade"),
    ("Chagall, Marc", "Marc Chagall"),        # the auction-house inversion
    ("M.C. Escher", "M. C. Escher"),
    ("Hokusai", "Katsushika Hokusai"),        # the name the West uses
    ("Van Gogh", "Vincent van Gogh"),
])
def test_a_known_spelling_becomes_the_catalogued_one(reading, expected):
    outcome, resolved, _ = roster.resolve(reading)
    assert outcome == roster.KNOWN
    assert resolved == expected


@pytest.mark.parametrize("reading,expected", [
    ("Salvador Dali", "Salvador Dali"),
    ("Salvador Dalí", "Salvador Dalí"),
    ("Joan Miro", "Joan Miro"),
    ("Joan Miró", "Joan Miró"),
    ("Edouard Manet", "Edouard Manet"),
])
def test_an_accent_is_not_a_spelling_mistake(reading, expected):
    """"Salvador Dali" is not a misspelling of "Salvador Dalí". It is the same
    name typed on a keyboard with no í, which is every keyboard most sellers
    and most buyers own -- and eBay folds diacritics in search, so putting the
    accent back wins nothing there and can only lose: a title that renders as
    "Salvador Dal?" somewhere downstream is worse than one that never had it.

    So the roster fixes SPELLING and leaves the alphabet alone. A misspelling
    typed in ASCII comes back corrected, and still in ASCII."""
    outcome, resolved, _ = roster.resolve(reading)
    assert outcome == roster.KNOWN
    assert resolved == expected
    # ...and a genuine misspelling is still fixed, in the reading's alphabet.
    assert roster.resolve("Salvadore Dali")[1] == "Salvador Dali"
    assert roster.resolve("Salvadore Dalí")[1] == "Salvador Dalí"


def test_case_is_still_corrected_because_it_costs_nothing():
    """Search is case-insensitive, so "LeRoy" over "Leroy" is free -- and it
    is how the artist writes it."""
    assert roster.resolve("leroy neiman")[1] == "LeRoy Neiman"


@pytest.mark.parametrize("typo,expected", [
    ("Pablo Picaso", "Pablo Picasso"),
    ("Chagalll", "Marc Chagall"),
    ("Piet Mondrain", "Piet Mondrian"),       # a transposition, the commonest typo
])
def test_a_near_miss_is_corrected_and_said_out_loud(typo, expected):
    outcome, resolved, _ = roster.resolve(typo)
    assert outcome == roster.CORRECTED
    assert resolved == expected
    note = roster.correction_note(typo, resolved)
    assert typo in note and resolved in note
    # ...and the seller is told to check it, because this is still a guess
    # about somebody's signature.
    assert "Check it against the signature" in note


def test_a_transposition_costs_one_edit_not_two():
    """Plain Levenshtein charges two for two swapped letters, which puts
    exactly the misspellings this is for outside the budget."""
    assert roster._edits("mondrain", "mondrian") == 1
    assert roster._edits("chagall", "chagall") == 0


# --- the half that stops the first half becoming the bug --------------------

def test_an_artist_the_roster_never_heard_of_is_left_completely_alone():
    """The design. Most art that gets resold is by someone no roster holds."""
    for who in ("Marcia Alpert", "J. Wilkinson", "Eileen Brautigam",
                "Hildegarde Sorensen", "Tomasz Wiśniewski"):
        outcome, resolved, entry = roster.resolve(who)
        assert outcome == roster.UNKNOWN, f"{who} was not left alone"
        assert resolved == who, "an unknown name must come back untouched"
        assert entry is None


def test_a_one_word_name_is_still_a_name():
    """Hokusai, Banksy, Christo, Erté. A rule that demanded two words would
    demote every one of them."""
    assert roster.is_name_shaped("Banksy")
    assert roster.is_name_shaped("Erté")
    assert roster.resolve("Kandinskyy")[0] in (roster.CORRECTED, roster.UNKNOWN)


@pytest.mark.parametrize("not_a_name", [
    "84/250", "1987 / 250", "© 1987 Publisher Inc", "A/P", "",
    "Limited Edition of 500", "?????", "-- illegible --",
])
def test_something_that_is_not_a_name_is_not_published_as_one(not_a_name):
    """These arrive in the artist field because they were read off the same
    margin the signature is in. Not published as a person -- and not silently
    dropped either: the seller is told what was read."""
    outcome, _, _ = roster.resolve(not_a_name)
    assert outcome == roster.UNVERIFIABLE


def test_the_unverifiable_note_says_where_the_reading_probably_belongs():
    note = roster.unverifiable_note("© 1987 Gallery Editions")
    assert "does not look like an artist's name" in note
    assert "belongs in the description" in note


# --- the two ways a correction could name the wrong person ------------------

def test_two_artists_one_letter_apart_are_not_merged():
    """Monet and Manet are one substitution apart and are not each other."""
    assert roster.resolve("Monet")[1] == "Claude Monet"
    assert roster.resolve("Manet")[1] == "Edouard Manet"
    assert roster.resolve("Édouard Manet")[1] == "Édouard Manet"


def test_a_surname_two_artists_share_resolves_to_neither():
    """The roster holds three Wyeths. Writing "Andrew Wyeth" onto a piece
    signed "N.C. Wyeth" is a confident attribution to the wrong person, which
    is worse than the vagueness it replaced -- so a shared bare surname is
    struck structurally, whatever the data happens to list as an alias."""
    for shared in ("Wyeth", "Buffet", "Yoshida"):
        outcome, resolved, _ = roster.resolve(shared)
        assert outcome == roster.UNKNOWN, f"{shared!r} resolved to {resolved!r}"
        assert resolved == shared
    # ...while the full names still resolve, which is the point of striking
    # only the ambiguous bare form.
    assert roster.resolve("Andrew Wyeth")[0] == roster.KNOWN
    assert roster.resolve("N.C. Wyeth")[1] == "N. C. Wyeth"
    assert roster.resolve("Guy Buffet")[0] == roster.KNOWN
    assert roster.resolve("Bernard Buffet")[0] == roster.KNOWN


def test_a_short_surname_is_never_fuzzy_matched():
    """At four or five characters, two edits reaches half the dictionary."""
    assert roster.near_miss("Klee") is None
    assert roster.near_miss("Miro") is None or roster.lookup("Miro")


# --- a missing roster is a working app --------------------------------------

def test_no_roster_file_means_every_name_is_left_alone(monkeypatch, tmp_path):
    """Every caller's behaviour on "not in the roster" is to change nothing,
    so an app with no roster drafts exactly as it did before there was one."""
    monkeypatch.setattr(roster, "_DATA", tmp_path / "nope.json")
    roster.reload()
    assert roster.size() == 0
    outcome, resolved, _ = roster.resolve("Salvadore Dali")
    assert outcome == roster.UNKNOWN and resolved == "Salvadore Dali"


# --- and what the draft actually gets ---------------------------------------

def test_the_corrected_spelling_reaches_the_title_not_just_the_brand():
    """The lookup writes its proposed title from what it READ, so a title
    arrives misspelled while the brand has already been fixed. Leaving that is
    the worst of both: the right artist in the field nobody searches and the
    wrong spelling in the field everybody does."""
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.models import Listing
    from backend import main

    listing = Listing(title="Vintage Art Print",
                      category_suggestion="Art > Art Prints")
    main._apply_artwork(listing, {
        "artist": "Salvadore Dali", "work": "Lincoln in Dalivision",
        "confidence": "high",
        "title": "Salvadore Dali Lincoln in Dalivision Lithograph Hand Signed",
    })
    assert listing.brand == "Salvador Dali"
    assert listing.title.startswith("Salvador Dali")
    assert "Salvadore" not in listing.title


def test_an_unrostered_artist_reaches_the_draft_exactly_as_read():
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.models import Listing
    from backend import main

    listing = Listing(title="Vintage Art Print",
                      category_suggestion="Art > Art Prints")
    main._apply_artwork(listing, {
        "artist": "Marcia Alpert", "work": "Baby in a Basket",
        "confidence": "high",
        "title": "Marcia Alpert Baby in a Basket Gouache",
    })
    assert listing.brand == "Marcia Alpert"
    assert listing.title.startswith("Marcia Alpert")
    assert not [m for m in listing.missing_info if "letters from" in m]


def test_an_edition_number_in_the_artist_field_is_not_published_as_a_person():
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend.models import Listing
    from backend import main

    listing = Listing(title="Vintage Art Print",
                      category_suggestion="Art > Art Prints")
    main._apply_artwork(listing, {"artist": "84/250", "confidence": "high"})
    assert listing.brand == ""
    assert not [s for s in listing.item_specifics
                if s.name.lower() == "artist" and s.value]
    assert [m for m in listing.missing_info if "does not look like" in m]


# --- and that a refresh cannot quietly delete the useful half ---------------
#
# The roster starts as a hand-written seed chosen for the RESALE print market
# -- LeRoy Neiman, Thomas Kinkade, Bev Doolittle, Charles Wysocki -- who
# dominate what a reseller actually finds and who rank nowhere on a
# museum-presence proxy. A refresh that replaced the file wholesale would drop
# every one of them, and the misspellings somebody sat down and thought of
# ("Thomas Kincade") along with them. No API will ever return those.

def _builder():
    import importlib.util
    from pathlib import Path
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "build_art_roster.py")
    spec = importlib.util.spec_from_file_location("build_art_roster", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_refresh_keeps_the_seeded_resale_names_it_never_fetches():
    pytest.importorskip("httpx")
    build = _builder()
    seed = [
        {"name": "Thomas Kinkade", "ulan": "", "aka": ["Thomas Kincade"],
         "born": "1958", "died": "2012", "nationality": "American",
         "mediums": ["giclee"], "signature_note": "note"},
        {"name": "Salvador Dalí", "ulan": "", "aka": ["Salvadore Dali"],
         "born": "1904", "died": "1989", "nationality": "Spanish",
         "mediums": [], "signature_note": "widely forged"},
    ]
    fetched = [{"name": "Salvador Dalí", "ulan": "500009365",
                "aka": ["Dalí, Salvador"], "born": "1904", "died": "1989",
                "nationality": "Spain", "mediums": [], "signature_note": "",
                "rank_hint": 250}]
    merged = {e["name"]: e for e in build.merge(fetched, seed)}

    # The name the fetch never returned is still there, with its hand-written
    # misspelling -- which is the most useful string in the entry.
    assert "Thomas Kinkade" in merged
    assert "Thomas Kincade" in merged["Thomas Kinkade"]["aka"]
    # The fetched entry gained its Getty id and kept every seeded spelling.
    dali = merged["Salvador Dalí"]
    assert dali["ulan"] == "500009365"
    assert "Salvadore Dali" in dali["aka"] and "Dalí, Salvador" in dali["aka"]
    assert dali["signature_note"] == "widely forged"


def test_a_rank_never_claims_to_be_sales_data():
    """Real turnover is behind Artnet's and Artprice's paywalls. What the
    builder computes is a presence proxy, and every entry has to say so --
    a number pretending to be sales data is worse than no number."""
    pytest.importorskip("httpx")
    build = _builder()
    import io
    import contextlib
    from pathlib import Path
    assert "PROXY" in build.__doc__ and "paywall" in build.__doc__
    # ...and nothing in the app reads rank as if it were turnover.
    source = (Path(build.__file__).resolve().parents[1] / "backend" / "services"
              / "experts" / "art" / "roster.py").read_text()
    assert "rank" not in source, "roster.py must not read rank at all yet"
    del io, contextlib

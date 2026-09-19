"""The museum may confirm a name and spell it. It may not pick who counts.

The Met's Collection API (https://metmuseum.github.io/) is the third leg
roster.py has always claimed and scripts/build_art_roster.py never had: no
key, CC0 data, and a real catalogue behind it rather than an encyclopedia. It
knows how a registrar writes a name, and `aka` -- the spellings -- is the one
field in the roster the running app actually reads, because it is the index
roster.lookup() searches. A catalogued spelling folded into this file is an
attribution the app FIXES instead of missing.

Which is also the whole danger. A source that can add a Getty id to an artist
can add the WRONG Getty id, and the app states what is in this file as fact on
a listing; a source that can rank artists by museum presence can quietly turn
the roster into a list of who is allowed to be an artist, which is the one
thing roster.py says it must never become. So the leg is built around a single
rule -- IT MAY ADD AND CONFIRM, IT MAY NEVER SUBTRACT OR OVERRULE -- and this
file is mostly that rule, asked one way at a time.

EVERYTHING HERE RUNS OFF FIXTURES. collectionapi.metmuseum.org is not
reachable from the sandbox CI runs in, and a data leg nobody can test is a
data leg that breaks silently the first time a field upstream is renamed. The
fake museum below serves objects in the real API's field names, and the three
ways an object gets REFUSED are the three the verification exists for: a name
the search merely contained, one spelling two catalogued people answer to, and
a relationship to the work that is not the named artist's own hand.
"""
from __future__ import annotations

import json

import pytest

from backend.services.experts.art import roster
from backend.services.experts.art.comps import MEDIUMS


def _builder():
    import importlib.util
    from pathlib import Path
    path = (Path(__file__).resolve().parents[2] / "scripts"
            / "build_art_roster.py")
    spec = importlib.util.spec_from_file_location("build_art_roster", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def build():
    pytest.importorskip("httpx")
    return _builder()


@pytest.fixture(autouse=True)
def _fresh():
    roster.reload()
    yield
    roster.reload()


# --- the fake museum --------------------------------------------------------

class _Response:
    def __init__(self, payload, status: int = 200):
        self.status_code = status
        self.headers: dict = {}
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Museum:
    """A Met that serves a fixed catalogue and counts what it was asked."""

    def __init__(self, hits: list[int], objects: dict,
                 search_payload=None, total=None):
        self.hits = hits
        self.objects = objects
        self.search_payload = search_payload
        self.total = total
        self.asked: list[str] = []

    def get(self, url, params=None):
        self.asked.append(url)
        if url.endswith("/search"):
            if self.search_payload is not None:
                return _Response(self.search_payload)
            return _Response({"total": self.total if self.total is not None
                              else len(self.hits),
                              "objectIDs": self.hits})
        object_id = int(url.rsplit("/", 1)[-1])
        if object_id not in self.objects:
            return _Response(None)      # the shape a miss takes, not an error
        return _Response(self.objects[object_id])


def _object(name: str, *, ulan: str = "", alpha: str = "", medium: str = "",
            classification: str = "", born: str = "", died: str = "",
            nationality: str = "", prefix: str = "",
            role: str = "Artist") -> dict:
    """One Met object, in the field names the real API uses."""
    return {
        "artistDisplayName": name,
        "artistAlphaSort": alpha,
        "artistPrefix": prefix,
        "artistRole": role,
        "artistULAN_URL": (f"http://vocab.getty.edu/page/ulan/{ulan}"
                           if ulan else ""),
        "artistBeginDate": born,
        "artistEndDate": died,
        "artistNationality": nationality,
        "medium": medium,
        "classification": classification,
    }


def _hiroshige(build, **kwargs):
    """The Met asked about Utagawa Hiroshige, who is the shape of the win: the
    catalogue's alphaSort for a Japanese artist is the art name alone, which
    is what a seller actually types.

    The entry it is folded into below starts with NO akas, which is how the
    Wikidata leg leaves one -- `to_entry` has only altLabels and a surname
    inversion it synthesises itself. That is where this leg earns its place:
    the hand-written seed is 115 artists somebody thought carefully about (it
    lists "Hiroshige" already), and the fetched half is a thousand nobody
    curated at all.
    """
    museum = _Museum(
        [36491],
        {36491: _object("Utagawa Hiroshige", ulan="500032932",
                        alpha="Hiroshige", born="1797", died="1858",
                        nationality="Japanese",
                        medium="Woodblock print", classification="Prints")},
        **kwargs)
    return museum, build.met_artist(museum, "Utagawa Hiroshige", pause=0.0)


# --- what the leg is for ----------------------------------------------------

def test_a_catalogued_spelling_reaches_the_app_that_looks_names_up(build,
                                                                   tmp_path,
                                                                   monkeypatch):
    """The point of the whole leg, asked end to end.

    Not "the dict has a new key" -- that proves nothing a typo would not also
    pass. This writes the enriched entry to a roster file, points the running
    module at it, and asks the app the question a seller's draft asks. On an
    entry as the Wikidata leg leaves it, a signature read as "Hiroshige"
    resolves to UNKNOWN and is left alone: correct, and no help to anybody.
    With the catalogue's own alphaSort in the file it resolves to the artist,
    under the name collectors search.
    """
    entries = [{"name": "Utagawa Hiroshige", "ulan": "", "aka": [],
                "born": "", "died": "", "nationality": "", "mediums": [],
                "signature_note": ""}]
    path = tmp_path / "artists.json"

    def _resolve_hiroshige_against(roster_entries):
        path.write_text(json.dumps({"artists": roster_entries}),
                        encoding="utf-8")
        monkeypatch.setattr(roster, "_DATA", path)
        roster.reload()
        return roster.resolve("Hiroshige")

    before, _, _ = _resolve_hiroshige_against(entries)
    assert before == roster.UNKNOWN, "the gap this leg is here to close"

    _, found = _hiroshige(build)
    build.enrich_from_met(entries, lambda name: found)
    outcome, resolved, entry = _resolve_hiroshige_against(entries)
    assert outcome == roster.KNOWN
    assert resolved == "Utagawa Hiroshige"
    assert entry["ulan"] == "500032932"


def test_the_getty_id_that_has_always_been_empty_gets_filled(build):
    """`ulan` has been "" on every entry since the file was written, because
    nothing had ever looked one up and an invented id would be a fabricated
    record. The catalogue's own artistULAN_URL is the thing that finally
    fills it honestly."""
    _, found = _hiroshige(build)
    assert found["ulan"] == "500032932"
    assert found["born"] == "1797" and found["died"] == "1858"
    assert found["objects"] == 1


def test_the_mediums_it_contributes_are_words_the_comp_search_knows(build):
    """"Woodblock print" is a curator's sentence; "woodblock" is what a
    collector types and what comps.medium_of() looks for. A medium spelled in
    the museum's vocabulary rather than the market's buys nothing, which is
    why the list is imported from comps instead of restated."""
    _, found = _hiroshige(build)
    assert found["mediums"] == ["woodblock"]
    assert set(found["mediums"]) <= set(MEDIUMS)


@pytest.mark.parametrize("curator,expected", [
    ("Lithograph printed in colors", {"lithograph"}),
    ("Etching and aquatint", {"aquatint", "etching"}),
    ("Oil on canvas", {"oil on canvas"}),
    ("Color screenprint", {"screenprint"}),
    ("Graphite and gouache on paper", {"gouache"}),
    ("Bronze", set()),
])
def test_a_curators_sentence_becomes_the_markets_words(build, curator,
                                                       expected):
    assert build._met_mediums(curator) == expected


# --- the verification, which is most of the code ----------------------------

def test_a_name_the_search_merely_contained_is_never_believed(build):
    """/search?q=Marc+Chagall is a TEXT search. It returns everything by every
    other Chagall too, and a Getty id read off one of those would be a
    fabricated record the app then states as fact about an artist. The
    object's own artistDisplayName has to come back as the name asked for,
    exactly once normalised, and nothing looser."""
    museum = _Museum([1, 2], {
        1: _object("David Chagall", ulan="500000001"),
        2: _object("Chagall Studio", ulan="500000002"),
    })
    assert build.met_artist(museum, "Marc Chagall", pause=0.0) is None


def test_the_auction_inversion_still_agrees_with_the_name(build):
    """...and "looser" does not mean "brittle". roster.normalise is the app's
    own comparison, so a catalogue that writes "Chagall, Marc" agrees with a
    file that writes "Marc Chagall" here exactly as it does in the app."""
    museum = _Museum([1], {1: _object("Chagall, Marc", ulan="500010680",
                                      alpha="Chagall, Marc")})
    found = build.met_artist(museum, "Marc Chagall", pause=0.0)
    assert found and found["ulan"] == "500010680"


def test_two_catalogued_people_with_one_spelling_are_both_refused(build):
    """Two objects agreeing on the name and disagreeing on the Getty id means
    the search conflated two catalogued people who share a spelling, and
    nothing here knows which one the seller's print is by. Believing the
    first would put one artist's dates and nationality on the other's entry,
    which is worse than the blank it replaced."""
    museum = _Museum([1, 2], {
        1: _object("John Smith", ulan="500000011", born="1820", died="1889"),
        2: _object("John Smith", ulan="500000022", born="1946"),
    })
    assert build.met_artist(museum, "John Smith", pause=0.0) is None


@pytest.mark.parametrize("prefix,role", [
    ("After a design by", "Artist"),      # somebody else's hand, their name
    ("Attributed to", "Artist"),          # the catalogue itself is unsure
    ("", "Publisher"),                    # they paid for it, not made it
    ("", "Printer"),
    ("Workshop of", "Artist"),
    ("Formerly attributed to", "Artist"),
])
def test_a_print_after_an_artist_is_not_evidence_of_their_hand(build, prefix,
                                                               role):
    """The identity would survive any of these -- the Getty id still belongs
    to the person named -- but the MEDIUM would not. A lithograph published by
    Ambroise Vollard is not evidence that Vollard was a lithographer, and an
    entry that learned it was would send his market a query about somebody
    else's plates. The object is skipped whole, which costs an enrichment the
    next object supplies and cannot be wrong."""
    museum = _Museum([1], {1: _object("Marc Chagall", ulan="500010680",
                                      medium="Lithograph", prefix=prefix,
                                      role=role)})
    assert build.met_artist(museum, "Marc Chagall", pause=0.0) is None


@pytest.mark.parametrize("role", [
    "Artist", "Draughtsman", "Designer", "Printmaker", "Engraver", "Painter",
])
def test_a_role_that_is_still_their_own_hand_is_believed(build, role):
    """The deny list is about RELATIONSHIPS to somebody else's making, not
    about the word "Artist". A printmaker made the print and an engraver cut
    the plate; refusing either would throw away most of what the catalogue
    knows about the print market, which is the half of it that matters here.
    Matched on whole words, so "Draughtsman" is not read as a relationship.
    """
    museum = _Museum([1], {1: _object("Marc Chagall", ulan="500010680",
                                      role=role)})
    assert build.met_artist(museum, "Marc Chagall", pause=0.0)


def test_a_living_artist_does_not_die_in_the_year_9999(build):
    """The Met writes a living artist's end date as "9999". Copied straight
    through it would put a death year on Alex Katz and then on a listing."""
    museum = _Museum([1], {1: _object("Alex Katz", ulan="500115194",
                                      born="1927", died="9999")})
    found = build.met_artist(museum, "Alex Katz", pause=0.0)
    assert found["born"] == "1927"
    assert found["died"] == ""


@pytest.mark.parametrize("url", [
    "",
    "http://vocab.getty.edu/page/ulan/",
    "http://vocab.getty.edu/page/ulan/not-a-number",
    "http://vocab.getty.edu/page/ulan/12",
    "https://www.wikidata.org/wiki/Q5589",
])
def test_an_id_this_script_did_not_understand_is_left_empty(build, url):
    """A field left empty is worth more than one filled with a guess, because
    the app states what is in this file as fact."""
    assert build._ulan_id(url) == ""


# --- add and confirm; never subtract or overrule ----------------------------

def test_it_never_renames_anyone_and_never_drops_a_spelling(build):
    """Renaming an artist renames them on every listing drafted afterwards,
    and the hand-written akas ("Thomas Kincade") are the most useful strings
    in the file -- somebody sat down and thought of them and no API will ever
    return one."""
    entries = [{"name": "Thomas Kinkade", "ulan": "", "aka": ["Thomas Kincade"],
                "born": "1958", "died": "2012", "nationality": "American",
                "mediums": ["giclee"], "signature_note": "note"}]
    build.enrich_from_met(entries, lambda name: {
        "ulan": "500000099", "spellings": ["Kinkade, Thomas"],
        "born": "1958", "died": "2012", "nationality": "American",
        "mediums": ["lithograph"], "objects": 2})
    entry = entries[0]
    assert entry["name"] == "Thomas Kinkade"
    assert "Thomas Kincade" in entry["aka"]
    assert "Kinkade, Thomas" in entry["aka"]
    assert entry["mediums"] == ["giclee", "lithograph"]
    assert entry["signature_note"] == "note"


def test_a_value_already_in_the_file_is_never_overwritten(build):
    """Whatever is there came from Wikidata or from the hand-written seed, and
    both have been through review. The museum arrived afterwards."""
    entries = [{"name": "Salvador Dalí", "ulan": "500009365",
                "aka": ["Salvadore Dali"], "born": "1904", "died": "1989",
                "nationality": "Spanish", "mediums": [],
                "signature_note": "widely forged"}]
    enriched, conflicts = build.enrich_from_met(entries, lambda name: {
        "ulan": "500009365", "spellings": [], "born": "1904", "died": "1989",
        "nationality": "Spain", "mediums": [], "objects": 40})
    assert enriched == 1 and conflicts == []
    entry = entries[0]
    assert entry["ulan"] == "500009365"
    assert entry["nationality"] == "Spanish", \
        "the seed's adjective, not the museum's country"
    assert entry["signature_note"] == "widely forged"


def test_two_sources_naming_two_getty_records_for_one_name_is_reported(build):
    """The strongest "we have the wrong person" signal either source can give,
    and the one most worth a human's eye: the Getty id IS the identity, the
    app states it, and two catalogues disagreeing about it means one of them
    is describing somebody else. Reported, and the reviewed value stays."""
    entries = [{"name": "Salvador Dalí", "ulan": "500009365", "aka": [],
                "born": "1904", "died": "1989", "nationality": "Spanish",
                "mediums": [], "signature_note": ""}]
    _, conflicts = build.enrich_from_met(entries, lambda name: {
        "ulan": "500999999", "spellings": [], "born": "1904", "died": "1989",
        "nationality": "Spanish", "mediums": [], "objects": 40})
    assert entries[0]["ulan"] == "500009365"
    assert conflicts == ["Salvador Dalí: ulan is 500009365 here "
                         "and 500999999 at the Met"]


def test_a_date_two_sources_disagree_about_is_reported_not_resolved(build):
    """A birth year the file and the catalogue disagree about is the shape
    "we have the wrong person" takes across two sources. The reviewer of the
    pull request is better placed to say which is right than the script is,
    so it is printed and nothing is written."""
    entries = [{"name": "John Smith", "ulan": "", "aka": [], "born": "1820",
                "died": "", "nationality": "", "mediums": [],
                "signature_note": ""}]
    _, conflicts = build.enrich_from_met(entries, lambda name: {
        "ulan": "500000011", "spellings": [], "born": "1946", "died": "2001",
        "nationality": "American", "mediums": [], "objects": 3})
    entry = entries[0]
    assert entry["born"] == "1820", "the reviewed value stays"
    assert entry["died"] == "2001", "an EMPTY field is still filled"
    assert conflicts == ["John Smith: born is 1820 here and 1946 at the Met"]


def test_the_seeded_resale_names_survive_a_museum_that_never_heard_of_them(
        build):
    """The Met holds no LeRoy Neiman, no Bev Doolittle, no Charles Wysocki --
    the names that dominate what a reseller actually finds. That is the normal
    case, not a failure, and it must leave their entries untouched: absent
    from a source is never a reason to drop anything (roster.py)."""
    before = [{"name": "LeRoy Neiman", "ulan": "", "aka": ["Leroy Neiman"],
               "born": "1921", "died": "2012", "nationality": "American",
               "mediums": ["serigraph"], "signature_note": ""},
              {"name": "Bev Doolittle", "ulan": "", "aka": [], "born": "1947",
               "died": "", "nationality": "American", "mediums": [],
               "signature_note": ""}]
    entries = json.loads(json.dumps(before))
    enriched, conflicts = build.enrich_from_met(entries, lambda name: None)
    assert (enriched, conflicts) == (0, [])
    assert entries == before


def test_nothing_is_added_that_the_roster_would_read_as_a_second_artist(build):
    """An aka equal to the canonical name is not a spelling, it is a duplicate
    key -- and roster._load treats a spelling two entries claim as nobody's.
    The enricher discards it rather than relying on the loader to."""
    entries = [{"name": "Marc Chagall", "ulan": "", "aka": [], "born": "",
                "died": "", "nationality": "", "mediums": [],
                "signature_note": ""}]
    build.enrich_from_met(entries, lambda name: {
        "ulan": "500010680", "spellings": ["Marc Chagall", "Chagall, Marc"],
        "born": "1887", "died": "1985", "nationality": "French",
        "mediums": [], "objects": 412})
    assert entries[0]["aka"] == ["Chagall, Marc"]


# --- and a museum having a bad afternoon is not a failed refresh ------------

def test_a_museum_that_answers_nothing_useful_just_adds_nothing(build):
    """Every failure here returns None and the entry is left exactly as it
    was. The roster is worth more than the enrichment: a refresh that died
    because an API was down would be a refresh that never ran, and the file it
    would have written is the one the app is already using."""
    for payload in ([], {}, {"total": 0, "objectIDs": None},
                    {"total": 0, "objectIDs": []}):
        museum = _Museum([], {}, search_payload=payload)
        assert build.met_artist(museum, "Marc Chagall", pause=0.0) is None

    # ...and an object the catalogue does not serve is skipped, not fatal.
    museum = _Museum([1, 2], {2: _object("Marc Chagall", ulan="500010680")})
    found = build.met_artist(museum, "Marc Chagall", pause=0.0)
    assert found and found["ulan"] == "500010680"


def test_a_client_that_raises_does_not_raise_out_of_met_get(build):
    class _Broken:
        def get(self, url, params=None):
            raise OSError("the network is on fire")

    assert build.met_get(_Broken(), "/search", {"q": "x"}, retries=1) is None


def test_the_probe_count_bounds_what_one_artist_costs(build):
    """One search plus `probes` objects, and no more. A monthly job that asks
    a free API a few thousand times is a good neighbour; one that walks every
    object a search returned is not."""
    objects = {i: _object("Marc Chagall", ulan="500010680") for i in range(1, 60)}
    museum = _Museum(list(objects), objects)
    build.met_artist(museum, "Marc Chagall", probes=3, pause=0.0)
    assert len(museum.asked) == 4
    assert sum(1 for u in museum.asked if "/objects/" in u) == 3


# --- honesty about the number, same as `rank` -------------------------------

def test_a_met_object_count_never_claims_to_be_turnover_either(build):
    """`rank` has to say it is a presence proxy rather than sales data. A
    count of one museum's holdings is under exactly the same obligation, and
    for the same reason: a number that pretends to be market data is worse
    than no number."""
    import re
    from pathlib import Path
    # Adjacent string literals joined, so the assertion is about what the
    # comment SAYS and not about where the author happened to wrap the line.
    source = re.sub(r'"\s*\n\s*"', "",
                    Path(build.__file__).read_text(encoding="utf-8"))
    assert "met_objects" in source
    for claim in ("not a rank", "not turnover either"):
        assert claim in source, f"the payload comment must say {claim!r}"
    roster_src = (Path(roster.__file__)).read_text(encoding="utf-8")
    assert "met_objects" not in roster_src, \
        "nothing in the app may read met_objects as if it meant something"

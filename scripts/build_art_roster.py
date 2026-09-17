#!/usr/bin/env python3
"""Build the artist roster from Getty ULAN, Wikidata and museum open data.

    python3 scripts/build_art_roster.py --limit 1000

Writes backend/services/experts/art/data/artists.json: the artists whose work
actually turns over, with the spellings a signature gets read as. See
services/experts/art/roster.py for what the app does with it -- and for the
rule that governs everything here, which is that an artist ABSENT from this
file must never be a reason to drop a name. The roster fixes spellings. It is
not a list of who is allowed to be an artist.

WHY THIS IS A SCRIPT AND NOT A SERVICE. It hits two public APIs a few
thousand times and takes minutes. It runs on a laptop or in the scheduled
GitHub Action (.github/workflows/art-roster.yml), which opens a PULL REQUEST
rather than committing -- a roster change alters what every art listing in the
app is allowed to be called, and that is reviewed like code.

THE MUSEUM LEG. roster.py has always described this file as built from "Getty
ULAN, Wikidata and museum open data", and for as long as it has said so the
third one was aspirational -- the script asked Wikidata and nothing else. The
Met's Collection API (https://metmuseum.github.io/) is that third leg: no key,
CC0 data, and, the part that earns it a place here, a CATALOGUE rather than an
encyclopedia. It knows how a registrar writes a name, which is a different and
more useful thing than knowing how many Wikipedia editions carry it. What it
contributes and what it is emphatically not allowed to do is written out above
`met_artist` below; the short version is that it may add a spelling and
confirm an identity, and it may never subtract one or rank anybody.

ON "MOST TRADED", HONESTLY. Real turnover -- hammer prices, lots sold per year
-- is behind Artnet's and Artprice's paywalls, and this script has no access
to it. What it computes is a PROXY: how present an artist is across Wikipedia
and the open museum collections, which correlates with market presence and is
not the same thing. Every entry records `rank_basis` saying so, and nothing in
the app reads `rank` as if it were turnover. A number that pretends to be
sales data would be worse than no number.

ON MERGING WITH WHAT IS ALREADY THERE. The checked-in file starts as a
hand-written seed, chosen for the RESALE print market -- LeRoy Neiman, Thomas
Kinkade, Bev Doolittle, Charles Wysocki -- who dominate what a reseller
actually finds and who rank nowhere on a museum-presence proxy. Those entries
are kept, and their hand-written aka spellings are merged rather than
overwritten, because "Thomas Kincade" is a misspelling somebody sat down and
thought of and no API will ever return it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:  # pragma: no cover - the script says what it needs
    sys.exit("this script needs httpx:  pip install httpx")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend" / "services" / "experts" / "art" / "data" / "artists.json"

# The app's own name comparison and its own medium vocabulary, imported rather
# than copied. Both modules are pure stdlib, so this costs nothing -- and a
# second copy of either would drift from the original, which is a failure this
# codebase has already had once: the art word lists lived in both main.py and
# experts/art/match.py, the copies disagreed, and items carrying nothing but an
# edition mark were drafted as art by one and refused the art lookup by the
# other. A roster whose mediums are spelled in words `comps.medium_of()` does
# not look for is the same bug with a quieter symptom.
sys.path.insert(0, str(ROOT))

from backend.services.experts.art import roster            # noqa: E402
from backend.services.experts.art.comps import MEDIUMS     # noqa: E402

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
# Wikidata asks for a descriptive User-Agent naming the tool and a contact.
# Sending a default one gets the query endpoint blocked, for everybody.
USER_AGENT = ("ThryftShopRosterBuilder/1.0 "
              "(+https://github.com/LeifHansen/Listing) python-httpx")

# Artists with a Getty ULAN id, ranked by how many Wikipedia language editions
# carry them. ULAN is the authority the art world catalogues names under, and
# requiring one is also a quality filter: it keeps the roster to people with a
# real catalogued identity rather than every person Wikidata calls a painter.
QUERY = """
SELECT ?artist ?artistLabel ?ulan ?born ?died ?nationalityLabel
       (COUNT(DISTINCT ?sitelink) AS ?links)
       (GROUP_CONCAT(DISTINCT ?altLabel; separator="|") AS ?aka)
WHERE {
  ?artist wdt:P245 ?ulan .
  ?artist wdt:P106 ?occupation .
  VALUES ?occupation { wd:Q1028181 wd:Q1281618 wd:Q15296811 wd:Q11569986
                       wd:Q33231 wd:Q10862983 }
  OPTIONAL { ?artist wdt:P569 ?born }
  OPTIONAL { ?artist wdt:P570 ?died }
  OPTIONAL { ?artist wdt:P27 ?nationality }
  OPTIONAL { ?sitelink schema:about ?artist }
  OPTIONAL { ?artist skos:altLabel ?altLabel . FILTER(lang(?altLabel) = "en") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
GROUP BY ?artist ?artistLabel ?ulan ?born ?died ?nationalityLabel
ORDER BY DESC(?links)
LIMIT %d
"""


def _year(value: str) -> str:
    """"1904-05-11T00:00:00Z" -> "1904". "" for anything else."""
    text = str(value or "")
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else ""


def _ascii(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


def fetch(limit: int, retries: int = 3) -> list[dict]:
    """The SPARQL rows, or exit with a message. Retries on the endpoint's
    rate limiter, which is generous but real."""
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.get(
                WIKIDATA_SPARQL,
                params={"query": QUERY % limit, "format": "json"},
                headers={"User-Agent": USER_AGENT,
                         "Accept": "application/sparql-results+json"},
                timeout=180.0, follow_redirects=True)
            if resp.status_code == 429 and attempt < retries:
                wait = int(resp.headers.get("retry-after") or 30)
                print(f"  rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["results"]["bindings"]
        except httpx.HTTPError as exc:
            if attempt >= retries:
                sys.exit(f"wikidata query failed after {retries} tries: "
                         f"{type(exc).__name__}")
            time.sleep(5 * attempt)
    return []


def to_entry(row: dict) -> Optional[dict]:
    def value(key: str) -> str:
        return str((row.get(key) or {}).get("value") or "").strip()

    name = value("artistLabel")
    ulan = value("ulan")
    # A label Wikidata could not resolve comes back as the Q-number itself.
    if not name or not ulan or name.startswith("Q") and name[1:].isdigit():
        return None

    aka = {a.strip() for a in value("aka").split("|") if a.strip()}
    # The unaccented form, because sellers type ASCII and the catalogues do
    # not, and the auction-house inversion, because that is how a consignment
    # sheet writes a name.
    plain = _ascii(name)
    if plain != name:
        aka.add(plain)
    parts = name.split()
    if len(parts) > 1:
        aka.add(f"{parts[-1]}, {' '.join(parts[:-1])}")
    aka.discard(name)

    try:
        links = int(value("links") or 0)
    except ValueError:
        links = 0

    return {
        "name": name,
        "ulan": ulan,
        "aka": sorted(aka),
        "born": _year(value("born")),
        "died": _year(value("died")),
        "nationality": value("nationalityLabel"),
        "mediums": [],
        "signature_note": "",
        "rank_hint": links,
    }


# --- the museum leg: what a real catalogue says about a name ----------------

MET_API = "https://collectionapi.metmuseum.org/public/collection/v1"

# How many of an artist's catalogued objects to read. Every identity field is
# on every object, so one would usually do; three is cheap insurance against
# the first hit being a mis-hit, and it is also the only reason the two-people
# guard in `met_artist` can fire at all -- a single object cannot disagree with
# itself.
MET_PROBES = 3

# A courtesy pause between Met requests. The API asks for no key and publishes
# no quota, which is a reason to be careful rather than a licence. At this
# pause and MET_PROBES above, a thousand names is something like twenty
# minutes of wall clock -- which a monthly job nobody is waiting on can
# afford, and which is a better neighbour than two minutes of rude ones.
MET_SLEEP = 0.1
MET_TIMEOUT = 20.0

# Words that mean the object is not this artist's own hand. The Met records
# the relationship in `artistPrefix` ("After a design by", "Attributed to",
# "Workshop of") and in `artistRole` ("Publisher", "Printer", "Retailer"), and
# both answer the same question: did this person make the thing, or is their
# name attached to somebody else's making of it?
#
# The IDENTITY would survive either way -- the Getty id still belongs to the
# person named -- but the MEDIUM would not. A lithograph published by Ambroise
# Vollard is not evidence that Vollard was a lithographer, and a roster that
# learned it was would price his market against somebody else's plates. Rather
# than carry that distinction field by field through the entry, an object
# carrying any of these words is skipped whole: it costs an enrichment the
# next object supplies anyway, and it cannot be wrong.
_NOT_THEIR_HAND = frozenset((
    "after", "attributed", "formerly", "possibly", "probably", "style",
    "manner", "school", "workshop", "follower", "imitator", "copy", "copyist",
    "publisher", "printer", "manufacturer", "factory", "retailer", "dealer",
    "restorer", "author", "editor",
))
# ...and not "printmaker", "draughtsman", "designer" or "engraver", which are
# all a person's OWN hand however unlike "Artist" they read.


def _met_year(value) -> str:
    """A Met artist date as a year, or "".

    The Met writes a LIVING artist's end date as "9999", so a straight copy
    would bury Alex Katz in the year 9999 and put it on a listing. Anything
    outside a plausible span for a catalogued artist is not a year this file
    can use, and "" is the honest value for a date nobody knows.
    """
    year = _year(value)
    if not year:
        return ""
    return year if 1000 <= int(year) <= 2100 else ""


def _ulan_id(url) -> str:
    """"http://vocab.getty.edu/page/ulan/500010680" -> "500010680".

    "" for anything that is not plainly a ULAN id. This value ends up in a
    file the app states as fact, so a URL whose tail is not a bare number of
    about the right length is a URL this script did not understand, and a
    field it does not fill is worth more than one it fills with a guess.
    """
    tail = str(url or "").strip().rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() and 6 <= len(tail) <= 12 else ""


def _met_mediums(*texts) -> set:
    """The Met's free-text medium, in the words the app's comp search uses.

    `medium` is a sentence a curator wrote ("Lithograph printed in colors",
    "Etching and aquatint", "Oil on canvas") and `classification` is a bucket
    ("Prints", "Paintings"). Neither is a term a collector types into a search
    box, and a medium spelled in words `comps.medium_of()` does not look for
    buys nothing -- which is why the vocabulary is imported from comps rather
    than restated here.

    Every term present, not the first one: "Etching and aquatint" is an
    etching AND an aquatint, and an artist works in more than one medium
    anyway. (comps.medium_of() takes the first match because it is answering
    "what is THIS sheet" about one listing; this is answering "what does the
    catalogue hold by this person".)
    """
    hay = " ".join(str(t or "").lower() for t in texts)
    return {m for m in MEDIUMS if m in hay}


def _their_own_hand(obj: dict) -> bool:
    """Whether the object is the named artist's own work, not somebody's
    after-the-fact relationship to it. Matched on whole words, so "school of"
    is caught and "Draughtsman" is not mistaken for one."""
    said = f"{obj.get('artistPrefix') or ''} {obj.get('artistRole') or ''}"
    return not (set(re.findall(r"[a-z]+", said.lower())) & _NOT_THEIR_HAND)


def _names_agree(catalogued: str, asked: str) -> bool:
    """Whether the Met's name for an object is the artist we asked about.

    roster.normalise is the APP's own comparison -- case, accents, punctuation
    and the "Surname, Forename" inversion all folded away -- so "Chagall,
    Marc" and "Marc Chagall" agree here exactly as they do in the running app,
    and "Marc Chagall" and "David Chagall" do not.
    """
    if not str(catalogued or "").strip() or not str(asked or "").strip():
        return False
    return roster.normalise(catalogued) == roster.normalise(asked)


def met_get(client, path: str, params: Optional[dict] = None,
            retries: int = 3) -> Optional[dict]:
    """One Met API call, or None.

    None rather than an exception, everywhere. The museum leg is an enrichment
    and the roster is worth more than it: a refresh that died because a
    museum's API had a bad afternoon would be a refresh that never ran, and
    the file it would have written is one the app is already using.
    """
    for attempt in range(1, retries + 1):
        try:
            resp = client.get(f"{MET_API}{path}", params=params or {})
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                # Retry-After is allowed to be an HTTP-date rather than a
                # number of seconds, and a header this script cannot read is
                # not a reason to stop backing off.
                try:
                    wait = float(resp.headers.get("retry-after") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                time.sleep(wait or 2 * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001 - enrichment is optional
            if attempt >= retries:
                print(f"  met: {path} failed ({type(exc).__name__})",
                      file=sys.stderr)
                return None
            time.sleep(2 * attempt)
    return None


def met_artist(client, name: str, probes: int = MET_PROBES,
               pause: float = MET_SLEEP) -> Optional[dict]:
    """What the Met's catalogue says about `name`, or None.

    WHAT THIS LEG IS FOR. Spellings, and a verifiable identity. `aka` is the
    only field in the roster the running app actually READS -- it is the index
    roster.lookup() searches -- and the Met supplies two kinds of it that
    nothing else does: `artistAlphaSort`, the catalogued inversion a
    consignment sheet writes ("Bruegel, Pieter, the Elder", where the naive
    split() in `to_entry` produces the nonsense "Elder, Pieter Bruegel the"),
    and `artistDisplayName`, the punctuation a registrar uses ("M.C. Escher"
    against this file's "M. C. Escher"). Each real one of those is an
    attribution the app FIXES instead of missing. `artistULAN_URL` fills the
    `ulan` column, which has been empty since the file was written because
    nothing had ever looked one up.

    WHAT IT IS NOT FOR: deciding who belongs on the roster. Two reasons, and
    the first is the rule the whole art path is built on -- absent from this
    file must never be a reason to drop a name (roster.py). A leg that ADDED
    artists on museum presence would quietly turn the roster into a list of
    who is allowed to be an artist, which is the one thing it must not become.
    The second is that the Met simply is not the resale market: it holds no
    LeRoy Neiman, no Thomas Kinkade, no Bev Doolittle -- the names that
    dominate what a reseller actually finds -- and a source that cannot see
    them has no business ranking them. (Discovery, if it is ever wanted, wants
    the Open Access CSV at github.com/metmuseum/openaccess rather than half a
    million calls to this endpoint.)

    THE VERIFICATION IS THE REST OF THE FUNCTION. `/search?q=Marc+Chagall` is
    a TEXT search: it returns objects whose artist fields merely contain those
    words, which is everything by every other Chagall, everything after Marc
    Chagall, and whatever else the index thought was relevant. Writing a Getty
    id read off one of those into the roster would be a fabricated record that
    the app then states as fact about an artist. So the object's own
    `artistDisplayName` has to come back as the name we asked about, EXACTLY
    once both are normalised, and nothing looser. Fuzziness has one home in
    this system and it is roster.near_miss, where a surname test guards it and
    the worst it can do is correct a spelling.

    And when two probed objects agree on the name but DISAGREE on the Getty
    id, the search has conflated two catalogued people who share a spelling,
    and this function knows nothing trustworthy about either. It returns None.
    Believing the first would put one artist's dates and nationality onto the
    other's entry, which is worse than the blank it replaced.
    """
    found = met_get(client, "/search", {"q": name, "artistOrCulture": "true"})
    time.sleep(pause)
    if not found:
        return None
    ids = [i for i in (found.get("objectIDs") or []) if isinstance(i, int)]
    if not ids:
        return None
    try:
        total = int(found.get("total") or 0)
    except (TypeError, ValueError):
        total = 0

    ulans: set = set()
    spellings: set = set()
    mediums: set = set()
    born = died = nationality = ""
    matched = 0
    for object_id in ids[:max(1, probes)]:
        obj = met_get(client, f"/objects/{object_id}")
        time.sleep(pause)
        if not isinstance(obj, dict):
            continue
        catalogued = str(obj.get("artistDisplayName") or "").strip()
        if not _names_agree(catalogued, name) or not _their_own_hand(obj):
            continue
        matched += 1
        spellings.add(catalogued)
        alpha = str(obj.get("artistAlphaSort") or "").strip()
        if alpha:
            spellings.add(alpha)
        ulan = _ulan_id(obj.get("artistULAN_URL"))
        if ulan:
            ulans.add(ulan)
        mediums |= _met_mediums(obj.get("medium"), obj.get("classification"))
        born = born or _met_year(obj.get("artistBeginDate"))
        died = died or _met_year(obj.get("artistEndDate"))
        nationality = nationality or str(obj.get("artistNationality") or "").strip()

    if not matched:
        return None
    if len(ulans) > 1:
        print(f"  met: {name!r} matches {len(ulans)} catalogued people "
              f"({', '.join(sorted(ulans))}) -- believing neither",
              file=sys.stderr)
        return None
    return {"ulan": next(iter(ulans), ""),
            "spellings": sorted(s for s in spellings if s),
            "born": born, "died": died, "nationality": nationality,
            "mediums": sorted(mediums), "objects": total or matched}


def enrich_from_met(entries: list[dict], look_up) -> tuple[int, list[str]]:
    """Fold what the Met confirms into `entries`, in place.

    Returns (how many entries it had anything for, the contradictions worth a
    human's attention). `look_up(name)` is a callable returning `met_artist`'s
    dict or None -- a parameter rather than a direct call, because the Met is
    not reachable from the sandbox CI runs in, and a data leg nobody can test
    is a data leg that breaks silently the first time a field upstream is
    renamed.

    THE ONLY RULE HERE: ADD AND CONFIRM, NEVER SUBTRACT OR OVERRULE. No name
    is renamed -- renaming an artist renames them on every listing drafted
    afterwards. No aka is deleted, because the hand-written ones are the most
    useful strings in the file and nothing upstream will regenerate them. Any
    field already carrying a value keeps it: it came from Wikidata or from the
    seed and both have been through review. Where the Met CONTRADICTS a Getty
    id or a date already in the file, the script prints it for a human to read
    in the pull request and writes nothing -- a disagreement about an id or a
    birth year is the shape "we have the wrong person" takes across two
    sources, and it wants eyes rather than a winner.
    """
    enriched = 0
    conflicts: list[str] = []
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        found = look_up(name)
        if not found:
            continue
        enriched += 1

        aka = set(entry.get("aka") or [])
        for spelling in found.get("spellings") or []:
            aka.add(spelling)
            # The unaccented form too, for the reason the Wikidata leg adds
            # one: sellers type ASCII and the catalogues do not.
            plain = _ascii(spelling)
            if plain != spelling:
                aka.add(plain)
        aka.discard(name)
        entry["aka"] = sorted(a for a in aka if str(a).strip())

        entry["mediums"] = sorted(set(entry.get("mediums") or [])
                                  | set(found.get("mediums") or []))
        theirs = str(found.get("ulan") or "").strip()
        mine = str(entry.get("ulan") or "").strip()
        if theirs and not mine:
            entry["ulan"] = theirs
        elif theirs and mine and theirs != mine:
            # Wikidata and a museum registrar naming two different Getty
            # records for one spelling is the strongest "we have the wrong
            # person" signal either source can give, and the one most worth a
            # human's eye: the id is the identity, and the app states it.
            conflicts.append(f"{name}: ulan is {mine} here and {theirs} "
                             f"at the Met")

        # The dates are falsifiable, so a disagreement is reported. The
        # nationality is not: this file's seed writes the adjective
        # ("Spanish"), the Wikidata leg writes the country ("Spain"), and the
        # Met writes the adjective again -- three vocabularies for one fact,
        # where a "conflict" would be noise rather than a finding.
        for field in ("born", "died"):
            theirs = str(found.get(field) or "").strip()
            mine = str(entry.get(field) or "").strip()
            if not theirs:
                continue
            if not mine:
                entry[field] = theirs
            elif mine != theirs:
                conflicts.append(f"{name}: {field} is {mine} here and "
                                 f"{theirs} at the Met")
        if not str(entry.get("nationality") or "").strip():
            entry["nationality"] = str(found.get("nationality") or "").strip()

        # How many objects the Met's catalogue search RETURNED for this name.
        # Not "how many works this artist made" and not "how many the Met
        # holds by them": /search is a text search, so the count includes the
        # objects `met_artist` went on to refuse. It is here for the reviewer
        # of the refresh -- a name the collection knows a thousand objects
        # under is a different kind of entry from one it knows three -- and
        # the corroboration that the `ulan` beside it names who it says is
        # the exact name match that earned it, never this number.
        entry["met_objects"] = int(found.get("objects") or 0)
    return enriched, conflicts


def merge(fetched: list[dict], existing: list[dict]) -> list[dict]:
    """Fetched entries, with the hand-written seed kept and folded in.

    The seed wins on every hand-written field -- aka spellings especially.
    "Thomas Kincade" is a misspelling somebody sat down and thought of; no API
    will ever return it, and it is the single most useful string in the entry.
    """
    by_name = {}
    for entry in fetched:
        by_name[entry["name"]] = entry
    for old in existing:
        name = str(old.get("name") or "").strip()
        if not name:
            continue
        new = by_name.get(name)
        if new is None:
            # Seeded and not fetched -- the resale-market names that rank
            # nowhere on a museum-presence proxy. Kept exactly as written.
            by_name[name] = {**old, "rank_hint": old.get("rank_hint", 0)}
            continue
        new["aka"] = sorted(set(new["aka"]) | set(old.get("aka") or []))
        new["mediums"] = sorted(set(new["mediums"]) | set(old.get("mediums") or []))
        for field in ("born", "died", "nationality", "signature_note"):
            new[field] = old.get(field) or new.get(field) or ""
    return list(by_name.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=1000,
                    help="how many artists to ask Wikidata for (default 1000)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change and write nothing")
    ap.add_argument("--no-met", action="store_true",
                    help="skip the Met Museum leg (catalogued spellings and "
                         "Getty ids)")
    ap.add_argument("--met-probes", type=int, default=MET_PROBES,
                    help=f"catalogued objects read per artist "
                         f"(default {MET_PROBES})")
    ap.add_argument("--met-sleep", type=float, default=MET_SLEEP,
                    help=f"courtesy pause between Met requests, in seconds "
                         f"(default {MET_SLEEP})")
    args = ap.parse_args()

    print(f"querying wikidata for {args.limit} artists with a ULAN id...")
    rows = fetch(args.limit)
    fetched = [e for e in (to_entry(r) for r in rows) if e]
    print(f"  {len(fetched)} usable of {len(rows)} rows")

    existing = []
    if args.out.is_file():
        try:
            raw = json.loads(args.out.read_text(encoding="utf-8"))
            existing = raw.get("artists") if isinstance(raw, dict) else raw
        except Exception as exc:  # noqa: BLE001 - a bad file is an empty one
            print(f"  could not read the existing roster ({exc}); starting fresh")
    print(f"  {len(existing or [])} already on the roster")

    merged = merge(fetched, existing or [])

    if args.no_met:
        print("skipping the met museum leg (--no-met)")
    else:
        print(f"asking the met museum about {len(merged)} name(s)...")
        with httpx.Client(timeout=MET_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            enriched, conflicts = enrich_from_met(
                merged,
                lambda name: met_artist(client, name, probes=args.met_probes,
                                        pause=args.met_sleep))
        ids = sum(1 for e in merged if str(e.get("ulan") or "").strip())
        print(f"  the met had something for {enriched} of {len(merged)}; "
              f"{ids} now carry a getty id")
        # Printed rather than resolved, and to stderr so it survives being
        # piped: a date two sources disagree about is how "we have the wrong
        # person" looks from here, and the reviewer of the pull request is
        # better placed to say which one is right than this script is.
        for line in conflicts:
            print(f"  CHECK  {line}", file=sys.stderr)

    # Ranked by the proxy, then written in NAME order so a refresh produces a
    # reviewable diff instead of a reshuffle.
    ranked = sorted(merged, key=lambda e: -int(e.get("rank_hint") or 0))
    for i, entry in enumerate(ranked, 1):
        entry["rank"] = i
        entry["rank_basis"] = ("wikipedia-language-editions (a PROXY for market "
                               "presence, not turnover)"
                               if entry.pop("rank_hint", 0) else "seed")
    artists = sorted(ranked, key=lambda e: e["name"])

    payload = {
        "_comment": (
            "The artists whose work actually turns over, for normalising an "
            "attribution's spelling and catching a near miss. NOT exhaustive: "
            "see services/experts/art/roster.py -- absent from this file must "
            "never be a reason to drop a name. `rank` is a PROXY for market "
            "presence (see rank_basis), never turnover, which is paywalled. "
            "`met_objects` is how many objects the Metropolitan Museum's "
            "catalogue search returned for that name -- a text search, so a "
            "rough measure of how present the name is in one collection and "
            "not a count of the artist's works: not a rank, and not turnover "
            "either."),
        "source": "scripts/build_art_roster.py",
        "artists": artists,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    if args.dry_run:
        was = {e.get("name") for e in (existing or [])}
        now = {e["name"] for e in artists}
        print(f"  + {len(now - was)} new, {len(was - now)} dropped, "
              f"{len(artists)} total")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {len(artists)} artists to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

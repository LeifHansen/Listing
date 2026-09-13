#!/usr/bin/env python3
"""Build the artist roster from Getty ULAN, Wikidata and museum open data.

    python3 scripts/build_art_roster.py --limit 1000

Writes backend/services/experts/art/data/artists.json: the artists whose work
actually turns over, with the spellings a signature gets read as. See
services/experts/art/roster.py for what the app does with it -- and for the
rule that governs everything here, which is that an artist ABSENT from this
file must never be a reason to drop a name. The roster fixes spellings. It is
not a list of who is allowed to be an artist.

WHY THIS IS A SCRIPT AND NOT A SERVICE. It hits four public APIs a few
thousand times and takes minutes. It runs on a laptop or in the scheduled
GitHub Action (.github/workflows/art-roster.yml), which opens a PULL REQUEST
rather than committing -- a roster change alters what every art listing in the
app is allowed to be called, and that is reviewed like code.

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
            "presence (see rank_basis), never turnover, which is paywalled."),
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

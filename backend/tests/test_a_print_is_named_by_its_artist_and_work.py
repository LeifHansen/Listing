"""A print by a known artist is drafted under the artist's name and the work's.

The identify pass can only read a name that is printed on the print. For the
one that carries none, it wrote "Vintage Japanese Woodblock Art Print" -- a
title no collector searches for -- for what was Hokusai's Great Wave, and the
seller reported that prints by well-known artists were not being identified.

So an art draft that does not yet lead with its artist is looked up: a
reverse image search (Google Lens, through SerpApi, when the key is set)
says what the web already calls the picture, and a vision call with web
search reads the print first, recognises the image second, takes the matches
as leads, and confirms before naming anyone. The rules for folding that in
are the research pass's: name and fill at medium or high confidence, replace
a title only at high confidence and only when it names neither the artist nor
the work, never demote a print, never argue with an artist the draft already
names.
"""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from backend import main, storage  # noqa: E402
from backend.models import IdentifyResult, ItemSpecific, Listing  # noqa: E402
from backend.services import imagesearch  # noqa: E402

HOKUSAI = {
    "artist": "Katsushika Hokusai",
    "work": "The Great Wave off Kanagawa",
    "kind": "open edition print",
    "title": "Katsushika Hokusai The Great Wave off Kanagawa Woodblock Art Print Framed",
    "verify": ["a publisher's seal or edition number in the margin"],
    "sources": ["https://www.metmuseum.org/art/collection/search/36491"],
    "confidence": "high",
}


def _draft(**over) -> Listing:
    base = dict(title="Vintage Japanese Woodblock Art Print Wave Framed",
                brand="", category_suggestion="Art > Art Prints",
                images=["img_000.jpg"])
    base.update(over)
    return Listing(**base)


def _photos(dir_, n=1):
    dir_.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        path = dir_ / f"img_{i:03d}.jpg"
        Image.new("RGB", (300, 300), (30, 60, 120)).save(path, "JPEG")
        out.append(path)
    return out


@pytest.fixture()
def lookup(monkeypatch):
    """The lookup wired to a stub answer; returns the calls it received."""
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "ART_LOOKUP", "auto")
    calls: list[dict] = []

    def use(answer):
        def identify_artwork(paths, listing, leads=None, observations=""):
            calls.append({"paths": paths, "leads": leads,
                          "observations": observations})
            return answer
        monkeypatch.setattr(main.claude_ai, "identify_artwork", identify_artwork)
        return calls
    return use


# ------------------------------------------------------ what counts as art

def test_art_is_known_by_its_category_or_by_what_it_is_called():
    assert main._is_artwork(Listing(title="Framed picture",
                                    category_suggestion="Art > Art Prints"))
    assert main._is_artwork(Listing(title="Signed lithograph, numbered",
                                    category_suggestion=""))
    assert main._is_artwork(Listing(title="Framed picture"), "a screen print")
    assert not main._is_artwork(Listing(title="Nike hoodie size M",
                                        category_suggestion="Clothing > Men"))


def test_a_draft_that_already_leads_with_its_artist_is_left_alone(lookup, tmp_path):
    calls = lookup(HOKUSAI)
    listing = _draft(title="Katsushika Hokusai Great Wave Woodblock Print",
                     brand="Katsushika Hokusai")
    assert main._lookup_artwork(listing, _photos(tmp_path)) is None
    assert calls == []


def test_off_means_off(lookup, monkeypatch, tmp_path):
    calls = lookup(HOKUSAI)
    monkeypatch.setattr(main, "ART_LOOKUP", "off")
    assert main._lookup_artwork(_draft(), _photos(tmp_path)) is None
    assert calls == []


# ----------------------------------------------- what a confident answer does

def test_a_confident_answer_names_the_title_the_artist_and_the_specific(lookup, tmp_path):
    lookup(HOKUSAI)
    listing = _draft(missing_info=["the artist, if you can find a signature"])
    assert main._lookup_artwork(listing, _photos(tmp_path), "", "a wave") is HOKUSAI
    assert listing.title == HOKUSAI["title"]
    assert listing.brand == "Katsushika Hokusai"
    artist = [s for s in listing.item_specifics if s.name == "Artist"]
    assert [(s.value, s.confidence) for s in artist] == [("Katsushika Hokusai", "high")]
    # The nag to find the artist is answered; what to check physically stays.
    assert not any("artist" in m.lower() for m in listing.missing_info)
    assert "Verify: a publisher's seal or edition number in the margin" in listing.missing_info
    assert any(m.startswith("Looked up from: https://www.metmuseum.org")
               for m in listing.missing_info)


def test_a_hedged_title_is_replaced_even_when_it_names_the_artist(lookup, tmp_path):
    lookup(HOKUSAI)
    listing = _draft(title="Hokusai style woodblock print of a wave")
    main._lookup_artwork(listing, _photos(tmp_path))
    assert listing.title == HOKUSAI["title"]


def test_a_medium_answer_fills_the_artist_but_only_suggests_the_title(lookup, tmp_path):
    lookup({**HOKUSAI, "confidence": "medium"})
    listing = _draft()
    main._lookup_artwork(listing, _photos(tmp_path))
    assert listing.title == "Vintage Japanese Woodblock Art Print Wave Framed"
    assert listing.brand == "Katsushika Hokusai"
    assert any(m.startswith("The lookup suggests this title: “Katsushika Hokusai")
               for m in listing.missing_info)


def test_a_low_answer_changes_nothing_but_says_what_it_thought(lookup, tmp_path):
    lookup({**HOKUSAI, "confidence": "low"})
    listing = _draft()
    main._lookup_artwork(listing, _photos(tmp_path))
    assert listing.title == "Vintage Japanese Woodblock Art Print Wave Framed"
    assert listing.brand == ""
    assert not any(s.name == "Artist" for s in listing.item_specifics)
    assert any("wasn't sure" in m and "Hokusai" in m for m in listing.missing_info)


def test_nothing_found_is_nothing_applied(lookup, tmp_path):
    lookup({"artist": "", "work": "", "confidence": "low", "verify": [],
            "sources": []})
    listing = _draft()
    assert main._lookup_artwork(listing, _photos(tmp_path)) is None
    assert listing.missing_info == []


def test_an_artist_the_draft_already_names_is_not_overruled(lookup, tmp_path):
    """The draft says Hiroshige, the lookup says Hokusai. The seller is
    holding the print; the pass is not."""
    lookup(HOKUSAI)
    listing = _draft(title="Vintage Japanese Woodblock Print", brand="Hiroshige")
    main._lookup_artwork(listing, _photos(tmp_path))
    assert listing.brand == "Hiroshige"
    assert listing.title == "Vintage Japanese Woodblock Print"
    assert any("reads the artist as Katsushika Hokusai" in m
               and "the draft says Hiroshige" in m for m in listing.missing_info)


def test_a_blank_artist_row_is_filled_and_a_written_one_kept():
    listing = _draft(item_specifics=[ItemSpecific(name="Artist", value="")])
    assert main._set_artist_specific(listing, "Hokusai", "high")
    assert listing.item_specifics[0].value == "Hokusai"
    assert not main._set_artist_specific(listing, "Hiroshige", "high")
    assert listing.item_specifics[0].value == "Hokusai"


# ------------------------------------------- the reverse-image leads

def test_the_reverse_image_matches_reach_the_lookup_as_leads(lookup, monkeypatch, tmp_path):
    calls = lookup(HOKUSAI)
    monkeypatch.setattr(main.imagesearch, "enabled", lambda: True)
    monkeypatch.setattr(main.objstore, "enabled", lambda: True)
    monkeypatch.setattr(main.objstore, "exists", lambda key: True)
    monkeypatch.setattr(main.objstore, "url_for",
                        lambda key, expires=3600: f"https://bucket/{key}")
    searched: list[str] = []
    monkeypatch.setattr(main.imagesearch, "reverse_image", lambda url: (
        searched.append(url) or [{"title": "The Great Wave off Kanagawa - Wikipedia",
                                  "source": "wikipedia.org", "link": "https://w/"}]))
    paths = _photos(tmp_path)
    main._lookup_artwork(_draft(), paths, "sess-1", "a wave")
    key = main.objstore.key_for("sess-1", "img_000.jpg")
    assert searched == [f"https://bucket/{key}"]
    assert calls[0]["leads"][0]["title"].startswith("The Great Wave")
    assert calls[0]["observations"] == "a wave"


def test_without_a_key_nothing_is_searched_and_the_bucket_is_not_touched(lookup, monkeypatch, tmp_path):
    calls = lookup(HOKUSAI)
    monkeypatch.setattr(main.imagesearch, "enabled", lambda: False)

    def boom(*a, **k):
        raise AssertionError("the bucket was asked without a key to use it for")
    monkeypatch.setattr(main.objstore, "exists", boom)
    main._lookup_artwork(_draft(), _photos(tmp_path), "sess-1")
    assert calls[0]["leads"] == []


def test_a_photo_the_mirror_has_not_reached_is_uploaded_first(lookup, monkeypatch, tmp_path):
    lookup(HOKUSAI)
    monkeypatch.setattr(main.imagesearch, "enabled", lambda: True)
    monkeypatch.setattr(main.objstore, "enabled", lambda: True)
    monkeypatch.setattr(main.objstore, "exists", lambda key: False)
    uploaded: list[str] = []
    monkeypatch.setattr(main.objstore, "upload",
                        lambda path, key: uploaded.append(key) or "ok")
    monkeypatch.setattr(main.objstore, "url_for",
                        lambda key, expires=3600: "https://bucket/x")
    monkeypatch.setattr(main.imagesearch, "reverse_image", lambda url: [])
    main._lookup_artwork(_draft(), _photos(tmp_path), "sess-2")
    assert uploaded == [main.objstore.key_for("sess-2", "img_000.jpg")]


# ------------------------------------------------------- the search itself

def test_lens_matches_become_leads_best_first():
    data = {
        "knowledge_graph": {"title": "The Great Wave off Kanagawa",
                            "subtitle": "Woodblock print by Hokusai",
                            "link": "https://kg/"},
        "visual_matches": [
            {"position": 1, "title": "Under the Wave off Kanagawa (Kanagawa oki nami ura)",
             "source": "metmuseum.org", "link": "https://met/"},
            {"position": 2, "title": ""},
            "junk",
            {"position": 3, "title": "Great Wave poster 24x36",
             "source": "posters.example", "link": "https://shop/"},
        ],
    }
    leads = imagesearch.parse_leads(data)
    assert [lead["title"] for lead in leads] == [
        "The Great Wave off Kanagawa -- Woodblock print by Hokusai",
        "Under the Wave off Kanagawa (Kanagawa oki nami ura)",
        "Great Wave poster 24x36",
    ]
    assert leads[0]["source"] == "knowledge graph"
    assert leads[1]["source"] == "metmuseum.org"
    assert imagesearch.parse_leads("not json") == []
    assert imagesearch.parse_leads({}) == []


def test_without_a_key_nothing_is_asked(monkeypatch):
    monkeypatch.setattr(imagesearch.config, "SERPAPI_KEY", "")

    def boom(*a, **k):
        raise AssertionError("asked SerpApi with no key")
    monkeypatch.setattr(imagesearch.httpx, "get", boom)
    assert imagesearch.reverse_image("https://bucket/x.jpg") == []


def test_a_failed_search_never_puts_the_key_in_the_log(monkeypatch, caplog):
    """httpx names the request URL in its errors, and the request URL
    carries the key. The log line says the error's kind and nothing else."""
    monkeypatch.setattr(imagesearch.config, "SERPAPI_KEY", "sk-very-secret-123")

    def fail(*a, **k):
        raise RuntimeError("401 for url https://serpapi.com/?api_key=sk-very-secret-123")
    monkeypatch.setattr(imagesearch.httpx, "get", fail)
    with caplog.at_level(logging.WARNING, logger="thryft"):
        assert imagesearch.reverse_image("https://bucket/x.jpg") == []
    assert "sk-very-secret-123" not in caplog.text
    assert "RuntimeError" in caplog.text


# ------------------------------------------------------- in the identify job

def test_the_draft_the_seller_gets_carries_the_name(monkeypatch):
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "ART_LOOKUP", "auto")
    monkeypatch.setattr(main, "_resolve_category", lambda *a, **k: None)
    monkeypatch.setattr(main, "_enrich_listing", lambda *a, **k: 0)
    monkeypatch.setattr(main, "_research_draft", lambda *a, **k: None)
    monkeypatch.setattr(main, "_price_against_comps", lambda *a, **k: None)
    monkeypatch.setattr(main.claude_ai, "identify_artwork",
                        lambda *a, **k: HOKUSAI)

    def identify(paths, names, strategy="", notes=""):
        return IdentifyResult(listing=_draft(images=list(names)),
                              confidence="low", raw_observations="a wave")
    monkeypatch.setattr(main.claude_ai, "identify", identify)

    session_id = storage.new_session_id()
    _photos(storage.optimized_dir(session_id))
    job_id = storage.new_session_id()
    main._register_bulk_job(job_id, {"id": job_id, "kind": "identify",
                                     "done": False, "error": None})
    main._run_identify_job(job_id, session_id, None)

    saved = storage.load_listing(session_id)
    assert saved["title"] == HOKUSAI["title"]
    assert saved["brand"] == "Katsushika Hokusai"

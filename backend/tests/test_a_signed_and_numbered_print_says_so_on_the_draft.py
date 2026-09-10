"""What the art lookup reads off the margin is written onto the draft.

The lookup used to return the artist, the work and a title, and the server
used those; what it read off the margin -- a pencil signature, an edition
fraction -- stayed in a field nothing read. The seller's complaint was exact:
the app was not identifying the artist, the signature or the number. So the
lookup now says how the piece is signed and numbered, and the server writes
Signed, Signed By, Edition Type and Edition Size where they are blank and
puts "Hand Signed" and "Numbered 84/250" on the title when it has the room.

The rules are the art rule's. A reading is written; an absence never is --
nothing here writes "No", "Open Edition" or "Reproduction" -- and a mark the
lookup could not see becomes the photo to ask for. A signature that is part
of the printed image is not a hand signature. And a placeholder in brand
("Unknown artist") is a blank, so the lookup still runs.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from PIL import Image  # noqa: E402

from backend import main  # noqa: E402
from backend.models import ItemSpecific, Listing  # noqa: E402

DALI = {
    "artist": "Salvador Dali",
    "work": "Lincoln in Dalivision",
    "kind": "hand-signed limited edition print",
    "signature": "hand signed in pencil, lower right margin",
    "signature_reads": "Dali",
    "edition": "84/250",
    "year": "1977",
    "title": "Salvador Dali Lincoln in Dalivision Lithograph Hand Signed Numbered 84/250",
    "verify": ["the pencil signature against a known exemplar"],
    "sources": ["https://www.dali-gallery.com/lincoln"],
    "confidence": "high",
}


def _draft(**over) -> Listing:
    base = dict(title="Vintage Surrealist Lithograph Framed", brand="",
                category_suggestion="Art > Art Prints", images=["img_000.jpg"])
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
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main, "ART_LOOKUP", "auto")
    calls: list[dict] = []

    def use(answer):
        def identify_artwork(paths, listing, leads=None, observations="",
                             crops=None):
            calls.append({"paths": paths, "leads": leads,
                          "observations": observations, "crops": crops})
            return answer
        monkeypatch.setattr(main.claude_ai, "identify_artwork", identify_artwork)
        return calls
    return use


def _specific(listing: Listing, name: str) -> list[tuple[str, str]]:
    return [(s.value, s.confidence) for s in listing.item_specifics
            if s.name.lower() == name.lower()]


# ------------------------------------------------ the markers reach the draft

def test_a_hand_signed_numbered_print_gets_its_specifics_and_its_title(lookup, tmp_path):
    lookup(DALI)
    listing = _draft()
    main._lookup_artwork(listing, _photos(tmp_path), "", "a surrealist print")
    assert listing.title == DALI["title"]
    assert listing.brand == "Salvador Dali"
    assert _specific(listing, "Artist") == [("Salvador Dali", "high")]
    assert _specific(listing, "Signed") == [("Yes", "high")]
    assert _specific(listing, "Signed By") == [("Salvador Dali", "high")]
    assert _specific(listing, "Edition Type") == [("Limited Edition", "high")]
    assert _specific(listing, "Edition Size") == [("250", "high")]


def test_the_title_gains_hand_signed_and_the_number_when_the_lookup_did_not_title_it(lookup, tmp_path):
    """A medium answer only suggests its title; the signature and the number
    are readings of the photos and go on whatever title stands."""
    lookup({**DALI, "confidence": "medium", "title": ""})
    listing = _draft(title="Salvador Dali Lincoln in Dalivision Lithograph")
    main._lookup_artwork(listing, _photos(tmp_path))
    assert listing.title == ("Salvador Dali Lincoln in Dalivision Lithograph "
                             "Hand Signed Numbered 84/250")


def test_the_title_is_not_cut_to_make_room_for_the_markers():
    long = "x" * 75
    out = main._title_with_markers(long, main._art_markers(DALI))
    assert out == long


def test_a_title_that_already_says_signed_and_numbered_is_left_alone():
    title = "Salvador Dali Lincoln in Dalivision Signed Numbered 84/250"
    assert main._title_with_markers(title, main._art_markers(DALI)) == title


def test_an_artist_proof_is_an_edition_type_and_a_title_word():
    found = {**DALI, "edition": "A/P"}
    m = main._art_markers(found)
    assert m["proof"] and not m["numbered"]
    listing = _draft(title="Salvador Dali Lincoln in Dalivision Lithograph")
    main._apply_artwork(listing, found)
    assert _specific(listing, "Edition Type") == [("Artist Proof", "high")]
    assert listing.title.endswith("Hand Signed Artist Proof")


def test_a_proof_with_its_own_count_is_still_a_proof():
    """The 20 in "A/P 3/20" is how many proofs were pulled, not the
    edition size."""
    found = {**DALI, "edition": "A/P 3/20"}
    m = main._art_markers(found)
    assert m["proof"] and m["numbered"] == "3/20" and m["edition_size"] == ""
    listing = _draft(title="Salvador Dali Lincoln in Dalivision Lithograph")
    main._apply_artwork(listing, found)
    assert _specific(listing, "Edition Type") == [("Artist Proof", "high")]
    assert not _specific(listing, "Edition Size")
    assert listing.title.endswith("Hand Signed Artist Proof 3/20")


@pytest.mark.parametrize("wording", ["84/250", "84 / 250", "84 of 250",
                                     "no. 84 out of 250, lower left"])
def test_an_edition_is_read_however_the_lookup_wrote_it(wording):
    m = main._art_markers({**DALI, "edition": wording})
    assert m["numbered"] == "84/250" and m["edition_size"] == "250"
    assert not m["proof"]


def test_a_roman_numeral_edition_is_read_as_numbered():
    m = main._art_markers({**DALI, "edition": "XX/L"})
    assert m["numbered"] == "XX/L"
    assert m["edition_size"] == ""


def test_a_year_written_on_the_piece_fills_a_blank_year_row_and_never_makes_one(lookup, tmp_path):
    lookup(DALI)
    listing = _draft(item_specifics=[ItemSpecific(name="Year Produced", value="")])
    main._lookup_artwork(listing, _photos(tmp_path))
    assert _specific(listing, "Year Produced") == [("1977", "medium")]
    listing = _draft()
    main._apply_artwork(listing, DALI)
    assert not _specific(listing, "Year Produced")


# ------------------------------------------- a reading is written, an absence never

def test_a_signature_in_the_plate_is_not_a_hand_signature():
    for wording in ("signed in the plate only", "printed signature within the image",
                    "signature stamp on verso", "not visible in these photos", ""):
        m = main._art_markers({**DALI, "signature": wording})
        assert not m["hand_signed"], wording
    listing = _draft(title="Salvador Dali Lincoln in Dalivision Lithograph")
    main._apply_artwork(listing, {**DALI, "signature": "signed in the plate only",
                                  "edition": ""})
    assert not _specific(listing, "Signed")
    assert not _specific(listing, "Signed By")
    assert "Signed" not in listing.title


def test_nothing_is_ever_written_as_no_open_edition_or_reproduction():
    listing = _draft(title="Salvador Dali Lincoln in Dalivision Lithograph")
    main._apply_artwork(listing, {**DALI, "kind": "poster or reproduction",
                                  "signature": "", "edition": "",
                                  "title": ""})
    names = {s.name for s in listing.item_specifics}
    assert "Signed" not in names and "Edition Type" not in names
    assert not any("Open Edition" in s.value or "Reproduction" in s.value
                   for s in listing.item_specifics)


def test_a_mark_the_lookup_could_not_see_is_the_photo_to_ask_for(lookup, tmp_path):
    lookup({**DALI, "signature": "not visible in these photos",
            "edition": "not visible in these photos", "title": ""})
    listing = _draft(title="Salvador Dali Lincoln in Dalivision Lithograph")
    main._lookup_artwork(listing, _photos(tmp_path))
    assert any(m.startswith("Verify: the signature and edition number could not be seen")
               and "lower margin" in m for m in listing.missing_info)
    assert not _specific(listing, "Signed")
    assert "Signed" not in listing.title


def test_a_contested_attribution_never_fills_signed_by():
    """Two artists on one listing is the seller's question; Signed By
    must not quietly answer it for one of them. Signed is still true."""
    listing = _draft(brand="Marc Chagall",
                     title="Marc Chagall lithograph framed")
    main._apply_artwork(listing, DALI)
    assert _specific(listing, "Signed") == [("Yes", "high")]
    assert not _specific(listing, "Signed By")
    assert listing.brand == "Marc Chagall"
    assert any("check which is right" in m for m in listing.missing_info)


def test_a_value_already_on_the_draft_is_kept():
    """The zoom pass read the margin at a resolution the lookup did not, and
    the seller may have typed the value."""
    listing = _draft(item_specifics=[
        ItemSpecific(name="Edition Size", value="300", confidence=""),
        ItemSpecific(name="Signed By", value="S. Dali", confidence="high"),
    ])
    main._apply_artwork(listing, DALI)
    assert _specific(listing, "Edition Size") == [("300", "")]
    assert _specific(listing, "Signed By") == [("S. Dali", "high")]


def test_outside_an_art_category_only_existing_rows_are_filled():
    listing = _draft(category_suggestion="Collectibles > Decorative",
                     item_specifics=[ItemSpecific(name="Signed", value="")])
    main._apply_artwork(listing, DALI)
    assert _specific(listing, "Signed") == [("Yes", "high")]
    assert not _specific(listing, "Edition Size")


# ------------------------------------------------------- the lookup still runs

def test_an_artist_the_zoom_pass_read_reaches_the_brand(lookup, tmp_path):
    """The zoom pass fills the Artist specific; the lookup agrees; the brand
    -- what the title and search use -- used to stay blank because the
    specific counted as "known"."""
    lookup(DALI)
    listing = _draft(item_specifics=[ItemSpecific(name="Artist", value="Salvador Dali",
                                                  confidence="high")])
    main._lookup_artwork(listing, _photos(tmp_path))
    assert listing.brand == "Salvador Dali"
    assert listing.title == DALI["title"]


def test_the_lookup_gets_the_zoomed_margin_crops(lookup, tmp_path, monkeypatch):
    """A pencil signature is a few pixels tall in a whole frame. The boxes
    the identify pass drew are cropped and go in beside the frames."""
    calls = lookup(DALI)
    paths = _photos(tmp_path)
    tags = [{"photo": 1, "box": [0.1, 0.8, 0.9, 0.98], "kind": "signature"}]
    main._lookup_artwork(_draft(), paths, "", "", tags=tags)
    assert len(calls[0]["crops"]) == 1
    assert calls[0]["crops"][0]["type"] == "image"
    # ...and a crop that cannot be cut never costs the lookup.
    monkeypatch.setattr(main.claude_ai, "tag_crops",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    main._lookup_artwork(_draft(), paths, "", "", tags=tags)
    assert calls[1]["crops"] == []


def test_the_crops_ride_the_lookup_request(monkeypatch, tmp_path):
    from backend.services import claude_ai
    seen: dict = {}

    class _Resp:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": '{"artist": ""}'})()]
        usage = None

    class _Messages:
        def create(self, **kw):
            seen.update(kw)
            return _Resp()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(claude_ai, "_client", lambda: _Client())
    monkeypatch.setattr(claude_ai, "_log_usage", lambda *a, **k: None)
    crop = {"type": "image", "source": {"type": "base64",
                                        "media_type": "image/jpeg", "data": "x"}}
    claude_ai.identify_artwork(_photos(tmp_path), _draft(), crops=[crop])
    content = seen["messages"][0]["content"]
    assert crop in content
    intro = content[content.index(crop) - 1]
    assert intro["type"] == "text" and "signature_reads" in intro["text"]


def test_unknown_artist_in_brand_is_a_blank_not_an_artist(lookup, tmp_path):
    calls = lookup(DALI)
    listing = _draft(title="Surrealist Lithograph Artist Proof Framed",
                     brand="Unknown artist")
    assert not main._artwork_named(listing)
    main._lookup_artwork(listing, _photos(tmp_path))
    assert calls and listing.brand == "Salvador Dali"


def test_the_lookup_is_handed_what_the_zoom_pass_read(monkeypatch, tmp_path):
    """The specifics the zoom pass filled off the margin ride the lookup's
    context: they were read at a resolution its four frames are not."""
    from backend.services import claude_ai
    seen: dict = {}

    class _Resp:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": '{"artist": ""}'})()]
        usage = None

    class _Messages:
        def create(self, **kw):
            seen.update(kw)
            return _Resp()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(claude_ai, "_client", lambda: _Client())
    monkeypatch.setattr(claude_ai, "_log_usage", lambda *a, **k: None)
    listing = _draft(item_specifics=[ItemSpecific(name="Signed By", value="Dali",
                                                  confidence="high")])
    claude_ai.identify_artwork(_photos(tmp_path), listing, observations="SIGNATURE: Dali")
    text = seen["messages"][0]["content"][-1]["text"]
    assert "Specifics so far: Signed By: Dali" in text
    assert "how this piece is signed and numbered" in text
    assert '"signature":' in text and '"edition":' in text
    assert "THE SIGNATURE" in text  # the art rule rides the lookup too


def test_art_is_also_known_by_the_words_a_first_pass_writes_about_it():
    assert main._is_artwork(Listing(title="Hand signed numbered print, framed"))
    assert main._is_artwork(Listing(title="Original oil on canvas seascape"))
    assert main._is_artwork(Listing(title="Bronze sculpture on marble base"))
    assert not main._is_artwork(Listing(title="Pastel pink cardigan size M",
                                        category_suggestion="Clothing > Women"))

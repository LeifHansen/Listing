"""Going back for the item specifics the first fill left blank.

A seller saved a listing from this app, opened it on eBay, and was met with
eBay's own suggestion box offering five specifics it had filled from the same
photos and the same title: Subject: Bowling, Era: Mid 20th Century
(1941-1969), Occasion: All Occasions, Packaging: Unboxed, Character: Bowler.
All five were blank on the listing this app wrote. Every one of them is a
filter a buyer can use that the listing does not appear in.

Nothing was broken in the sense of raising: the fill pass is handed thirty-odd
aspects at once and asked to fill what it can, and what it does with the ones
it is unsure of is silently nothing. Nothing downstream ever noticed, because
nothing downstream ever asked how many were left.

So the blanks now get a second, narrower ask — "you have already read this
item; what is it obviously about" — over a short list instead of thirty. These
are the rules that keep it honest:

  * it goes back for the blanks, and merges what it finds beside the first
    pass's answers rather than over them;

  * it is never shown an identifier. A UPC is read off a barcode or it is
    wrong, and "prefer an inference to a blank" is exactly the instruction
    that turns an empty box into twelve invented digits;

  * an aspect the listing already holds is not asked about again, including
    under the "Height"/"Item Height" spelling eBay and the app disagree on;

  * a value that is illegal for its aspect is still dropped, second pass or
    not; and

  * it can be turned off (SPECIFICS_COVERAGE=0), because it costs a vision
    call per listing and a seller's AI budget is real money.
"""
from __future__ import annotations

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from backend import main  # noqa: E402
from backend.models import ItemSpecific, Listing  # noqa: E402
from backend.services import claude_ai, taxonomy  # noqa: E402


def _aspect(name, *, values=None, multi=False, mode="SELECTION_ONLY",
            required=False, data_type="STRING"):
    return {"name": name, "required": required, "mode": mode,
            "values": list(values or []),
            "cardinality": "MULTI" if multi else "SINGLE",
            "data_type": data_type, "format": "", "max_length": 0}


# The five from the seller's screenshot, plus the identifier that must never
# be filled by inference and a free-text aspect the first pass answered.
SUBJECT = _aspect("Subject", values=["Bowling", "Golf", "Floral"])
ERA = _aspect("Era", values=["Mid 20th Century (1941-1969)", "1970s"])
OCCASION = _aspect("Occasion", values=["All Occasions", "Christmas"])
PACKAGING = _aspect("Packaging", values=["Unboxed", "Boxed"])
CHARACTER = _aspect("Character", mode="FREE_TEXT")
UPC = _aspect("UPC", mode="FREE_TEXT")
MATERIAL = _aspect("Material", values=["Ceramic", "Glass"])

CATEGORY = [SUBJECT, ERA, OCCASION, PACKAGING, CHARACTER, UPC, MATERIAL]


# ------------------------------------------------------- which blanks it sees

def test_the_specifics_ebay_suggested_are_the_ones_it_goes_back_for():
    listing = Listing(title="Vintage bowling trophy",
                      item_specifics=[ItemSpecific(name="Material",
                                                   value="Ceramic")])
    blanks = [a["name"] for a in taxonomy.fillable_blanks(listing, CATEGORY)]
    assert blanks == ["Subject", "Era", "Occasion", "Packaging", "Character"]


def test_an_identifier_is_never_offered_for_inference():
    """"Prefer a defensible inference to a blank" and an empty UPC box in the
    same prompt is how a model talks itself into twelve digits. It is not that
    the pass is told to leave identifiers alone — it is never shown them."""
    listing = Listing(title="Vintage bowling trophy")
    assert "UPC" not in [a["name"]
                         for a in taxonomy.fillable_blanks(listing, CATEGORY)]


@pytest.mark.parametrize("name", [
    "UPC", "EAN", "ISBN", "GTIN", "MPN", "Manufacturer Part Number",
    "Serial Number", "Model Number", "Style Number", "Card Number",
])
def test_every_identifier_shaped_aspect_is_out(name):
    assert taxonomy.is_identifier_aspect(name)


@pytest.mark.parametrize("name", [
    "Model", "Style", "Card Name", "Year Manufactured", "Subject",
    "Character", "Era", "Occasion", "Packaging",
])
def test_the_describable_ones_are_not_mistaken_for_identifiers(name):
    """The rule catches the family by shape ("...Number"), so it has to leave
    the plain names sitting beside them alone."""
    assert not taxonomy.is_identifier_aspect(name)


def test_an_aspect_already_answered_is_not_asked_about_twice():
    listing = Listing(title="Trophy",
                      item_specifics=[ItemSpecific(name="Subject",
                                                   value="Bowling")])
    assert "Subject" not in [a["name"]
                             for a in taxonomy.fillable_blanks(listing, CATEGORY)]


def test_the_height_spelling_the_app_and_ebay_disagree_on_counts_as_filled():
    """eBay publishes "Item Height"; the identify pass writes "Height".
    sanitize_specifics already treats them as one aspect, and a coverage check
    that did not would keep asking for a specific the listing plainly holds."""
    listing = Listing(title="Vase",
                      item_specifics=[ItemSpecific(name="Height", value="7 in")])
    blanks = taxonomy.fillable_blanks(listing, [_aspect("Item Height",
                                                        mode="FREE_TEXT")])
    assert blanks == []


def test_a_blank_row_left_behind_does_not_count_as_an_answer():
    listing = Listing(title="Trophy",
                      item_specifics=[ItemSpecific(name="Subject", value="  ")])
    assert "Subject" in [a["name"]
                         for a in taxonomy.fillable_blanks(listing, CATEGORY)]


def test_the_brand_field_answers_the_brand_aspect():
    """identify and the maker double-check write the maker to listing.brand,
    not to a specifics row — so a Brand aspect is not blank when it is set."""
    listing = Listing(title="Trophy", brand="Brunswick")
    assert taxonomy.fillable_blanks(
        listing, [_aspect("Brand", mode="FREE_TEXT")]) == []


# ------------------------------------------------------------ what it is told

def test_the_prompt_names_the_specifics_that_ship_blank():
    """Subject, Era, Occasion, Packaging and Character are what eBay's own
    suggester offered on the listing this app had just written. Naming them,
    and saying what each one means, is the difference between a model that
    skips them and one that answers them."""
    schema = claude_ai._COVERAGE_SCHEMA
    for name in ("Subject", "Era", "Occasion", "Packaging", "Character"):
        assert name in schema
    assert "All Occasions" in schema     # Occasion has a real default answer
    assert "Unboxed" in schema           # Packaging is answered from the photo


def test_the_prompt_still_refuses_the_placeholders():
    """A pass told to prefer an answer to a blank is a pass that will reach
    for "Unknown" — which is worse than the blank, because it publishes."""
    schema = claude_ai._COVERAGE_SCHEMA
    assert "Not Specified" in schema and "Does Not Apply" in schema
    assert "NEVER fill an aspect with" in schema


def test_it_carries_what_the_first_pass_already_settled():
    """The second ask is not a second identify: it argues from the finished
    listing, so its answers agree with what the seller is about to publish."""
    listing = Listing(title="Vintage bowling trophy", brand="Brunswick",
                      item_specifics=[ItemSpecific(name="Material",
                                                   value="Ceramic")])
    context = claude_ai._coverage_context(listing)
    assert "Material: Ceramic" in context
    assert "do not repeat these" in context


# ------------------------------------------------------------- what comes back

def _stub_coverage(monkeypatch, specifics):
    seen = {}

    def fake(image_paths, listing, blanks):
        seen["blanks"] = [a["name"] for a in blanks]
        return claude_ai._validate_specifics({"specifics": specifics}, blanks)

    monkeypatch.setattr(main.claude_ai, "fill_missing_aspects", fake)
    return seen


def test_the_second_look_fills_what_the_first_pass_skipped(monkeypatch, tmp_path):
    photo = tmp_path / "1.jpg"
    photo.write_bytes(b"x")
    listing = Listing(title="Vintage bowling trophy",
                      item_specifics=[ItemSpecific(name="Material",
                                                   value="Ceramic",
                                                   confidence="high")])
    seen = _stub_coverage(monkeypatch, [
        {"name": "Subject", "value": "Bowling", "confidence": "medium"},
        {"name": "Era", "value": "Mid 20th Century (1941-1969)",
         "confidence": "medium"},
        {"name": "Occasion", "value": "All Occasions", "confidence": "medium"},
        {"name": "Packaging", "value": "Unboxed", "confidence": "high"},
        {"name": "Character", "value": "Bowler", "confidence": "medium"},
    ])

    added = main._cover_remaining_specifics(listing, [photo], CATEGORY)

    assert added == 5
    assert "UPC" not in seen["blanks"]
    held = {s.name: s.value for s in listing.item_specifics}
    assert held["Subject"] == "Bowling"
    assert held["Era"] == "Mid 20th Century (1941-1969)"
    assert held["Occasion"] == "All Occasions"
    assert held["Packaging"] == "Unboxed"
    assert held["Character"] == "Bowler"
    # And it did not touch what the first pass had already read off the item.
    assert held["Material"] == "Ceramic"


def test_it_never_overwrites_what_the_seller_answered(monkeypatch, tmp_path):
    photo = tmp_path / "1.jpg"
    photo.write_bytes(b"x")
    # confidence "" is the seller's own hand (see models.ItemSpecific).
    listing = Listing(title="Trophy",
                      item_specifics=[ItemSpecific(name="Subject",
                                                   value="Golf",
                                                   confidence="")])
    _stub_coverage(monkeypatch, [
        {"name": "Subject", "value": "Bowling", "confidence": "medium"}])

    main._cover_remaining_specifics(listing, [photo], CATEGORY)

    assert [s.value for s in listing.item_specifics] == ["Golf"]


def test_a_value_that_is_not_on_ebays_list_is_still_dropped(monkeypatch, tmp_path):
    """The second pass is more willing to answer, not more willing to be
    wrong: a fixed-choice value eBay does not publish is dropped exactly as it
    would be on the first pass, and the box stays empty."""
    photo = tmp_path / "1.jpg"
    photo.write_bytes(b"x")
    listing = Listing(title="Trophy")
    _stub_coverage(monkeypatch, [
        {"name": "Era", "value": "Some time in the past", "confidence": "medium"},
        {"name": "Subject", "value": "Bowling", "confidence": "medium"}])

    added = main._cover_remaining_specifics(listing, [photo], CATEGORY)

    assert added == 1
    assert [s.name for s in listing.item_specifics] == ["Subject"]


def test_one_leftover_is_not_worth_a_round_trip(monkeypatch, tmp_path):
    photo = tmp_path / "1.jpg"
    photo.write_bytes(b"x")
    listing = Listing(title="Trophy")

    def never(*a, **k):
        raise AssertionError("a whole vision call for one empty box")

    monkeypatch.setattr(main.claude_ai, "fill_missing_aspects", never)
    assert main._cover_remaining_specifics(listing, [photo], [SUBJECT]) == 0


def test_it_can_be_turned_off(monkeypatch, tmp_path):
    photo = tmp_path / "1.jpg"
    photo.write_bytes(b"x")
    listing = Listing(title="Trophy")

    def never(*a, **k):
        raise AssertionError("SPECIFICS_COVERAGE=0 still made the call")

    monkeypatch.setattr(main.claude_ai, "fill_missing_aspects", never)
    monkeypatch.setenv("SPECIFICS_COVERAGE", "0")
    assert main._cover_remaining_specifics(listing, [photo], CATEGORY) == 0


def test_a_failure_in_the_second_look_never_costs_the_draft(monkeypatch, tmp_path):
    """This runs between a finished draft and the seller. A complete specifics
    grid is not worth a listing."""
    photo = tmp_path / "1.jpg"
    photo.write_bytes(b"x")
    listing = Listing(title="Trophy",
                      item_specifics=[ItemSpecific(name="Material",
                                                   value="Ceramic")])

    def boom(*a, **k):
        raise RuntimeError("the model was cut off")

    monkeypatch.setattr(main.claude_ai, "fill_missing_aspects", boom)
    assert main._cover_remaining_specifics(listing, [photo], CATEGORY) == 0
    assert [s.value for s in listing.item_specifics] == ["Ceramic"]


# ----------------------------------------------- counting the store's blanks

def _record(rid, category_id="112581", status="published", **listing):
    return {"id": rid, "status": status,
            "listing": {"title": "Trophy", "category_id": category_id,
                        **listing}}


def test_the_dashboard_counts_what_each_listing_is_missing(monkeypatch):
    monkeypatch.setattr(main.taxonomy, "cached_item_aspects",
                        lambda cid, marketplace_id=None: {"aspects": CATEGORY})

    counts = main._blank_specifics_by_id([
        _record("A"),
        _record("B", item_specifics=[{"name": "Subject", "value": "Bowling"}]),
    ])

    # Six aspects the fill can answer (UPC is an identifier and never counts).
    assert counts["A"] == 6
    assert counts["B"] == 5


def test_a_listing_the_fill_has_run_on_is_not_counted(monkeypatch):
    """It cannot earn the recommendation, so looking its category up would
    spend a shared eBay allowance on an answer nobody reads."""
    monkeypatch.setattr(main.taxonomy, "cached_item_aspects",
                        lambda cid, marketplace_id=None: {"aspects": CATEGORY})
    counts = main._blank_specifics_by_id(
        [_record("A", enriched_at="2026-09-04T12:00:00+00:00")])
    assert counts == {}


def test_a_draft_is_not_counted(monkeypatch):
    monkeypatch.setattr(main.taxonomy, "cached_item_aspects",
                        lambda cid, marketplace_id=None: {"aspects": CATEGORY})
    assert main._blank_specifics_by_id([_record("A", status="draft")]) == {}


def test_the_live_lookups_one_dashboard_load_may_make_are_capped(monkeypatch):
    """The Taxonomy API runs on ONE allowance shared by every seller of this
    app. A dashboard that fetched aspects for a whole store's worth of
    categories would spend everyone's quota on a screen nobody asked a
    question on."""
    monkeypatch.setattr(main.taxonomy, "cached_item_aspects",
                        lambda cid, marketplace_id=None: None)
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    calls = []

    def fetch(cid, marketplace_id=None):
        calls.append(cid)
        return {"aspects": CATEGORY}

    monkeypatch.setattr(main.taxonomy, "item_aspects", fetch)
    records = [_record(f"L{i}", category_id=str(i)) for i in range(40)]

    main._blank_specifics_by_id(records)

    assert len(calls) == main._INSIGHTS_ASPECT_LOOKUPS


def test_what_is_already_cached_is_always_free(monkeypatch):
    """The budget is for calls, not for answers: a category whose aspects are
    already in hand costs nothing and is never rationed."""
    monkeypatch.setattr(main.taxonomy, "cached_item_aspects",
                        lambda cid, marketplace_id=None: {"aspects": CATEGORY})

    def never(*a, **k):
        raise AssertionError("a live taxonomy call for a cached category")

    monkeypatch.setattr(main.taxonomy, "item_aspects", never)
    records = [_record(f"L{i}", category_id=str(i)) for i in range(40)]

    assert len(main._blank_specifics_by_id(records)) == 40


def test_the_biggest_categories_are_looked_up_first(monkeypatch):
    """One lookup that answers for forty listings is worth more than one that
    answers for a single listing."""
    monkeypatch.setattr(main.taxonomy, "cached_item_aspects",
                        lambda cid, marketplace_id=None: None)
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    calls = []

    def fetch(cid, marketplace_id=None):
        calls.append(cid)
        return {"aspects": CATEGORY}

    monkeypatch.setattr(main.taxonomy, "item_aspects", fetch)
    records = ([_record(f"big{i}", category_id="BIG") for i in range(5)]
               + [_record(f"L{i}", category_id=str(i))
                  for i in range(main._INSIGHTS_ASPECT_LOOKUPS + 5)])

    main._blank_specifics_by_id(records)

    assert calls[0] == "BIG"


def test_a_taxonomy_failure_leaves_the_count_unknown(monkeypatch):
    """Absence of a count is not evidence that nothing is blank — the
    recommendation falls back to the notes rather than going quiet."""
    monkeypatch.setattr(main.taxonomy, "cached_item_aspects",
                        lambda cid, marketplace_id=None: None)
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("eBay is down")

    monkeypatch.setattr(main.taxonomy, "item_aspects", boom)
    assert main._blank_specifics_by_id([_record("A")]) == {}

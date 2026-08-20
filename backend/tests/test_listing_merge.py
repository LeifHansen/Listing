"""What survives when duplicate drafts are consolidated into one.

A merge deletes listings, so the rules that matter here are about not losing
a seller's writing: the master's values hold unless the seller says otherwise,
a blank on the master is filled rather than argued about, and every field two
drafts genuinely disagree about is reported so it can be asked about instead
of silently resolved.
"""
from __future__ import annotations

from backend.services import listing_merge


def draft(**fields) -> dict:
    """A listing record's data, with the model's own defaults for the fields
    every draft carries — those defaults are what make "the drafts agree"
    the common case."""
    base = {"title": "", "subtitle": "", "brand": "", "condition": "USED_EXCELLENT",
            "condition_description": "", "category_id": "", "category_suggestion": "",
            "description": "", "price": None, "purchase_price": None, "quantity": 1,
            "listing_format": "FIXED_PRICE", "auction_start_price": None,
            "auction_duration": "DAYS_7", "package_weight_lb": 0.0,
            "package_weight_oz": 0.0, "package_length_in": 0.0,
            "package_width_in": 0.0, "package_height_in": 0.0,
            "currency": "USD", "item_specifics": [], "images": []}
    base.update(fields)
    return base


def keys(reported) -> list[str]:
    return [f["key"] for f in reported]


def by_key(reported, key) -> dict:
    return next(f for f in reported if f["key"] == key)


# ------------------------------------------------------------ what gets asked

def test_the_same_entry_on_both_drafts_is_not_a_question():
    both = [("a", draft(title="Nike Air Max 90", price=60.0)),
            ("b", draft(title="Nike Air Max 90", price=60.0))]
    assert listing_merge.review(both) == {"conflicts": [], "auto_filled": []}


def test_different_entries_are_reported_with_both_values():
    found = listing_merge.review([
        ("a", draft(title="Nike Air Max 90")),
        ("b", draft(title="Nike Air Max 90 Mens Size 10")),
    ])
    assert keys(found["conflicts"]) == ["title"]
    title = by_key(found["conflicts"], "title")
    assert [o["display"] for o in title["options"]] == [
        "Nike Air Max 90", "Nike Air Max 90 Mens Size 10"]
    assert title["default"] == "a", "the master's own value is what wins by default"


def test_the_master_is_whichever_draft_comes_first():
    """Same two drafts, other way round — the picker follows the master."""
    pair = [("a", draft(title="Short")), ("b", draft(title="Longer and better"))]
    assert by_key(listing_merge.review(pair)["conflicts"], "title")["default"] == "a"
    flipped = listing_merge.review(list(reversed(pair)))
    assert by_key(flipped["conflicts"], "title")["default"] == "b"
    assert [o["listing_id"] for o in by_key(flipped["conflicts"], "title")["options"]] == ["b", "a"]


def test_a_blank_on_the_master_is_filled_in_not_argued_about():
    found = listing_merge.review([("a", draft(brand="")), ("b", draft(brand="Nike"))])
    assert found["conflicts"] == []
    assert by_key(found["auto_filled"], "brand") == {
        "key": "brand", "label": "Brand", "kind": "text",
        "listing_id": "b", "display": "Nike"}


def test_duplicates_that_disagree_over_a_blank_master_still_get_asked():
    """Nothing on the master to prefer, so the seller picks between the two."""
    found = listing_merge.review([
        ("a", draft(price=None)), ("b", draft(price=60.0)), ("c", draft(price=45.0))])
    price = by_key(found["conflicts"], "price")
    assert [o["display"] for o in price["options"]] == ["$60.00", "$45.00"]
    assert price["default"] == "b", "first duplicate with a value, until the seller says"


def test_a_field_only_the_master_filled_in_is_left_out_entirely():
    found = listing_merge.review([("a", draft(description="Worn twice.")),
                                  ("b", draft(description=""))])
    assert found == {"conflicts": [], "auto_filled": []}


def test_repeated_values_across_drafts_are_offered_once():
    found = listing_merge.review([("a", draft(brand="Nike")), ("b", draft(brand="Adidas")),
                                  ("c", draft(brand="Adidas"))])
    assert [o["listing_id"] for o in by_key(found["conflicts"], "brand")["options"]] == ["a", "b"]


def test_whitespace_and_case_are_not_a_disagreement():
    found = listing_merge.review([
        ("a", draft(description="Barely worn.\n\nSmoke free home.", brand="Nike")),
        ("b", draft(description="Barely worn. Smoke free home.", brand="NIKE"))])
    assert found["conflicts"] == []


def test_an_empty_price_and_a_zero_price_are_both_nothing():
    """A $0 draft is an unanswered box, not a seller pricing an item at zero."""
    assert listing_merge.review([("a", draft(price=0)), ("b", draft(price=0.0))]) == {
        "conflicts": [], "auto_filled": []}
    found = listing_merge.review([("a", draft(price=0)), ("b", draft(price=45.0))])
    assert by_key(found["auto_filled"], "price")["display"] == "$45.00"


def test_the_same_category_worded_differently_is_the_same_category():
    found = listing_merge.review([
        ("a", draft(category_id="15709", category_suggestion="Athletic Shoes")),
        ("b", draft(category_id="15709", category_suggestion="Men's Athletic Shoes"))])
    assert found["conflicts"] == []


def test_different_categories_are_reported_with_the_id_shown():
    found = listing_merge.review([
        ("a", draft(category_id="15709", category_suggestion="Athletic Shoes")),
        ("b", draft(category_id="93427", category_suggestion="Sneakers"))])
    assert [o["display"] for o in by_key(found["conflicts"], "category")["options"]] == [
        "Athletic Shoes (15709)", "Sneakers (93427)"]


def test_condition_and_format_are_shown_the_way_the_editor_shows_them():
    found = listing_merge.review([
        ("a", draft(condition="USED_EXCELLENT", listing_format="FIXED_PRICE")),
        ("b", draft(condition="NEW_WITH_DEFECTS", listing_format="AUCTION"))])
    assert [o["display"] for o in by_key(found["conflicts"], "condition")["options"]] == [
        "Used — excellent", "New with defects"]
    assert [o["display"] for o in by_key(found["conflicts"], "listing_format")["options"]] == [
        "Buy It Now", "Auction"]


def test_a_package_is_weighed_as_one_field():
    """Pounds and ounces are one answer — 2 lb 4 oz, not two arguments."""
    found = listing_merge.review([
        ("a", draft(package_weight_lb=2.0, package_weight_oz=4.0)),
        ("b", draft(package_weight_lb=1.0, package_weight_oz=0.0))])
    assert [o["display"] for o in by_key(found["conflicts"], "package_weight")["options"]] == [
        "2 lb 4 oz", "1 lb"]


def test_half_measured_dimensions_count_as_unmeasured():
    """eBay's calculated shipping wants all three sides or none."""
    found = listing_merge.review([
        ("a", draft(package_length_in=10.0, package_width_in=6.0)),
        ("b", draft(package_length_in=10.0, package_width_in=6.0, package_height_in=4.0))])
    assert by_key(found["auto_filled"], "package_size")["display"] == "10 × 6 × 4 in"


def test_a_long_description_is_clipped_for_the_picker():
    found = listing_merge.review([("a", draft(description="A" * 50)),
                                  ("b", draft(description="B" * 2000))])
    shown = by_key(found["conflicts"], "description")["options"][1]["display"]
    assert len(shown) == listing_merge.MAX_DISPLAY and shown.endswith("…")


# ------------------------------------------------------------- item specifics

def spec(name, value, confidence="high") -> dict:
    return {"name": name, "value": value, "confidence": confidence}


def test_item_specifics_are_fields_too():
    found = listing_merge.review([
        ("a", draft(item_specifics=[spec("Size", "10")])),
        ("b", draft(item_specifics=[spec("Size", "10.5")]))])
    size = by_key(found["conflicts"], "specific:size")
    assert size["label"] == "Size"
    assert [o["display"] for o in size["options"]] == ["10", "10.5"]


def test_a_specific_only_one_duplicate_filled_in_is_carried_over():
    found = listing_merge.review([
        ("a", draft(item_specifics=[spec("Size", "10")])),
        ("b", draft(item_specifics=[spec("Size", "10"), spec("Color", "Black")]))])
    assert found["conflicts"] == []
    assert by_key(found["auto_filled"], "specific:color")["display"] == "Black"


def test_a_specifics_name_is_matched_however_it_is_capitalized():
    found = listing_merge.review([
        ("a", draft(item_specifics=[spec("Color", "Black")])),
        ("b", draft(item_specifics=[spec("COLOR", "Red")]))])
    assert keys(found["conflicts"]) == ["specific:color"]


def test_junk_in_the_specifics_list_is_stepped_over():
    found = listing_merge.review([
        ("a", draft(item_specifics=["not a dict", {"value": "no name"}, spec("Size", "10")])),
        ("b", draft(item_specifics=[spec("Size", "11")]))])
    assert keys(found["conflicts"]) == ["specific:size"]


# ------------------------------------------------------------- what gets kept

def test_nothing_is_taken_from_a_duplicate_unless_it_was_asked_for():
    master = draft(title="Nike Air Max 90", price=60.0, brand="Nike")
    merged, applied = listing_merge.resolve(
        [("a", master), ("b", draft(title="Air Max", price=45.0, brand="NIKE"))])
    assert (merged["title"], merged["price"], merged["brand"]) == ("Nike Air Max 90", 60.0, "Nike")
    assert applied == []


def test_the_sellers_pick_overwrites_the_master():
    merged, applied = listing_merge.resolve(
        [("a", draft(title="Air Max", price=60.0)),
         ("b", draft(title="Nike Air Max 90 Mens Size 10", price=45.0))],
        {"title": "b"})
    assert merged["title"] == "Nike Air Max 90 Mens Size 10"
    assert merged["price"] == 60.0, "a field nobody picked keeps the master's value"
    assert applied == [{"key": "title", "label": "Title", "from": "b",
                        "display": "Nike Air Max 90 Mens Size 10", "filled": False}]


def test_blanks_fill_themselves_in_without_being_picked():
    merged, applied = listing_merge.resolve(
        [("a", draft(title="Nike Air Max 90")), ("b", draft(title="Air Max", brand="Nike"))])
    assert merged["brand"] == "Nike"
    assert [(f["key"], f["filled"]) for f in applied] == [("brand", True)]


def test_a_picked_category_moves_with_its_id():
    """Half a category — a name with someone else's id — publishes to the
    wrong place on eBay."""
    merged, _ = listing_merge.resolve(
        [("a", draft(category_id="15709", category_suggestion="Athletic Shoes")),
         ("b", draft(category_id="93427", category_suggestion="Sneakers"))],
        {"category": "b"})
    assert (merged["category_id"], merged["category_suggestion"]) == ("93427", "Sneakers")


def test_a_picked_weight_moves_pounds_and_ounces_together():
    merged, _ = listing_merge.resolve(
        [("a", draft(package_weight_lb=2.0, package_weight_oz=4.0)),
         ("b", draft(package_weight_lb=1.0, package_weight_oz=0.0))],
        {"package_weight": "b"})
    assert (merged["package_weight_lb"], merged["package_weight_oz"]) == (1.0, 0.0)


def test_a_picked_specific_replaces_the_masters_value_in_place():
    merged, _ = listing_merge.resolve(
        [("a", draft(item_specifics=[spec("Brand", "Nike"), spec("Size", "10")])),
         ("b", draft(item_specifics=[spec("Size", "10.5", confidence="")]))],
        {"specific:size": "b"})
    assert merged["item_specifics"] == [
        {"name": "Brand", "value": "Nike", "confidence": "high"},
        {"name": "Size", "value": "10.5", "confidence": ""}]


def test_a_specific_the_master_never_had_is_appended():
    merged, _ = listing_merge.resolve(
        [("a", draft(item_specifics=[spec("Size", "10")])),
         ("b", draft(item_specifics=[spec("Color", "Black")]))])
    assert [s["name"] for s in merged["item_specifics"]] == ["Size", "Color"]


def test_resolving_never_writes_back_into_the_drafts_it_read():
    """The duplicates are about to be deleted, but the master's own record is
    still live — mutating either in place would corrupt what gets saved."""
    master = draft(title="Nike Air Max 90", item_specifics=[spec("Size", "10")])
    other = draft(title="Air Max", item_specifics=[spec("Size", "10.5")])
    merged, _ = listing_merge.resolve([("a", master), ("b", other)],
                                      {"title": "b", "specific:size": "b"})
    assert master["title"] == "Nike Air Max 90"
    assert master["item_specifics"] == [spec("Size", "10")]
    assert other["item_specifics"] == [spec("Size", "10.5")]
    assert merged["item_specifics"] == [spec("Size", "10.5", confidence="high")]


def test_a_pick_no_draft_can_honour_falls_back_to_the_default():
    """A review screen left open while the drafts changed underneath must not
    be able to write a value nothing ever held."""
    pair = [("a", draft(title="Nike Air Max 90")), ("b", draft(title="Air Max"))]
    for nonsense in ({"title": "gone"}, {"title": ""}, {"title": None},
                     {"nosuchfield": "b"}, {}, None, "not a mapping"):
        merged, _ = listing_merge.resolve(pair, nonsense)
        assert merged["title"] == "Nike Air Max 90"


def test_picking_the_master_is_the_same_as_picking_nothing():
    merged, applied = listing_merge.resolve(
        [("a", draft(title="Nike Air Max 90")), ("b", draft(title="Air Max"))], {"title": "a"})
    assert merged["title"] == "Nike Air Max 90" and applied == []


def test_one_draft_on_its_own_has_nothing_to_decide():
    only = draft(title="Nike Air Max 90")
    assert listing_merge.review([("a", only)]) == {"conflicts": [], "auto_filled": []}
    merged, applied = listing_merge.resolve([("a", only)])
    assert merged["title"] == "Nike Air Max 90" and applied == []
    assert listing_merge.resolve([]) == ({}, [])


def test_the_merged_data_is_still_a_listing_the_model_accepts():
    """resolve() hands its dict straight to Listing(**data) before it is saved
    — a field copied across as the wrong type would 500 the merge."""
    from backend.models import Listing

    merged, _ = listing_merge.resolve(
        [("a", draft(title="Air Max", quantity=1)),
         ("b", draft(title="Nike Air Max 90", price=45.0, quantity=2,
                     category_id="15709", category_suggestion="Athletic Shoes",
                     package_weight_lb=2.0, package_weight_oz=4.0,
                     item_specifics=[spec("Size", "10")]))],
        {"title": "b", "quantity": "b"})
    listing = Listing(**merged)
    assert listing.title == "Nike Air Max 90"
    assert listing.quantity == 2
    assert listing.price == 45.0
    assert listing.category_id == "15709"
    assert listing.package_weight_lb == 2.0
    assert [(s.name, s.value) for s in listing.item_specifics] == [("Size", "10")]


def test_every_conflict_can_be_answered_from_the_review_it_reported():
    """The contract the two halves share: every key the review screen shows
    is a key the merge honours, and every option is a value it can write."""
    drafts = [("a", draft(title="Air Max", brand="", price=60.0,
                          item_specifics=[spec("Size", "10")])),
              ("b", draft(title="Nike Air Max 90", brand="Nike", price=45.0,
                          item_specifics=[spec("Size", "10.5")]))]
    found = listing_merge.review(drafts)
    picks = {c["key"]: c["options"][-1]["listing_id"] for c in found["conflicts"]}
    merged, applied = listing_merge.resolve(drafts, picks)
    assert {f["key"] for f in applied} == set(picks) | {"brand"}
    assert merged["title"] == "Nike Air Max 90"
    assert merged["price"] == 45.0
    assert merged["brand"] == "Nike"
    assert merged["item_specifics"] == [spec("Size", "10.5")]

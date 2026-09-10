"""A trading card's condition is two answers, and eBay refuses the second.

In the three single-card categories (Sports 261328, CCG 183454, Non-Sport
183050) eBay stopped taking "Used" in 2023. A card is Graded (2750) or
Ungraded (4000), and each REQUIRES condition descriptors: a graded card names
its grading service (27501) and grade (27502) and may give a certification
number (27503, free text); an ungraded one names its card condition (40001:
Near Mint or Better / Excellent / Very Good / Poor, or the CCG played-ness
wording). A listing that stops at "Graded" is refused outright.

This app stopped at "Graded". The condition list it fetched for the category
carried eBay's descriptors, and `item_conditions` dropped them on the floor;
nothing stored a grade, nothing sent one, and the preflight had no idea one
was missing. Every card the seller tried to list in those categories came
back refused, naming a descriptor id the app had never heard of.

These pin the whole path: eBay's answer read whole, the grade stored, sent in
the shape the Trading API wants, read back from GetItem, pruned to what the
category offers, and named in the checklist before eBay has to. And they pin
what the app must NOT do -- invent an id, or send a grade under a condition
that does not take one.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from backend.models import ConditionDescriptor, Listing
from backend.services import (dirty_fields, ebay_trading, listing_merge,
                              preflight, sync_merge, taxonomy)

# What eBay answers for Sports Trading Card Singles (261328), trimmed to the
# values these tests need. The shape is the Sell Metadata API's: each
# condition carries its descriptors, each descriptor its constraint and
# values. Ids are eBay's real ones for the graded card descriptors.
METADATA = {"itemConditionPolicies": [{"categoryId": "261328", "itemConditions": [
    {"conditionId": "2750", "conditionDescription": "Graded",
     "conditionDescriptors": [
         {"conditionDescriptorId": "27501",
          "conditionDescriptorName": "Professional Grader",
          "conditionDescriptorConstraint": {"mode": "SELECTION_ONLY",
                                            "usage": "REQUIRED",
                                            "cardinality": "SINGLE"},
          "conditionDescriptorValues": [
              {"conditionDescriptorValueId": "275010",
               "conditionDescriptorValueName": "Professional Sports Authenticator (PSA)"},
              {"conditionDescriptorValueId": "275013",
               "conditionDescriptorValueName": "Beckett Grading Services (BGS)"},
              {"conditionDescriptorValueId": "275016",
               "conditionDescriptorValueName": "Sportscard Guaranty Corporation (SGC)"},
          ]},
         {"conditionDescriptorId": "27502",
          "conditionDescriptorName": "Grade",
          "conditionDescriptorConstraint": {"mode": "SELECTION_ONLY",
                                            "usage": "REQUIRED",
                                            "cardinality": "SINGLE"},
          "conditionDescriptorValues": [
              {"conditionDescriptorValueId": "275020",
               "conditionDescriptorValueName": "10"},
              {"conditionDescriptorValueId": "275021",
               "conditionDescriptorValueName": "9.5"},
              {"conditionDescriptorValueId": "275022",
               "conditionDescriptorValueName": "9"},
          ]},
         {"conditionDescriptorId": "27503",
          "conditionDescriptorName": "Certification Number",
          "conditionDescriptorConstraint": {"mode": "FREE_TEXT",
                                            "usage": "OPTIONAL",
                                            "maxLength": 30}},
     ]},
    {"conditionId": "4000", "conditionDescription": "Ungraded",
     "conditionDescriptors": [
         {"conditionDescriptorId": "40001",
          "conditionDescriptorName": "Card Condition",
          "conditionDescriptorConstraint": {"mode": "SELECTION_ONLY",
                                            "usage": "REQUIRED",
                                            "cardinality": "SINGLE"},
          "conditionDescriptorValues": [
              {"conditionDescriptorValueId": "400010",
               "conditionDescriptorValueName": "Near Mint or Better"},
              {"conditionDescriptorValueId": "400011",
               "conditionDescriptorValueName": "Excellent"},
              {"conditionDescriptorValueId": "400012",
               "conditionDescriptorValueName": "Very Good"},
              {"conditionDescriptorValueId": "400013",
               "conditionDescriptorValueName": "Poor"},
          ]},
     ]},
]}]}


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return METADATA


def _card_conditions(monkeypatch) -> list[dict]:
    """eBay's condition list for 261328, read through the real parser."""
    monkeypatch.setattr(taxonomy.httpx, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(taxonomy, "_app_token", lambda: "t")
    taxonomy._CONDITIONS_CACHE.clear()
    try:
        return taxonomy.item_conditions("261328")["conditions"]
    finally:
        taxonomy._CONDITIONS_CACHE.clear()


def _cards() -> list[dict]:
    """eBay's answer for 261328 in the shape `item_conditions` returns it,
    built without HTTP so the checklist and publish tests need no stand-in."""
    out = []
    for pol in METADATA["itemConditionPolicies"]:
        for c in pol["itemConditions"]:
            out.append({
                "enum": taxonomy.CONDITION_ID_TO_ENUM[c["conditionId"]],
                "id": c["conditionId"],
                "label": c["conditionDescription"],
                "descriptors": taxonomy.parse_condition_descriptors(
                    c.get("conditionDescriptors")),
            })
    return out


GRADER = {"id": "27501", "values": ["275010"], "label": "Professional Grader",
          "value_labels": ["Professional Sports Authenticator (PSA)"]}
GRADE = {"id": "27502", "values": ["275020"], "label": "Grade", "value_labels": ["10"]}
CERT = {"id": "27503", "text": "12345678", "label": "Certification Number"}
NEAR_MINT = {"id": "40001", "values": ["400010"], "label": "Card Condition",
             "value_labels": ["Near Mint or Better"]}


def _card(**fields) -> Listing:
    base = {"title": "2003 Topps Chrome LeBron James Rookie #111 PSA 10",
            "condition": "LIKE_NEW",      # eBay's 2750 -- "Graded" here
            "condition_descriptors": [GRADER, GRADE, CERT],
            "price": 1499.99, "quantity": 1, "category_id": "261328",
            "description": "Gem mint.", "package_weight_lb": 1.0,
            "images": ["img_000.jpg"]}
    base.update(fields)
    return Listing(**base)


ACCOUNT_READY = {"has_fulfillment": True, "has_payment": True,
                 "has_return": True, "has_location": True, "connected": True}


# ------------------------------------------------- eBay's answer, read whole


def test_the_descriptors_come_through_with_the_conditions(monkeypatch):
    """The drop that started this: `item_conditions` named each condition
    and threw away the descriptors underneath it."""
    conditions = _card_conditions(monkeypatch)
    graded = next(c for c in conditions if c["label"] == "Graded")
    ungraded = next(c for c in conditions if c["label"] == "Ungraded")
    assert [d["name"] for d in graded["descriptors"]] == [
        "Professional Grader", "Grade", "Certification Number"]
    assert [d["name"] for d in ungraded["descriptors"]] == ["Card Condition"]
    # The ladder is eBay's, id and wording both.
    assert ungraded["descriptors"][0]["values"][0] == {
        "id": "400010", "name": "Near Mint or Better"}


def test_which_descriptors_are_required_and_which_take_free_text():
    graded = taxonomy.condition_descriptor_meta(_cards(), "LIKE_NEW")
    by_name = {d["name"]: d for d in graded}
    assert by_name["Professional Grader"]["required"]
    assert by_name["Grade"]["required"]
    assert not by_name["Professional Grader"]["free_text"]
    cert = by_name["Certification Number"]
    assert cert["free_text"] and not cert["required"]
    assert cert["max_length"] == 30


def test_a_descriptor_with_values_but_no_constraint_is_a_required_pick():
    """eBay's constraint block is optional in the response. Values with no
    word on usage are a pick-one that has to be picked; no values and no
    word is free text that need not be."""
    parsed = taxonomy.parse_condition_descriptors([
        {"conditionDescriptorId": "40001", "conditionDescriptorName": "Card Condition",
         "conditionDescriptorValues": [{"conditionDescriptorValueId": "400010",
                                        "conditionDescriptorValueName": "Near Mint or Better"}]},
        {"conditionDescriptorId": "27503", "conditionDescriptorName": "Certification Number"},
    ])
    assert parsed[0]["required"] and not parsed[0]["free_text"]
    assert parsed[1]["free_text"] and not parsed[1]["required"]


def test_every_other_category_has_no_second_step():
    """A condition list without descriptors -- every category that is not
    trading cards, and every list built before this existed -- is the one
    step it always was."""
    plain = [{"enum": "USED_EXCELLENT", "id": "3000", "label": "Used"}]
    assert taxonomy.condition_descriptor_meta(plain, "USED_EXCELLENT") == []
    assert taxonomy.condition_descriptor_problems([GRADER], []) == []


# --------------------------------------------------------------- the record


def test_the_model_takes_what_the_editor_and_the_wire_send():
    """One grade arrives as a string as often as a one-item list, an old
    record holds None, and an empty row the editor left behind is noise."""
    listing = Listing(condition_descriptors=[
        {"id": "27502", "values": "275020"},
        {"id": "", "values": []},
        {"id": "27503", "text": " 12345678 "},
    ])
    assert [d.model_dump() for d in listing.condition_descriptors] == [
        {"id": "27502", "values": ["275020"], "text": "", "label": "",
         "value_labels": []},
        {"id": "27503", "values": [], "text": "12345678", "label": "",
         "value_labels": []},
    ]
    assert Listing(condition_descriptors=None).condition_descriptors == []


# ------------------------------------------------------------ the checklist


def test_a_graded_card_without_a_grade_is_blocked_before_ebay_sees_it():
    listing = _card(condition_descriptors=[GRADER, CERT])
    issues = preflight.validate(listing, "live", **ACCOUNT_READY,
                                allowed_conditions=_cards())
    blocking = [i for i in issues if i["blocking"] and i["target"] == "condition"]
    assert blocking, "a graded card with no grade is refused by eBay"
    assert "Grade" in blocking[0]["title"]
    # It names the ladder, so the fix is a dropdown away.
    assert "10" in blocking[0]["fix"]


def test_an_ungraded_card_needs_its_card_condition():
    listing = _card(condition="USED_VERY_GOOD", condition_descriptors=[])
    issues = preflight.validate(listing, "live", **ACCOUNT_READY,
                                allowed_conditions=_cards())
    blocking = [i for i in issues if i["blocking"] and i["target"] == "condition"]
    assert blocking
    assert "Card Condition" in blocking[0]["title"]
    assert "Near Mint or Better" in blocking[0]["fix"]


def test_a_complete_card_raises_nothing():
    for listing in (_card(),
                    _card(condition_descriptors=[GRADER, GRADE]),   # cert is optional
                    _card(condition="USED_VERY_GOOD",
                          condition_descriptors=[NEAR_MINT])):
        issues = preflight.validate(listing, "live", **ACCOUNT_READY,
                                    allowed_conditions=_cards())
        assert [i for i in issues if i["target"] == "condition"] == []


def test_a_grade_ebay_does_not_list_is_blocked():
    """A value id from another category's ladder, or one eBay retired."""
    listing = _card(condition_descriptors=[
        GRADER, {"id": "27502", "values": ["999999"], "label": "Grade"}])
    issues = preflight.validate(listing, "live", **ACCOUNT_READY,
                                allowed_conditions=_cards())
    blocking = [i for i in issues if i["blocking"] and i["target"] == "condition"]
    assert blocking and "Grade" in blocking[0]["title"]


def test_not_being_able_to_ask_ebay_blocks_nothing():
    listing = _card(condition_descriptors=[])
    issues = preflight.validate(listing, "live", **ACCOUNT_READY,
                                allowed_conditions=None)
    assert [i for i in issues if i["target"] == "condition"] == []


# ---------------------------------------------------------- kept to the list


def test_switching_a_card_to_ungraded_drops_its_grade():
    """The half of a two-step answer that no longer applies is not sent."""
    meta = taxonomy.condition_descriptor_meta(_cards(), "USED_VERY_GOOD")
    assert taxonomy.fit_condition_descriptors([GRADER, GRADE, CERT], meta) == []


def test_fitting_keeps_ebays_order_and_wording():
    meta = taxonomy.condition_descriptor_meta(_cards(), "LIKE_NEW")
    fitted = taxonomy.fit_condition_descriptors(
        [{"id": "27503", "text": "12345678"},          # out of order, no labels
         {"id": "27502", "values": "275020"},
         {"id": "27501", "values": ["275010"], "label": "grader?"}],
        meta)
    assert [d["id"] for d in fitted] == ["27501", "27502", "27503"]
    assert fitted[0]["label"] == "Professional Grader"
    assert fitted[0]["value_labels"] == ["Professional Sports Authenticator (PSA)"]
    assert fitted[1]["value_labels"] == ["10"]
    assert fitted[2] == {"id": "27503", "values": [], "text": "12345678",
                         "label": "Certification Number", "value_labels": []}


def test_fitting_drops_a_value_ebay_does_not_list_and_clips_free_text():
    meta = taxonomy.condition_descriptor_meta(_cards(), "LIKE_NEW")
    fitted = taxonomy.fit_condition_descriptors(
        [GRADER, {"id": "27502", "values": ["999999"]},
         {"id": "27503", "text": "x" * 40}],
        meta)
    assert [d["id"] for d in fitted] == ["27501", "27503"]
    assert len(fitted[1]["text"]) == 30


def test_the_publish_path_prunes_a_stale_grade_but_invents_nothing(monkeypatch):
    """A draft switched to Ungraded on a card still carrying PSA 10 goes out
    with the grade removed -- and with NO card condition, because there is
    no honest one to pick; the checklist blocks it instead."""
    from backend.marketplaces import ebay_provider

    monkeypatch.setattr(ebay_provider.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(ebay_provider.taxonomy, "item_conditions",
                        lambda cid, **k: {"conditions": _cards()})
    listing = _card(condition="USED_VERY_GOOD",
                    condition_descriptors=[GRADER, GRADE, CERT])
    assert ebay_provider.fit_condition_to_category(listing) == ""
    assert listing.condition == "USED_VERY_GOOD"
    assert listing.condition_descriptors == []


def test_the_publish_path_leaves_a_complete_card_exactly_as_it_is(monkeypatch):
    from backend.marketplaces import ebay_provider

    monkeypatch.setattr(ebay_provider.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(ebay_provider.taxonomy, "item_conditions",
                        lambda cid, **k: {"conditions": _cards()})
    listing = _card()
    before = [d.model_dump() for d in listing.condition_descriptors]
    assert ebay_provider.fit_condition_to_category(listing) == ""
    assert [d.model_dump() for d in listing.condition_descriptors] == before


# ---------------------------------------------------------------- the wire


def _add_body(listing: Listing) -> str:
    return "".join(ebay_trading._item_fields(listing, None))


def test_a_new_listing_carries_its_descriptors_in_ebays_shape():
    body = _add_body(_card())
    assert "<ConditionID>2750</ConditionID>" in body
    assert ("<ConditionDescriptors>"
            "<ConditionDescriptor><Name>27501</Name><Value>275010</Value></ConditionDescriptor>"
            "<ConditionDescriptor><Name>27502</Name><Value>275020</Value></ConditionDescriptor>"
            "<ConditionDescriptor><Name>27503</Name><AdditionalInfo>12345678</AdditionalInfo>"
            "</ConditionDescriptor>"
            "</ConditionDescriptors>") in body


def test_a_listing_with_no_descriptors_sends_no_container():
    body = _add_body(_card(condition="USED_EXCELLENT", condition_descriptors=[]))
    assert "<ConditionDescriptors>" not in body


def test_a_revise_of_the_grade_carries_the_condition_with_it():
    """eBay checks a grade against the ConditionID in the same request, so
    a grade edit sends both -- and a price edit sends neither."""
    listing = _card(ebay_listing_id="110000000001", source="ebay"
                    ).mark_dirty("condition_descriptors")
    body = ebay_trading.build_revise_item(listing, "110000000001")[1]
    assert "<ConditionID>2750</ConditionID>" in body
    assert "<Name>27502</Name><Value>275020</Value>" in body

    price_only = _card(ebay_listing_id="110000000001", source="ebay"
                       ).mark_dirty("price")
    body = ebay_trading.build_revise_item(price_only, "110000000001")[1]
    assert "<ConditionID>" not in body
    assert "<ConditionDescriptors>" not in body


def test_a_grade_survives_the_trip_through_ebay():
    """GetItem hands the descriptors back in the same shape; an import must
    read them, or the next revise strips the grade off a live listing."""
    item = ET.fromstring(
        "<Item><ItemID>110000000001</ItemID><Title>Card</Title>"
        "<ConditionID>2750</ConditionID>"
        "<ConditionDescriptors>"
        "<ConditionDescriptor><Name>27501</Name><Value>275010</Value></ConditionDescriptor>"
        "<ConditionDescriptor><Name>27502</Name><Value>275020</Value></ConditionDescriptor>"
        "<ConditionDescriptor><Name>27503</Name><AdditionalInfo>12345678</AdditionalInfo>"
        "</ConditionDescriptor>"
        "</ConditionDescriptors>"
        "<PrimaryCategory><CategoryID>261328</CategoryID></PrimaryCategory>"
        "<SellingStatus><CurrentPrice>1499.99</CurrentPrice></SellingStatus>"
        "</Item>")
    data = ebay_trading._item_to_listing(item)
    assert data["condition"] == "LIKE_NEW"
    assert data["condition_descriptors"] == [
        {"id": "27501", "values": ["275010"], "text": ""},
        {"id": "27502", "values": ["275020"], "text": ""},
        {"id": "27503", "values": [], "text": "12345678"},
    ]
    # ...and goes back out byte-for-byte.
    imported = Listing(**{k: v for k, v in data.items() if k in Listing.model_fields})
    assert "<Name>27503</Name><AdditionalInfo>12345678</AdditionalInfo>" in _add_body(imported)


# ------------------------------------------------- what counts as an edit


def test_filling_in_the_labels_is_not_an_edit():
    """An import carries ids only and the editor adds eBay's wording. That
    must not read as the seller re-grading the card: it would put the
    condition into every revise and every sync conflict."""
    stored = _card(condition_descriptors=[
        {"id": "27501", "values": ["275010"]}, {"id": "27502", "values": ["275020"]},
        {"id": "27503", "text": "12345678"}]).model_dump()
    assert "condition_descriptors" not in dirty_fields.changed_fields(_card(), stored)


def test_changing_the_grade_is_an_edit():
    stored = _card().model_dump()
    regraded = _card(condition_descriptors=[
        GRADER, {**GRADE, "values": ["275021"], "value_labels": ["9.5"]}, CERT])
    assert "condition_descriptors" in dirty_fields.changed_fields(regraded, stored)


def test_a_sync_does_not_see_labels_as_a_disagreement():
    local = _card()
    remote = {**_card().model_dump(),
              "condition_descriptors": [{"id": "27501", "values": ["275010"]},
                                        {"id": "27502", "values": ["275020"]},
                                        {"id": "27503", "text": "12345678"}]}
    shadow = dict(remote)
    out = sync_merge.three_way(local, shadow, remote)
    assert "condition_descriptors" not in out.conflicts
    assert "condition_descriptors" not in out.took_remote
    assert "condition_descriptors" not in out.kept_local


# -------------------------------------------------------- said in words


def test_a_merge_shows_the_grade_as_a_seller_would_say_it():
    key, shown = listing_merge._descriptors([GRADER, GRADE, CERT])
    assert shown == "Professional Sports Authenticator (PSA) · 10 · #12345678"
    # Same ids without labels (an import) compare equal to the labelled copy.
    bare, _ = listing_merge._descriptors([
        {"id": "27501", "values": ["275010"]}, {"id": "27502", "values": ["275020"]},
        {"id": "27503", "text": "12345678"}])
    assert bare == key
    assert listing_merge._descriptors([]) == ("", "")


def test_the_condition_descriptor_type_is_exported():
    assert ConditionDescriptor(id="27502", values=["275020"]).values == ["275020"]

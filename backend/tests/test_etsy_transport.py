"""Etsy request encoding: the v3 listing endpoints are form-urlencoded.

Posting JSON to createDraftListing/updateListing gets a 400 on every call, and
Etsy has no sandbox to catch it before production — so the encoding is pinned
here, against the real mapping output.
"""
from backend.marketplaces import mapping_etsy
from backend.models import Listing
from backend.services import etsy


def _payload(**kw):
    base = dict(
        title="Vintage Levi's 501 Jeans", description="Great pair.",
        price=45.0, quantity=2, images=["a.jpg"],
        etsy={"taxonomy_id": 1234, "who_made": "someone_else",
              "when_made": "1990s", "shipping_profile_id": "77",
              "tags": ["denim", "vintage"], "materials": ["cotton"]},
    )
    base.update(kw)
    return mapping_etsy.build_listing_payload(Listing(**base), {})


def test_every_value_is_a_string():
    """httpx form-encodes `data`, which rejects non-str scalars like ints."""
    body = etsy.form_body(_payload())
    assert body, "payload should not encode to nothing"
    for key, value in body.items():
        assert isinstance(value, str), f"{key} -> {value!r} is not a str"


def test_scalars_survive_round_trip():
    body = etsy.form_body(_payload())
    assert body["title"] == "Vintage Levi's 501 Jeans"
    assert body["price"] == "45.0"
    assert body["quantity"] == "2"
    assert body["taxonomy_id"] == "1234"
    assert body["shipping_profile_id"] == "77"


def test_list_fields_are_comma_joined():
    """Etsy takes repeated-value fields as one comma-separated string."""
    body = etsy.form_body(_payload())
    assert body["tags"] == "denim,vintage"
    assert body["materials"] == "cotton"


def test_bools_are_lowercase_words():
    # "False" (Python's str()) is truthy to Etsy; "false" is what it parses.
    assert etsy.form_body({"is_supply": False})["is_supply"] == "false"
    assert etsy.form_body({"is_supply": True})["is_supply"] == "true"


def test_none_is_dropped_not_stringified():
    assert "return_policy_id" not in etsy.form_body({"return_policy_id": None})


def test_empty_list_is_dropped():
    """An empty tags list must not send tags="" and wipe existing tags."""
    assert "tags" not in etsy.form_body({"tags": []})


def test_state_patch_encodes():
    """The go-live PATCH is the smallest body we send."""
    assert etsy.form_body({"state": "active"}) == {"state": "active"}

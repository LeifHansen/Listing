"""Listing -> Etsy payload mapping and Etsy preflight rules (pure module)."""
from backend.marketplaces import mapping_etsy
from backend.models import ItemSpecific, Listing


def _listing(**kw):
    base = dict(
        title="Vintage Levi's 501 Jeans 32x30 Dark Wash",
        description="<p>Great pair.</p><p>No flaws.</p>",
        price=45.0, quantity=1, condition="USED_EXCELLENT",
        images=["a.jpg"],
        etsy={"taxonomy_id": 1234, "who_made": "someone_else",
              "when_made": "1990s", "shipping_profile_id": "77"},
    )
    base.update(kw)
    return Listing(**base)


def _errors(issues):
    return {i["target"] for i in issues if i["level"] == "error"}


# --- payload -----------------------------------------------------------------

def test_payload_core_fields():
    p = mapping_etsy.build_listing_payload(_listing(), {})
    assert p["title"].startswith("Vintage")
    assert p["price"] == 45.0
    assert p["quantity"] == 1
    assert p["who_made"] == "someone_else"
    assert p["when_made"] == "1990s"
    assert p["taxonomy_id"] == 1234
    assert p["shipping_profile_id"] == 77


def test_description_html_stripped_and_condition_appended():
    p = mapping_etsy.build_listing_payload(_listing(), {})
    assert "<p>" not in p["description"]
    assert "Great pair.\n\nNo flaws." in p["description"]
    assert "Condition: Used Excellent" in p["description"]


def test_settings_default_used_when_listing_has_no_profile():
    lst = _listing(etsy={"taxonomy_id": 1, "who_made": "i_did",
                         "when_made": "made_to_order"})
    p = mapping_etsy.build_listing_payload(lst, {"shipping_profile_id": "88"})
    assert p["shipping_profile_id"] == 88
    # The listing-level override wins over the account default.
    p2 = mapping_etsy.build_listing_payload(_listing(), {"shipping_profile_id": "88"})
    assert p2["shipping_profile_id"] == 77


def test_weight_converted_to_oz():
    p = mapping_etsy.build_listing_payload(
        _listing(package_weight_lb=1, package_weight_oz=4), {})
    assert p["item_weight"] == 20.0
    assert p["item_weight_unit"] == "oz"


# --- tags --------------------------------------------------------------------

def test_tags_capped_deduped_and_sanitized():
    lst = _listing(
        brand="Levi's",
        etsy={"taxonomy_id": 1, "who_made": "someone_else", "when_made": "1990s",
              "tags": ["denim", "denim", "x" * 25, "vintage denim!!"]},
        item_specifics=[ItemSpecific(name=f"Spec{i}", value=f"value {i}")
                        for i in range(15)],
    )
    tags = mapping_etsy.build_tags(lst)
    assert len(tags) <= mapping_etsy.TAG_LIMIT
    assert len(set(t.lower() for t in tags)) == len(tags)
    assert all(len(t) <= mapping_etsy.TAG_CHAR_LIMIT for t in tags)
    assert "x" * 25 not in tags            # too long -> dropped, not truncated
    assert "vintage denim" in tags         # punctuation stripped
    assert "Levi's" in tags                # apostrophes allowed


def test_materials_from_item_specifics():
    lst = _listing(item_specifics=[
        ItemSpecific(name="Material", value="Cotton"),
        ItemSpecific(name="Color", value="Blue")])
    assert mapping_etsy.build_materials(lst) == ["Cotton"]


# --- preflight ---------------------------------------------------------------

def test_preflight_clean_listing_passes():
    assert _errors(mapping_etsy.preflight(_listing(), {})) == set()


def test_preflight_auction_rejected():
    issues = mapping_etsy.preflight(_listing(listing_format="AUCTION"), {})
    assert "format" in _errors(issues)


def test_preflight_price_floor():
    issues = mapping_etsy.preflight(_listing(price=0.10), {})
    assert "price" in _errors(issues)


def test_preflight_missing_etsy_fields_are_targeted():
    lst = _listing(etsy={})
    targets = _errors(mapping_etsy.preflight(lst, {}))
    assert {"etsy_taxonomy", "etsy_attribution", "etsy_shipping_profile"} <= targets


def test_preflight_shipping_profile_fallback_order():
    lst = _listing(etsy={"taxonomy_id": 1, "who_made": "i_did",
                         "when_made": "made_to_order"})
    # No listing override, no account default -> error.
    assert "etsy_shipping_profile" in _errors(mapping_etsy.preflight(lst, {}))
    # Account default satisfies it.
    assert "etsy_shipping_profile" not in _errors(
        mapping_etsy.preflight(lst, {"shipping_profile_id": "9"}))


def test_preflight_photos_required_but_remote_urls_count():
    lst = _listing(images=[], image_urls=[])
    assert "photos" in _errors(mapping_etsy.preflight(lst, {}))
    imported = _listing(images=[], image_urls=["https://i.ebayimg.com/x.jpg"])
    assert "photos" not in _errors(mapping_etsy.preflight(imported, {}))

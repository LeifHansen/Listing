"""Listing -> Depop product mapping and Depop preflight rules (pure module)."""
from backend.marketplaces import mapping_depop
from backend.models import ItemSpecific, Listing

# Every condition the app can emit: the AI/taxonomy vocabulary plus the
# aliases the eBay Trading layer speaks. A condition missing from
# CONDITION_MAP would silently drop off the Depop payload, so totality is
# asserted here against a frozen copy of that vocabulary.
APP_CONDITIONS = [
    "NEW", "NEW_OTHER", "NEW_WITH_DEFECTS", "CERTIFIED_REFURBISHED",
    "SELLER_REFURBISHED", "LIKE_NEW", "PRE_OWNED_EXCELLENT", "USED_EXCELLENT",
    "USED_VERY_GOOD", "USED_GOOD", "PRE_OWNED_FAIR", "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING",
]


def _listing(**kw):
    base = dict(
        title="Vintage Levi's 501 Jeans 32x30 Dark Wash Made in USA Excellent",
        description="Great pair.", price=45.0, quantity=1,
        condition="USED_EXCELLENT", brand="Levi's", images=["a.jpg", "b.jpg"],
    )
    base.update(kw)
    return Listing(**base)


def _errors(issues):
    return {i["target"] for i in issues if i["level"] == "error"}


def test_condition_map_total_over_app_vocabulary():
    for cond in APP_CONDITIONS:
        assert cond in mapping_depop.CONDITION_MAP, f"unmapped condition {cond}"


def test_title_word_boundary_truncation():
    long = "Amazing Vintage Hand Knitted Wool Sweater With Extremely Long Descriptive Name"
    cut = mapping_depop.truncate_title(long)
    assert len(cut) <= mapping_depop.TITLE_LIMIT
    assert not cut.endswith(" ")
    assert long.startswith(cut)          # a clean prefix...
    assert long[len(cut)] == " "         # ...cut exactly at a word boundary


def test_title_single_giant_word_hard_cut():
    cut = mapping_depop.truncate_title("x" * 100)
    assert cut == "x" * mapping_depop.TITLE_LIMIT


def test_short_title_untouched():
    assert mapping_depop.truncate_title("Nice hat") == "Nice hat"


def test_payload_core_fields_and_condition_translation():
    p = mapping_depop.build_product_payload(_listing())
    assert p["condition"] == "excellent"
    assert p["brand"] == "Levi's"
    assert p["price"] == 45.0
    assert len(p["title"]) <= mapping_depop.TITLE_LIMIT


def test_size_explicit_beats_item_specific():
    lst = _listing(depop={"size": "W 32"},
                   item_specifics=[ItemSpecific(name="Size", value="32x30")])
    assert mapping_depop.size_for(lst) == "W 32"
    lst2 = _listing(item_specifics=[ItemSpecific(name="Size", value="32x30")])
    assert mapping_depop.size_for(lst2) == "32x30"


def test_preflight_clean_listing_passes():
    assert _errors(mapping_depop.preflight(_listing())) == set()


def test_preflight_auction_rejected():
    issues = mapping_depop.preflight(_listing(listing_format="AUCTION"))
    assert "format" in _errors(issues)


def test_preflight_multi_quantity_warns_not_blocks():
    issues = mapping_depop.preflight(_listing(quantity=3))
    assert "quantity" not in _errors(issues)
    assert any(i["target"] == "quantity" and i["level"] == "warn" for i in issues)


def test_preflight_missing_price_and_photos():
    issues = mapping_depop.preflight(_listing(price=None, images=[]))
    assert {"price", "photos"} <= _errors(issues)

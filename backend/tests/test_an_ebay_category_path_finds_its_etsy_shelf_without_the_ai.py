"""eBay's category path usually names the same shelf Etsy does.

A crosspost of two hundred listings that asked a model for every category
would cost two hundred calls and a minute of the seller's time for answers
that are mostly already written down: eBay files a tee under
"… > Men's Clothing > T-Shirts" and Etsy under "… > Shirts & Tees >
T-shirts". The match is deliberately strict — every word of eBay's LAST
segment has to appear in the Etsy path — so a vague leaf matches nothing
and falls through to the shortlist and the model, rather than filing the
item somewhere confident and wrong.
"""
import pytest

from backend.services import etsy


@pytest.fixture(autouse=True)
def tree(monkeypatch):
    nodes = [
        {"id": 1, "name": "Clothing", "children": [
            {"id": 11, "name": "Men's Clothing", "children": [
                {"id": 111, "name": "Shirts & Tees", "children": [
                    {"id": 1111, "name": "T-shirts"},
                    {"id": 1112, "name": "Polo shirts"}]},
                {"id": 112, "name": "Jeans"}]}]},
        {"id": 2, "name": "Home & Living", "children": [
            {"id": 21, "name": "Kitchen & Dining", "children": [
                {"id": 211, "name": "Drink & Barware", "children": [
                    {"id": 2111, "name": "Mugs"}]}]}]},
        {"id": 3, "name": "Art & Collectibles", "children": [
            {"id": 31, "name": "Collectibles"}]},
    ]
    monkeypatch.setattr(etsy, "taxonomy_nodes", lambda: nodes)
    etsy._TAXONOMY_CACHE.update(at=1.0, nodes=nodes)
    etsy._INDEX_CACHE.update(at=0.0, paths=None)
    etsy._EBAY_PATH_CACHE.clear()
    return nodes


def test_the_leaf_is_what_matches():
    found = etsy.taxonomy_from_ebay_path(
        "Clothing, Shoes & Accessories > Men > Men's Clothing > T-Shirts")
    assert found["id"] == 1111
    assert found["path"].endswith("T-shirts")


def test_the_rest_of_the_path_breaks_a_tie():
    """"Mugs" alone would fit anywhere the word appears; the segments above
    it are what choose between them."""
    found = etsy.taxonomy_from_ebay_path("Pottery & Glass > Drinkware > Mugs")
    assert found["id"] == 2111


def test_a_leaf_etsy_does_not_have_falls_through_to_the_model():
    assert etsy.taxonomy_from_ebay_path("Cameras & Photo > Film Photography > Darkroom") is None
    assert etsy.taxonomy_from_ebay_path("") is None
    assert etsy.taxonomy_from_ebay_path("   ") is None


def test_the_answer_is_remembered_so_a_batch_pays_once(monkeypatch):
    path = "Clothing, Shoes & Accessories > Men > Men's Clothing > T-Shirts"
    first = etsy.taxonomy_from_ebay_path(path)
    calls = []
    monkeypatch.setattr(etsy, "taxonomy_index",
                        lambda: calls.append(1) or [])
    assert etsy.taxonomy_from_ebay_path(path) == first
    assert calls == [], "a second listing in the same category re-scored the tree"


def test_the_suggestion_says_where_its_answer_came_from(monkeypatch):
    from backend.models import Listing

    listing = Listing(title="Vintage tee", price=20.0, quantity=1,
                      category_suggestion="Clothing > Men's Clothing > T-Shirts")
    picked = etsy.suggest_taxonomy(listing)
    assert picked == {"taxonomy_id": 1111, "path": "Clothing > Men's Clothing > Shirts & Tees > T-shirts",
                      "source": "ebay_path"}

    # Nothing in the path to match: the keyword shortlist answers, and says so.
    monkeypatch.setattr(etsy.config, "anthropic_ready", lambda: False)
    vague = Listing(title="Collectibles lot", price=20.0, quantity=1,
                    category_suggestion="Everything Else > Other")
    assert etsy.suggest_taxonomy(vague)["source"] == "keyword"

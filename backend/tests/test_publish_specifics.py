"""How multi-valued item specifics reach eBay, on both publish paths.

eBay's multi-select aspects — the item-specifics checkboxes (Features, Style,
Season...) — hold several values, which reach us as several ItemSpecific rows
under one name. The Inventory path has to send them as one aspect array; the
Trading path as ONE NameValueList with several <Value> children, since a second
NameValueList under the same Name is a duplicate-name error that rejects the
whole publish.
"""
import pytest

from backend.models import ItemSpecific, Listing
from backend.services import ebay, ebay_trading, taxonomy


def _listing(specifics, **kw):
    base = dict(title="Vintage Wool Overcoat", description="Great coat.",
                price=80.0, quantity=1, condition="USED_EXCELLENT",
                images=["a.jpg"], item_specifics=specifics)
    base.update(kw)
    return Listing(**base)


def _aspect(name, *, values, multi):
    return {"name": name, "required": False, "mode": "SELECTION_ONLY",
            "values": list(values), "cardinality": "MULTI" if multi else "SINGLE",
            "data_type": "STRING", "format": "", "max_length": 0}


CATEGORY_ASPECTS = [
    _aspect("Features", values=["Breathable", "Pockets", "Water Resistant"],
            multi=True),
    _aspect("Fit", values=["Regular", "Slim"], multi=False),
]


@pytest.fixture
def category(monkeypatch):
    """eBay's aspect metadata for the listing's category, without the network.
    Both the sanitize pass and the aspect assembly read it through this."""
    monkeypatch.setattr(taxonomy, "item_aspects",
                        lambda cid, marketplace_id=None: {"aspects": CATEGORY_ASPECTS})


# --- Trading (XML) path -------------------------------------------------------

def _specifics_xml(listing) -> str:
    xml = "".join(ebay_trading._item_fields(listing))
    start = xml.index("<ItemSpecifics>")
    return xml[start:xml.index("</ItemSpecifics>", start)]


def test_multi_value_aspect_is_one_namevaluelist():
    block = _specifics_xml(_listing([
        ItemSpecific(name="Features", value="Breathable"),
        ItemSpecific(name="Features", value="Water Resistant"),
        ItemSpecific(name="Season", value="Winter"),
    ]))
    assert block.count("<Name>Features</Name>") == 1
    assert "<Value>Breathable</Value><Value>Water Resistant</Value>" in block
    assert "<Name>Season</Name><Value>Winter</Value>" in block


def test_repeated_value_and_over_thirty_are_dropped():
    specs = [ItemSpecific(name="Features", value="Pockets")] * 2
    specs += [ItemSpecific(name="Features", value=f"F{i}") for i in range(40)]
    block = _specifics_xml(_listing(specs))
    assert block.count("<Value>") == 30
    assert block.count("<Value>Pockets</Value>") == 1


def test_values_are_escaped_and_brand_still_seeded():
    block = _specifics_xml(_listing(
        [ItemSpecific(name="Style", value="Rock & Roll")], brand="Levi's"))
    assert "<Value>Rock &amp; Roll</Value>" in block
    assert "<Name>Brand</Name><Value>Levi's</Value>" in block


# --- Inventory (REST) path ----------------------------------------------------

def _aspects_sent(listing):
    item = ebay.build_inventory_item("sess", listing, "http://x", ["http://img"])
    return item["product"]["aspects"]


def test_inventory_multi_aspect_keeps_every_tick(category):
    sent = _aspects_sent(_listing([
        ItemSpecific(name="Features", value="Pockets"),
        ItemSpecific(name="Features", value="Water Resistant"),
    ], category_id="57988"))
    assert sent["Features"] == ["Pockets", "Water Resistant"]


def test_inventory_single_aspect_still_ships_one_value(category):
    sent = _aspects_sent(_listing([
        ItemSpecific(name="Fit", value="Slim"),
        ItemSpecific(name="Fit", value="Regular"),
    ], category_id="57988"))
    assert sent["Fit"] == ["Slim"]

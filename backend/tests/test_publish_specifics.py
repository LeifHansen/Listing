"""How multi-valued item specifics reach eBay.

eBay's multi-select aspects — the item-specifics checkboxes (Features, Style,
Season...) — hold several values, which reach us as several ItemSpecific rows
under one name. They have to go out as ONE NameValueList with several <Value>
children: a second NameValueList under the same Name is a duplicate-name error
that rejects the whole publish.

There used to be two publish paths to check. The Inventory engine is gone, so
the second half of this file now covers the same guarantees where they
actually live on the Trading path — see the comment there.
"""
import pytest

from backend.models import ItemSpecific, Listing
from backend.services import ebay_trading, taxonomy


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


# --- the same contract, now that Trading is the only path ---------------------
#
# These two were written against ebay.build_inventory_item, which assembled the
# aspect dict itself. That engine is gone. The guarantees are not: on the
# Trading path they come from taxonomy.sanitize_specifics (canonical names,
# one value per SINGLE-cardinality aspect, values coerced to the aspect's
# constraints) running before _item_fields groups the rows. create_on_ebay and
# push_edit both call it, so this pairing is what a real publish does.

def _sanitized_specifics_xml(listing) -> str:
    taxonomy.sanitize_specifics(listing)
    return _specifics_xml(listing)


def test_multi_aspect_keeps_every_tick(category):
    block = _sanitized_specifics_xml(_listing([
        ItemSpecific(name="Features", value="Pockets"),
        ItemSpecific(name="Features", value="Water Resistant"),
    ], category_id="57988"))
    assert block.count("<Name>Features</Name>") == 1
    assert "<Value>Pockets</Value><Value>Water Resistant</Value>" in block


def test_single_aspect_still_ships_one_value(category):
    """eBay rejects the whole publish with "<Aspect> should contain only one
    value" — so a second row under a SINGLE-cardinality name must be dropped
    before the XML is built, not grouped into it."""
    block = _sanitized_specifics_xml(_listing([
        ItemSpecific(name="Fit", value="Slim"),
        ItemSpecific(name="Fit", value="Regular"),
    ], category_id="57988"))
    assert "<Name>Fit</Name><Value>Slim</Value>" in block
    assert "Regular" not in block


def test_a_name_that_only_differs_in_case_is_snapped_to_ebay_s(category):
    """eBay matches aspects by EXACT name, so a seller-typed "features" does
    not satisfy the category's "Features" — it lands as a second, unrecognised
    specific instead."""
    block = _sanitized_specifics_xml(_listing([
        ItemSpecific(name="features", value="Pockets"),
    ], category_id="57988"))
    assert "<Name>Features</Name>" in block

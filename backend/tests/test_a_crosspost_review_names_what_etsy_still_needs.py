"""Before anything is sent, the seller reads what Etsy will get.

The crosspost fills in most of a listing from what eBay already holds — a
tidied title, a category, tags, materials, the age off the item specifics —
and asks once per batch for the three things Etsy requires that eBay never
did. The review is where a seller sees both halves: what was filled and
what Etsy would still refuse the listing over.
"""
from backend.models import ItemSpecific, Listing
from backend.services import crosspost

SETTINGS = {"shipping_profile_id": "7", "return_policy_id": "8",
            "readiness_state_id": "9"}
BATCH = {"who_made": "someone_else", "when_made": "1990s", "is_supply": False}


def _listing(**kw):
    base = dict(
        title="$$ VINTAGE LEVI'S 501 JEANS DARK WASH", description="<p>Great pair.</p>",
        price=45.0, quantity=1, condition="USED_EXCELLENT", images=["a.jpg"],
        brand="Levi's",
        item_specifics=[ItemSpecific(name="Decade", value="1990s"),
                        ItemSpecific(name="Material", value="Denim")],
    )
    base.update(kw)
    return Listing(**base)


def _suggest(_listing):
    return {"taxonomy_id": 1234, "path": "Clothing > Jeans", "source": "ebay_path"}


def test_the_row_shows_what_etsy_will_get():
    row = crosspost.review_row(_listing(), SETTINGS, BATCH, "draft", _suggest)
    assert row["etsy_title"] == "VINTAGE LEVI'S 501 JEANS Dark Wash"
    assert row["taxonomy"] == {"id": 1234, "path": "Clothing > Jeans", "source": "ebay_path"}
    assert row["when_made"] == "1990s" and row["when_made_source"] == "details"
    assert row["who_made"] == "someone_else"
    assert "Levi's" in row["tags"]
    assert row["materials"] == ["Denim"]
    assert row["ready"] is True
    assert row["blockers"] == []
    # The tidy is shown as a warning, not a stop.
    assert [w for w in row["warnings"] if w["target"] == "title"]


def test_a_listing_that_needs_something_says_which():
    row = crosspost.review_row(_listing(), {}, {}, "live", _suggest)
    targets = {b["target"] for b in row["blockers"]}
    assert {"etsy_attribution", "etsy_shipping_profile", "etsy_readiness_state",
            "etsy_return_policy"} <= targets
    assert row["ready"] is False


def test_a_category_the_listing_already_has_is_not_looked_up_again():
    listing = _listing()
    listing.etsy.taxonomy_id = 99
    asked = []
    row = crosspost.review_row(listing, SETTINGS, BATCH, "draft",
                               lambda item: asked.append(1) or _suggest(item))
    assert row["taxonomy"] == {"id": 99, "path": "", "source": "listing"}
    assert asked == []


def test_a_recent_item_by_someone_else_is_flagged_for_etsys_policy():
    """Etsy allows handmade, vintage and supplies. Something someone else
    made in the last twenty years is none of the three — Etsy calls it a
    production-partner listing, and for a reseller that is a refusal."""
    recent = crosspost.review_row(_listing(item_specifics=[]), SETTINGS,
                                  {"who_made": "someone_else", "when_made": "2010_2019"},
                                  "draft", _suggest)
    assert recent["policy_flag"] is True
    assert [b for b in recent["blockers"] if "production partner" in b["title"]]

    vintage = crosspost.review_row(_listing(), SETTINGS, BATCH, "draft", _suggest)
    assert vintage["policy_flag"] is False

    supply = crosspost.review_row(
        _listing(item_specifics=[]), SETTINGS,
        {"who_made": "someone_else", "when_made": "2010_2019", "is_supply": True},
        "draft", _suggest)
    assert supply["policy_flag"] is False
    assert supply["is_supply"] is True

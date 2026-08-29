"""What a revise is allowed to say, recovered by diffing saves.

The app has no edit-event stream, so "what did the seller change?" is answered
by comparing the listing arriving from the editor against the one already
stored. Getting this wrong in either direction is a real failure:

  - too eager (calling an untouched field an edit) puts the whole stale
    snapshot back in the revise payload, which is the overwrite this exists to
    prevent;
  - too shy (missing a real edit) silently drops the seller's change — they
    press save, eBay never hears about it, and the app shows the new value.
"""
from __future__ import annotations

from backend.models import ItemSpecific, Listing
from backend.services import dirty_fields


def _stored(**over) -> dict:
    base = Listing(title="Blue lamp", price=25.0, quantity=3,
                   description="A lamp.", category_id="123",
                   condition="USED_EXCELLENT").model_dump()
    base.update(over)
    return base


def test_an_untouched_save_marks_nothing():
    """The editor autosaves. A save that changed nothing must not manufacture
    an edit, or every autosave would re-send the full stale payload."""
    stored = _stored()
    listing = Listing(**stored)
    assert dirty_fields.changed_fields(listing, stored) == []


def test_the_edited_field_is_the_only_one_marked():
    stored = _stored()
    listing = Listing(**{**stored, "title": "Green lamp"})
    assert dirty_fields.changed_fields(listing, stored) == ["title"]


def test_an_inventory_edit_is_marked():
    """The field the whole mechanism exists for."""
    stored = _stored()
    listing = Listing(**{**stored, "quantity": 0})
    assert "quantity" in dirty_fields.changed_fields(listing, stored)


def test_marks_accumulate_across_saves():
    """A seller edits price, saves, edits title, saves, then publishes. If
    each save replaced the marks, the revise would carry only the title and
    the price change would be lost."""
    stored = _stored()
    first = dirty_fields.accumulate(Listing(**{**stored, "price": 30.0}), stored)
    assert first.is_dirty("price")

    stored_after = first.model_dump()
    second = dirty_fields.accumulate(
        Listing(**{**stored_after, "title": "Green lamp"}), stored_after)
    assert second.is_dirty("price") and second.is_dirty("title")


def test_a_brand_new_draft_has_nothing_to_diff_against():
    """No stored copy means no marketplace copy to overwrite. A create sends
    every field anyway, so there is nothing to mark."""
    assert dirty_fields.changed_fields(Listing(title="New"), None) == []
    assert dirty_fields.changed_fields(Listing(title="New"), {}) == []


def test_item_specifics_compare_across_their_two_shapes():
    """Aspects arrive as models from the editor and as dicts from storage.
    Comparing the shapes rather than the values would call every save an
    aspect edit, and put the whole stale aspect set back on the wire."""
    stored = _stored(item_specifics=[{"name": "Brand", "value": "Ikea"}])
    listing = Listing(**{**stored,
                         "item_specifics": [ItemSpecific(name="Brand",
                                                         value="Ikea")]})
    assert dirty_fields.changed_fields(listing, stored) == []


def test_a_real_aspect_edit_is_still_caught():
    stored = _stored(item_specifics=[{"name": "Brand", "value": "Ikea"}])
    listing = Listing(**{**stored,
                         "item_specifics": [ItemSpecific(name="Brand",
                                                         value="Muji")]})
    assert dirty_fields.changed_fields(listing, stored) == ["item_specifics"]


def test_json_widening_a_price_is_not_an_edit():
    """25 goes into the JSON column and can come back 25.0. Calling that an
    edit would mark price dirty on every save of an untouched listing."""
    stored = _stored(price=25)
    listing = Listing(**{**stored, "price": 25.0})
    assert "price" not in dirty_fields.changed_fields(listing, stored)


def test_a_field_the_stored_record_predates_is_not_an_edit():
    """After a deploy adds a field, old records don't carry it. Reading the
    stored record through the model gives it the field's default, so an
    untouched listing compares equal instead of reporting a phantom edit."""
    stored = _stored()
    stored.pop("subtitle", None)
    listing = Listing(**{**stored, "subtitle": ""})
    assert "subtitle" not in dirty_fields.changed_fields(listing, stored)


def test_filling_in_a_newly_added_field_IS_an_edit():
    """The other half: once the field exists and the seller types into it,
    that is a real change and has to reach eBay."""
    stored = _stored()
    stored.pop("subtitle", None)
    listing = Listing(**{**stored, "subtitle": "Mid-century"})
    assert "subtitle" in dirty_fields.changed_fields(listing, stored)


def test_an_unreadable_stored_record_infers_no_edits():
    """With no parseable baseline nothing can be PROVEN edited. Sending less
    is the safe failure: the alternative is building a revise payload out of
    a record we could not read."""
    assert dirty_fields.changed_fields(Listing(title="x"),
                                       {"price": "not-a-number"}) == []


def test_server_owned_state_is_never_a_seller_edit():
    """Identity and live counters are restored from storage or reported by
    eBay. Marking them would try to push eBay's own numbers back at it."""
    for name in ("ebay_listing_id", "source", "marketplaces", "watch_count",
                 "sold_quantity", "view_url", "ebay_account"):
        assert name not in dirty_fields.TRACKED, name


def test_marks_survive_the_save_then_publish_round_trip():
    """The end-to-end risk created by making revise minimal.

    A seller edits in the app, saves, then publishes. The save stores the new
    value, so by publish time the incoming listing and the stored one AGREE —
    a diff at that moment finds nothing changed. If the marks did not carry
    across, the revise would send an empty payload and the seller's edit would
    silently never reach eBay, with a success message on screen.

    Accumulating against the stored record's own marks is what prevents that.
    """
    stored = _stored()

    # Save: the seller changes the title.
    saved = dirty_fields.accumulate(
        Listing(**{**stored, "title": "Green lamp"}), stored)
    assert saved.is_dirty("title")

    # Publish: the client sends the same listing back, now identical to what
    # was stored a moment ago.
    after_save = saved.model_dump()
    at_publish = dirty_fields.accumulate(Listing(**after_save), after_save)

    assert dirty_fields.changed_fields(Listing(**after_save), after_save) == []
    assert at_publish.is_dirty("title"), \
        "the edit was lost between save and publish; the revise would be empty"

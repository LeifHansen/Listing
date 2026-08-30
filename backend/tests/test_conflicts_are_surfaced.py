""""Updated on eBay" must not be said about an edit that was held back.

When the seller and eBay have both changed the same field since the last
agreed state, the sync records a conflict and — correctly — sends neither
value: picking one silently is how a seller's Seller Hub fix gets overwritten,
which is the bug the three-way merge exists to stop.

But refusing to choose is only half an answer. Nothing said so. The seller
edited a title here, pressed Update, and got "Your eBay listing has been
updated." Their title never left the building, the listing on eBay still said
what it said before, and the record kept a conflict entry nothing rendered.
The next time they looked at eBay they found their edit missing with no reason
given — which is worse than an error, because an error at least prompts them.

So a revise that held something back says so and names the fields, and there
is a way to answer: keep mine, or take eBay's. Answering is the ONLY thing
that clears a conflict — a later sync must not resolve it by attrition.
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import sync_merge


def _conflicted() -> Listing:
    shadow = {"title": "Blue lamp", "price": 25.0, "quantity": 1}
    local = Listing(**dict(shadow, title="Blue ceramic lamp"))
    remote = dict(shadow, title="Blue lamp, mid-century")
    merged = sync_merge.three_way(local, shadow, remote, dirty={"title"})
    assert "title" in merged.conflicts, "the fixture stopped conflicting"
    merged.listing.conflicts = merged.conflicts
    return merged.listing


# ------------------------------------------------------- it is describable

def test_a_conflict_can_be_put_into_words(monkeypatch):
    listing = _conflicted()

    described = sync_merge.describe_conflicts(listing.conflicts)

    assert [d["field"] for d in described] == ["title"]
    assert described[0]["label"] == "title"
    assert described[0]["mine"] == "Blue ceramic lamp"
    assert described[0]["ebay"] == "Blue lamp, mid-century"


def test_the_description_names_fields_a_seller_recognises():
    """"package_weight_lb" is a column name, not something to show someone."""
    described = sync_merge.describe_conflicts(
        {"package_weight_lb": {"local": 2, "remote": 3},
         "item_specifics": {"local": [], "remote": []}})

    labels = {d["field"]: d["label"] for d in described}
    assert labels["package_weight_lb"] == "package weight"
    assert labels["item_specifics"] == "item specifics"


def test_long_values_are_trimmed_for_display():
    """A description conflict is two 5,000-character blobs. They belong in the
    editor, not in a toast."""
    described = sync_merge.describe_conflicts(
        {"description": {"local": "x" * 9000, "remote": "y" * 9000}})

    assert len(described[0]["mine"]) < 300
    assert len(described[0]["ebay"]) < 300


def test_nothing_conflicting_describes_as_nothing():
    assert sync_merge.describe_conflicts({}) == []
    assert sync_merge.describe_conflicts(None) == []


# ------------------------------------------------------- answering resolves

def test_keeping_mine_queues_it_to_be_sent(monkeypatch):
    listing = _conflicted()

    sync_merge.resolve(listing, "title", "mine")

    assert listing.title == "Blue ceramic lamp"
    assert "title" in listing.dirty_fields, \
        "keeping the local value did not queue it for eBay"
    assert "title" not in listing.conflicts


def test_taking_ebays_writes_it_in_and_asks_for_nothing(monkeypatch):
    listing = _conflicted()

    sync_merge.resolve(listing, "title", "ebay")

    assert listing.title == "Blue lamp, mid-century"
    assert "title" not in listing.dirty_fields, \
        "taking eBay's value queued a pointless revise sending it back"
    assert "title" not in listing.conflicts


def test_taking_ebays_moves_the_base_too():
    """The shadow is what "have they changed it since we agreed" is measured
    against. Leaving it stale re-raises the same conflict on the next sync."""
    listing = _conflicted()
    listing.remote_shadow = {"title": "Blue lamp"}

    sync_merge.resolve(listing, "title", "ebay")

    assert listing.remote_shadow["title"] == "Blue lamp, mid-century"


def test_keeping_mine_moves_the_base_to_ebays_too():
    """Subtle but load-bearing: the base records what eBay LAST SAID, not what
    we want it to say. Keeping the local value still means eBay's current text
    is the newest thing eBay has told us — leaving the old base behind would
    make eBay look like it had changed the field again on the next sync, and
    re-raise a conflict the seller has already answered."""
    listing = _conflicted()
    listing.remote_shadow = {"title": "Blue lamp"}

    sync_merge.resolve(listing, "title", "mine")

    assert listing.remote_shadow["title"] == "Blue lamp, mid-century"


def test_an_unknown_answer_is_refused():
    listing = _conflicted()
    with pytest.raises(ValueError):
        sync_merge.resolve(listing, "title", "whatever")
    assert "title" in listing.conflicts


def test_a_field_that_is_not_conflicted_is_refused():
    """Otherwise a stale browser tab could overwrite a field with a value
    nobody is looking at, through an endpoint meant to settle a question."""
    listing = _conflicted()
    with pytest.raises(ValueError):
        sync_merge.resolve(listing, "price", "ebay")


def test_resolving_one_leaves_the_others_alone():
    listing = _conflicted()
    listing.conflicts["price"] = {"local": 25.0, "remote": 30.0}

    sync_merge.resolve(listing, "title", "mine")

    assert list(listing.conflicts) == ["price"]


# ------------------------------------- the revise stops claiming it all went

def test_a_revise_that_held_something_back_says_so():
    """The finding: "Your eBay listing has been updated." while the seller's
    title never left the building."""
    from backend.marketplaces import ebay_provider

    message = ebay_provider.revise_message(
        {"title": {"local": "Mine", "remote": "Theirs"}}, relist=False)

    assert "title" in message
    lowered = message.lower()
    assert "both" in lowered or "also changed" in lowered, message
    # And it has to point somewhere, not just apologise.
    assert "choose" in lowered or "pick" in lowered, message


def test_a_clean_revise_says_what_it_always_said():
    from backend.marketplaces import ebay_provider

    assert ebay_provider.revise_message({}, relist=False) == \
        "Your eBay listing has been updated."


def test_a_relist_is_not_described_as_an_update():
    from backend.marketplaces import ebay_provider

    assert "Relisted" in ebay_provider.revise_message({}, relist=True)


def test_several_held_back_fields_are_all_named():
    from backend.marketplaces import ebay_provider

    message = ebay_provider.revise_message(
        {"title": {"local": "a", "remote": "b"},
         "price": {"local": 1, "remote": 2}}, relist=False)

    assert "title" in message and "price" in message

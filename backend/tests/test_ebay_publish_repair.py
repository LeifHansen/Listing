"""What the publish path does when it finds another account's ids.

`_with_current_policies` re-checks the account-scoped ids on the path every
publish already takes. Replacing an id that HELD a value is the tell-tale of a
different eBay seller account -- the same signal `ebay_callback` acts on -- so
this has to do everything the connect handler does on that signal, not just the
part it was named for.

Two of those things, and the second is not housekeeping:

  1. Stamp the listings already here as the previous account's. Without it
     `listing_sync.belongs_to` treats every unstamped record as belonging to
     whoever is connected now, and syncs and revises go on operating on the
     PREVIOUS seller's item ids.

  2. Clear the saved ZIP. On a live publish `create_on_ebay` re-ensures the
     ship-from location FROM that ZIP and writes the returned key back over
     `merchant_location_key`. Left set, the old store's ZIP would force our
     location to the old address and undo the location half of this repair
     inside the same request -- so the repair would look applied in the
     database and still publish from the wrong store.
"""
from __future__ import annotations

import pytest

from backend.marketplaces import ebay_provider
from backend.services import ebay_account

ACCT = {
    "ebay_username": "new_seller",
    "fulfillment_policy_id": "F-old",
    "payment_policy_id": "P-old",
    "return_policy_id": "R-old",
    "merchant_location_key": "L-old",
    "ship_from_postal": "90210",   # the PREVIOUS store's ZIP
    "refresh_token": "r",
}
ON_NEW_ACCOUNT = {
    "fulfillment_policy_id": "F-new",
    "payment_policy_id": "P-new",
    "return_policy_id": "R-new",
    "merchant_location_key": "L-new",
}


@pytest.fixture
def repair(monkeypatch):
    """_with_current_policies with eBay and the database stubbed out.

    `answers` is what eBay says exists on the connected account; pass {} / None
    to simulate the outage case.
    """
    saved: dict = {}
    stamped: list = []

    def run(answers_policies, answers_location, acct=None):
        saved.clear()
        stamped.clear()
        ebay_account.forget_verified("u1")
        monkeypatch.setattr(ebay_provider.ebay_auth,
                            "fetch_policies_and_location",
                            lambda _t: dict(ON_NEW_ACCOUNT))
        monkeypatch.setattr(ebay_provider.ebay_auth, "policy_ids_on_account",
                            lambda _t: answers_policies)
        monkeypatch.setattr(ebay_provider.ebay_auth, "location_keys_on_account",
                            lambda _t: answers_location)
        monkeypatch.setattr(ebay_provider.db, "save_ebay_account",
                            lambda uid, **kw: saved.update(kw))
        monkeypatch.setattr(ebay_provider.db, "stamp_ebay_account",
                            lambda uid, name: stamped.append(name) or 3)
        out = ebay_provider._with_current_policies(
            "u1", dict(acct or ACCT), "access-token")
        return out, saved, stamped
    return run


ALL_FOREIGN = ({"fulfillment": {"F-new"}, "payment": {"P-new"},
                "return": {"R-new"}}, {"L-new"})
ALL_MINE = ({"fulfillment": {"F-old"}, "payment": {"P-old"},
             "return": {"R-old"}}, {"L-old"})


def test_a_foreign_account_gets_every_id_replaced(repair):
    out, saved, _ = repair(*ALL_FOREIGN)
    for field, value in ON_NEW_ACCOUNT.items():
        assert out[field] == value
        assert saved[field] == value


def test_the_switch_labels_the_listings_already_here(repair):
    """Otherwise every existing record still reads as the new account's."""
    _, _, stamped = repair(*ALL_FOREIGN)
    assert stamped == ["new_seller"]


def test_the_switch_drops_the_previous_store_s_zip(repair):
    """The one that would silently undo itself: a live publish re-ensures the
    ship-from location from this ZIP and writes the key back over
    merchant_location_key."""
    out, saved, _ = repair(*ALL_FOREIGN)
    assert saved["ship_from_postal"] == ""
    assert out["ship_from_postal"] == ""


def test_an_account_whose_ids_all_check_out_is_left_completely_alone(repair):
    out, saved, stamped = repair(*ALL_MINE)
    assert saved == {}
    assert stamped == []
    assert out["ship_from_postal"] == "90210"


def test_an_ebay_outage_changes_nothing_and_does_not_start_the_clock(repair):
    """The failure mode the whole re-check exists for. Neither lookup raises,
    so an outage produces no changes -- and must not be recorded as a pass, or
    the repair is suppressed for the next TTL for the seller who needs it."""
    out, saved, stamped = repair({}, None)
    assert saved == {}
    assert stamped == []
    assert out == ACCT
    assert ebay_account.verify_due("u1") is True


def test_a_conclusive_pass_is_not_repeated_on_the_next_publish(repair):
    repair(*ALL_MINE)
    assert ebay_account.verify_due("u1") is False


def test_a_deliberate_none_survives_the_repair(repair):
    """Settings offers "— none —" for each business policy. A repair pass that
    filled it back in from eBay's default would break a working seller."""
    acct = {**ACCT, "return_policy_id": ""}
    out, saved, _ = repair(
        {"fulfillment": {"F-old"}, "payment": {"P-old"}, "return": {"R-new"}},
        {"L-old"}, acct=acct)
    assert "return_policy_id" not in saved
    assert out["return_policy_id"] == ""

"""A seller who renames their eBay account still owns their listings.

Ownership is decided on eBay's immutable account id (listing_sync.owns), but
"is this a previous account's listing?" -- the question behind the release
button -- was answered on the username alone. A seller who renamed their eBay
handle kept their old name on every record the import does not re-label
(sold listings past eBay's window, ended ones in their grace period), so those
read as another account's, and releasing them flipped the seller's own sale
archive back to drafts with no item ids.

`releasable` now takes the connected account and lets the immutable id decide
whenever both sides carry one. A bare username still works as it did.
"""
from __future__ import annotations

from backend.services import ebay_account

RENAMED = {"ebay_username": "alice_vintage", "ebay_user_id": "ID_A"}


def _record(**extra):
    return {"ebay_account": "alice", "ebay_account_id": "ID_A",
            "ebay_listing_id": "110000000001", **extra}


def test_a_rename_does_not_make_a_listing_foreign():
    assert not ebay_account.releasable(_record(), RENAMED)
    assert not ebay_account.releasable(_record(), RENAMED, include_unowned=True)


def test_a_different_account_id_is_foreign_whatever_the_name():
    """A handle can be given up and taken by someone else."""
    same_name = {"ebay_username": "alice", "ebay_user_id": "ID_OTHER"}
    assert ebay_account.releasable(_record(), same_name)


def test_the_unknown_account_label_is_not_foreign_when_the_id_matches():
    record = _record(ebay_account="previous account")
    assert not ebay_account.releasable(record, RENAMED)


def test_without_an_id_on_either_side_the_name_still_decides():
    assert ebay_account.releasable({"ebay_account": "bob",
                                    "ebay_listing_id": "1"}, RENAMED)
    assert not ebay_account.releasable(
        {"ebay_account": "alice_vintage", "ebay_listing_id": "1"},
        {"ebay_username": "alice_vintage", "ebay_user_id": ""})
    # ...and a bare name is still accepted, exactly as before.
    assert ebay_account.releasable(_record(), "alice_vintage")
    assert not ebay_account.releasable(_record(), "alice")

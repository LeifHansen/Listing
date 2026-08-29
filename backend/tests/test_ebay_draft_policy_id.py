"""The one policy id nothing was reconciling: the one on the DRAFT.

`ACCOUNT_SCOPED` covers the three ids stored against the eBay account, and the
publish path re-checks them against eBay once per TTL. But a listing carries
its OWN `fulfillment_policy_id` — the editor's and the bulk card's Shipping
dropdown — and `publish_policies` prefers it over the account default.

Nothing ever validated it. A draft created while another eBay account was
connected therefore kept that account's policy id indefinitely and re-sent it
on every publish; eBay rejects another seller's profile id outright, which
looks from every screen in this app like the ACCOUNT being blocked rather than
one stale field on one draft. Seven drafts, seven identical failures, on an
account whose own API checks all come back clean.
"""
from __future__ import annotations

import pytest

from backend.models import Listing
from backend.services import ebay_account, listing_sync

VALID = {"fulfillment": {"F-mine"}, "payment": {"P-mine"}, "return": {"R-mine"}}
CREDS = {"_uid": "u1", "fulfillment_policy_id": "F-mine",
         "payment_policy_id": "P-mine", "return_policy_id": "R-mine"}


@pytest.fixture(autouse=True)
def _clean():
    ebay_account.forget_verified()
    yield
    ebay_account.forget_verified()


def _draft(policy_id: str = "") -> Listing:
    return Listing(title="Teacup", price=22.0,
                   fulfillment_policy_id=policy_id)


def test_a_foreign_policy_id_on_a_draft_falls_back_to_the_account_default():
    ebay_account.remember_valid_policies("u1", VALID)
    out = listing_sync.publish_policies(_draft("F-someone-elses"), CREDS)
    assert out["fulfillment_policy_id"] == "F-mine"


def test_a_policy_the_account_really_has_is_kept():
    """The per-listing choice is a real feature — it must survive the check."""
    ebay_account.remember_valid_policies(
        "u1", {**VALID, "fulfillment": {"F-mine", "F-second"}})
    out = listing_sync.publish_policies(_draft("F-second"), CREDS)
    assert out["fulfillment_policy_id"] == "F-second"


def test_an_unchecked_id_is_passed_through_untouched():
    """No cached answer means no opinion. Replacing a seller's chosen policy
    because eBay was unreachable is the same class of bug as keeping a foreign
    one — it just fails in the other direction."""
    out = listing_sync.publish_policies(_draft("F-unknown"), CREDS)
    assert out["fulfillment_policy_id"] == "F-unknown"


def test_a_stale_answer_is_not_trusted():
    ebay_account.remember_valid_policies(
        "u1", VALID, now=0.0)  # far outside the TTL
    out = listing_sync.publish_policies(_draft("F-someone-elses"), CREDS)
    assert out["fulfillment_policy_id"] == "F-someone-elses"


def test_a_draft_with_no_choice_still_uses_the_account_default():
    ebay_account.remember_valid_policies("u1", VALID)
    out = listing_sync.publish_policies(_draft(""), CREDS)
    assert out["fulfillment_policy_id"] == "F-mine"


def test_the_other_two_policies_are_untouched():
    ebay_account.remember_valid_policies("u1", VALID)
    out = listing_sync.publish_policies(_draft("F-someone-elses"), CREDS)
    assert out["payment_policy_id"] == "P-mine"
    assert out["return_policy_id"] == "R-mine"

"""What survives a reconnect, and how an account switch is detected.

Business-policy ids and location keys belong to ONE eBay seller account: eBay
rejects another account's id outright, and a listing published with one fails
somewhere the seller can't see. The old rule kept every saved id whenever the
account NAME couldn't be read — which is the common case, because connections
made before the identity scope was granted 403 on the identity call. A seller
switching accounts therefore carried the previous store's shipping, payment,
return and location ids straight into the new one.

The rule now is existence, not identity: a saved id survives only if the
account that just connected actually has it.
"""
from __future__ import annotations

import pytest

from backend import main

SAVED = {
    "fulfillment_policy_id": "F-old",
    "payment_policy_id": "P-old",
    "return_policy_id": "R-old",
    "merchant_location_key": "L-old",
}
DISCOVERED = {
    "fulfillment_policy_id": "F-new",
    "payment_policy_id": "P-new",
    "return_policy_id": "R-new",
    "merchant_location_key": "L-new",
}


@pytest.fixture
def account(monkeypatch):
    """Stub what eBay reports as existing on the connected account."""
    def setup(policies, locations):
        monkeypatch.setattr(main.ebay_auth, "policy_ids_on_account",
                            lambda _t: policies)
        monkeypatch.setattr(main.ebay_auth, "location_keys_on_account",
                            lambda _t: locations)
    return setup


def test_same_account_keeps_every_saved_choice(account):
    account({"fulfillment": {"F-old"}, "payment": {"P-old"},
             "return": {"R-old"}}, {"L-old"})
    assert main._carry_over_settings("tok", SAVED, DISCOVERED) == {}


def test_a_different_account_gets_fresh_defaults(account):
    """None of the saved ids exist here — every one is replaced."""
    account({"fulfillment": {"F-new"}, "payment": {"P-new"},
             "return": {"R-new"}}, {"L-new"})
    assert main._carry_over_settings("tok", SAVED, DISCOVERED) == DISCOVERED


def test_an_unreadable_account_name_no_longer_hides_the_switch(account):
    """The whole bug: identity is unreadable, so the old code kept everything.
    Existence answers it without the name."""
    account({"fulfillment": {"F-new"}, "payment": {"P-new"},
             "return": {"R-new"}}, {"L-new"})
    carried = main._carry_over_settings("tok", SAVED, DISCOVERED)
    assert main._settings_were_dropped({**SAVED, **carried}, SAVED) is True


def test_an_api_blip_never_re_picks_a_sellers_shipping(account):
    """A kind eBay couldn't report is unknown, not empty — leave it alone."""
    account({"payment": {"P-old"}, "return": {"R-old"}}, None)
    carried = main._carry_over_settings("tok", SAVED, DISCOVERED)
    assert "fulfillment_policy_id" not in carried
    assert "merchant_location_key" not in carried
    assert main._settings_were_dropped({**SAVED, **carried}, SAVED) is False


def test_a_gap_is_filled_from_what_was_discovered(account):
    account({"fulfillment": set(), "payment": {"P-old"}, "return": {"R-old"}},
            {"L-old"})
    saved = {**SAVED, "fulfillment_policy_id": ""}
    carried = main._carry_over_settings("tok", saved, DISCOVERED)
    assert carried["fulfillment_policy_id"] == "F-new"


def test_only_one_dropped_id_is_enough_to_call_it_a_switch(account):
    account({"fulfillment": {"F-new"}, "payment": {"P-old"},
             "return": {"R-old"}}, {"L-old"})
    carried = main._carry_over_settings("tok", SAVED, DISCOVERED)
    assert carried == {"fulfillment_policy_id": "F-new"}
    assert main._settings_were_dropped({**SAVED, **carried}, SAVED) is True

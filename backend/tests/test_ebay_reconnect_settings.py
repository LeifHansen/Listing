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

import httpx
import pytest

from backend import ebay_auth
from backend.services import ebay_account

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
def carry():
    """carry_over_settings with eBay's answers about the connected account
    injected — the lookups are parameters precisely so this needs no network
    and no app import."""
    def run(saved, policies, locations):
        return ebay_account.carry_over_settings(
            "tok", saved, DISCOVERED,
            policy_ids=lambda _t: policies, location_keys=lambda _t: locations)
    return run


def test_same_account_keeps_every_saved_choice(carry):
    assert carry(SAVED, {"fulfillment": {"F-old"}, "payment": {"P-old"},
                         "return": {"R-old"}}, {"L-old"}) == {}


def test_a_different_account_gets_fresh_defaults(carry):
    """None of the saved ids exist here — every one is replaced."""
    assert carry(SAVED, {"fulfillment": {"F-new"}, "payment": {"P-new"},
                         "return": {"R-new"}}, {"L-new"}) == DISCOVERED


def test_an_unreadable_account_name_no_longer_hides_the_switch(carry):
    """The whole bug: identity is unreadable, so the old code kept everything.
    Existence answers it without the name."""
    carried = carry(SAVED, {"fulfillment": {"F-new"}, "payment": {"P-new"},
                            "return": {"R-new"}}, {"L-new"})
    assert ebay_account.settings_were_dropped({**SAVED, **carried}, SAVED) is True


def test_an_api_blip_never_re_picks_a_sellers_shipping(carry):
    """A kind eBay couldn't report is unknown, not empty — leave it alone."""
    carried = carry(SAVED, {"payment": {"P-old"}, "return": {"R-old"}}, None)
    assert "fulfillment_policy_id" not in carried
    assert "merchant_location_key" not in carried
    assert ebay_account.settings_were_dropped({**SAVED, **carried}, SAVED) is False


def test_a_gap_is_filled_from_what_was_discovered(carry):
    carried = carry({**SAVED, "fulfillment_policy_id": ""},
                    {"fulfillment": set(), "payment": {"P-old"},
                     "return": {"R-old"}}, {"L-old"})
    assert carried["fulfillment_policy_id"] == "F-new"


def test_only_one_dropped_id_is_enough_to_call_it_a_switch(carry):
    carried = carry(SAVED, {"fulfillment": {"F-new"}, "payment": {"P-old"},
                            "return": {"R-old"}}, {"L-old"})
    assert carried == {"fulfillment_policy_id": "F-new"}
    assert ebay_account.settings_were_dropped({**SAVED, **carried}, SAVED) is True


# --- "does this account have that policy?" -----------------------------------
#
# The answer gates a publish: preflight turns a definitive no into a blocking
# shipping-policy error. So the three states have to stay apart — a rejected
# REQUEST is not a missing policy, and reading it as one blocks every live
# publish behind an error about a policy that is fine.

def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.ebay.com/policy")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.fixture()
def lookup(monkeypatch):
    """fulfillment_policy_lookup with eBay's answer stubbed."""
    def run(outcome):
        def fake_get(path, token):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setattr(ebay_auth, "_account_get", fake_get)
        return ebay_auth.fulfillment_policy_lookup("tok", "F-1")
    return run


def test_a_404_means_the_account_really_lacks_the_policy(lookup):
    assert lookup(_http_error(404)) == ([], False)


def test_a_400_is_a_rejected_request_not_a_missing_policy(lookup):
    """eBay refusing the request says nothing about what the account has.

    Treating it as absence blocked every live publish behind "that shipping
    policy isn't on your eBay account" — for a policy the seller could see on
    eBay the whole time.
    """
    _services, exists = lookup(_http_error(400))
    assert exists is None


def test_a_server_error_leaves_the_answer_unknown(lookup):
    assert lookup(_http_error(503)) == ([], None)


def test_no_policy_id_asks_nothing(lookup):
    assert ebay_auth.fulfillment_policy_lookup("tok", "") == ([], None)

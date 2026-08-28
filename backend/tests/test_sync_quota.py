"""The status heartbeat must not spend the account's daily eBay quota.

eBay caps Trading API calls per DAY for the whole app. A sync's per-item
probe sweeps cost up to ~100 calls (60 imported + 40 app listings, each its
own round trip), while the finished-list reconcile that actually moves ended
and sold records costs two. A background heartbeat running the sweeps every
few minutes therefore drains the day's allowance by itself — and once it's
gone, every Trading call fails, including the AddFixedPriceItem that
publishes a listing. Publishes then die with no fault in the listing.

So: unforced syncs (the heartbeat) run the cheap pass only, a deliberate
sync (the "Sync with eBay" button) still runs everything, and the two limit
rejections a seller can hit are explained rather than reported as a generic
"eBay rejected the listing".
"""
from __future__ import annotations

import backend.ebay_errors as ebay_errors
from backend.services import sync_guard


def test_first_unforced_sweep_runs_then_cools_down():
    sync_guard.reset()
    # The first background check is free — nothing has been swept yet.
    assert sync_guard.sweep_due("u1") is True
    # Immediately after, the expensive sweeps are skipped.
    assert sync_guard.sweep_due("u1") is False
    assert sync_guard.sweep_due("u1") is False


def test_force_always_runs_the_sweep():
    sync_guard.reset()
    assert sync_guard.sweep_due("u1") is True
    # The manual "Sync with eBay" button must never be silently downgraded.
    assert sync_guard.sweep_due("u1", force=True) is True
    assert sync_guard.sweep_due("u1", force=True) is True


def test_cooldown_is_per_account():
    sync_guard.reset()
    assert sync_guard.sweep_due("u1") is True
    # One user's sweep must not stop another account's first check.
    assert sync_guard.sweep_due("u2") is True
    assert sync_guard.sweep_due("u1") is False


def test_cooldown_expires():
    sync_guard.reset()
    assert sync_guard.sweep_due("u1") is True
    # Age the record past the window rather than sleeping through it.
    sync_guard._last_sweep["u1"] -= sync_guard.COOLDOWN_SECONDS + 1
    assert sync_guard.sweep_due("u1") is True


def test_call_limit_error_is_explained_not_generic():
    # No errorId: this branch is keyed on eBay's wording, and that is what the
    # test is about. The fixture used to carry 21917053, which reads as "this
    # is the call-limit code" — it is not, it is an expired IAF token, and
    # `ebay_trading._failure` correctly routes it to the reconnect message.
    # A fixture that mislabels a code is a trap for whoever reads it next.
    issues = ebay_errors.from_response(
        '{"errors":[{'
        '"message":"Application has exceeded the number of calls permitted."}]}')
    assert len(issues) == 1
    issue = issues[0]
    assert "API limit" in issue["title"]
    # The seller must be told the listing is fine and when to retry.
    assert "listing" in issue["fix"].lower()
    assert "reset" in issue["fix"].lower()


def test_selling_limit_error_names_the_real_fix():
    issues = ebay_errors.from_response(
        '{"errors":[{"errorId":21919188,'
        '"message":"You have reached your monthly selling limit."}]}')
    issue = issues[0]
    assert "selling limit" in issue["title"].lower()
    assert "Seller Hub" in issue["fix"]


def test_ordinary_rejections_still_map_to_their_field():
    # The limit branches must not swallow the field-targeted errors that make
    # the fix-it chips work.
    price = ebay_errors.from_response(
        '{"errors":[{"errorId":25002,"message":"The price is invalid."}]}')[0]
    assert price["target"] == "price"
    specifics = ebay_errors.from_response(
        '{"errors":[{"errorId":25002,'
        '"message":"The item specific Brand is missing."}]}')[0]
    assert specifics["target"] == "specifics"

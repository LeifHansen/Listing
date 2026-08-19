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
import backend.main as main


def _reset(user_id: str = "u1") -> None:
    main._last_sweep.pop(user_id, None)


def test_first_unforced_sweep_runs_then_cools_down():
    _reset()
    # The first background check is free — nothing has been swept yet.
    assert main._sweep_due("u1", force=False) is True
    # Immediately after, the expensive sweeps are skipped.
    assert main._sweep_due("u1", force=False) is False
    assert main._sweep_due("u1", force=False) is False


def test_force_always_runs_the_sweep():
    _reset()
    assert main._sweep_due("u1", force=False) is True
    # The manual "Sync with eBay" button must never be silently downgraded.
    assert main._sweep_due("u1", force=True) is True
    assert main._sweep_due("u1", force=True) is True


def test_cooldown_is_per_account():
    _reset("u1")
    _reset("u2")
    assert main._sweep_due("u1", force=False) is True
    # One user's sweep must not stop another account's first check.
    assert main._sweep_due("u2", force=False) is True
    assert main._sweep_due("u1", force=False) is False


def test_cooldown_expires():
    _reset()
    assert main._sweep_due("u1", force=False) is True
    # Age the record past the window rather than sleeping through it.
    main._last_sweep["u1"] -= main._SWEEP_COOLDOWN + 1
    assert main._sweep_due("u1", force=False) is True


def test_call_limit_error_is_explained_not_generic():
    issues = ebay_errors.from_response(
        '{"errors":[{"errorId":21917053,'
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

"""The monetization engine: spend arithmetic, monthly reset, refunds, and
purchase idempotency.

The pure helpers (plan_spend, periods, Stripe signature check) test directly;
the balance invariants run against the real db.token_* transaction code on a
throwaway SQLite file — the same SQLAlchemy paths production uses on Postgres,
minus the row locking (which SQLite ignores).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import importlib
import time

import pytest

from backend import config, db
from backend.services import tokens


# --- pure arithmetic --------------------------------------------------------

def test_plan_spend_prefers_free_allowance():
    assert tokens.plan_spend(50, 0, 100, 5) == (5, 0)


def test_plan_spend_splits_across_free_and_purchased():
    # 2 free left, needs 5 -> 2 free + 3 purchased
    assert tokens.plan_spend(50, 48, 10, 5) == (2, 3)


def test_plan_spend_uses_purchased_when_free_exhausted():
    assert tokens.plan_spend(50, 50, 10, 5) == (0, 5)


def test_plan_spend_declines_when_broke():
    assert tokens.plan_spend(50, 50, 4, 5) is None
    assert tokens.plan_spend(0, 0, 0, 1) is None


def test_plan_spend_zero_cost_is_free():
    assert tokens.plan_spend(0, 0, 0, 0) == (0, 0)


def test_period_and_reset_roll_the_calendar():
    d = dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc)
    assert tokens.period_now(d) == "2026-08"
    assert tokens.next_reset(d) == "2026-09-01"
    nye = dt.datetime(2026, 12, 31, tzinfo=dt.timezone.utc)
    assert tokens.period_now(nye) == "2026-12"
    assert tokens.next_reset(nye) == "2027-01-01"


def test_costs_and_packs_are_sane():
    # Every metered feature has a positive integer price...
    assert all(isinstance(v, int) and v >= 0 for v in tokens.COSTS.values())
    assert tokens.COSTS["identify"] >= tokens.COSTS["refine"]
    # ...and packs get cheaper per token as they grow (the upsell ladder).
    per_token = [p["usd_cents"] / p["tokens"] for p in tokens.PACKS]
    assert per_token == sorted(per_token, reverse=True)
    assert all(p["usd_cents"] > 0 and p["tokens"] > 0 for p in tokens.PACKS)
    assert tokens.pack("plus")["tokens"] == 120
    assert tokens.pack("nope") is None


def test_insufficient_message_names_tokens():
    # The frontend keys its buy-tokens dialog off the word "token" in a 402 —
    # it must never be dropped from this message.
    msg = tokens.insufficient_message({"free_remaining": 0, "purchased": 1})
    assert "token" in msg.lower()


# --- Stripe signature verification ------------------------------------------

def _sign(payload: bytes, secret: str, ts: int) -> str:
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def test_stripe_signature_roundtrip():
    payload, secret = b'{"type":"checkout.session.completed"}', "whsec_test"
    now = int(time.time())
    header = _sign(payload, secret, now)
    assert tokens.verify_stripe_signature(payload, header, secret)
    # Tampered body, wrong secret, stale timestamp -> all rejected.
    assert not tokens.verify_stripe_signature(b"{}", header, secret)
    assert not tokens.verify_stripe_signature(payload, header, "whsec_other")
    stale = _sign(payload, secret, now - 3600)
    assert not tokens.verify_stripe_signature(payload, stale, secret)
    assert not tokens.verify_stripe_signature(payload, "", secret)
    assert not tokens.verify_stripe_signature(payload, "t=abc,v1=zz", secret)


# --- balance invariants against the real DB code (SQLite) -------------------

@pytest.fixture
def tokens_db(monkeypatch, tmp_path):
    """Point the db module at a throwaway SQLite file and reset its
    process-wide engine state (it memoizes both engine and schema init)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/tokens.db")
    importlib.reload(config)
    db._engine = None
    db._initialized = False
    yield db
    db._engine = None
    db._initialized = False
    importlib.reload(config)


def test_spend_splits_and_declines(tokens_db):
    uid, period = "u1", "2026-08"
    res = tokens_db.token_spend(uid, 5, free_quota=6, period=period, feature="identify")
    assert res["ok"] and (res["free_part"], res["paid_part"]) == (5, 0)
    assert res["free_remaining"] == 1

    # Second draft: 1 free left, no purchased -> declined, balance untouched.
    res = tokens_db.token_spend(uid, 5, free_quota=6, period=period)
    assert not res["ok"] and res["reason"] == "insufficient"
    snap = tokens_db.token_status(uid, period, 6)
    assert snap == {"free_used": 5, "free_remaining": 1, "purchased": 0}

    # Buy a pack; the next spend takes the last free token first.
    assert tokens_db.token_credit(uid, 50, ref="cs_test_1")["ok"]
    res = tokens_db.token_spend(uid, 5, free_quota=6, period=period)
    assert res["ok"] and (res["free_part"], res["paid_part"]) == (1, 4)
    assert tokens_db.token_status(uid, period, 6)["purchased"] == 46


def test_free_allowance_resets_next_month(tokens_db):
    uid = "u2"
    assert tokens_db.token_spend(uid, 5, 5, "2026-08")["ok"]
    assert not tokens_db.token_spend(uid, 1, 5, "2026-08")["ok"]
    # New month: the allowance is back without any cron having run.
    res = tokens_db.token_spend(uid, 5, 5, "2026-09")
    assert res["ok"] and res["free_part"] == 5


def test_refund_restores_and_is_idempotent(tokens_db):
    uid, period = "u3", "2026-08"
    tokens_db.token_credit(uid, 10, ref="cs_test_2")
    spend = tokens_db.token_spend(uid, 8, free_quota=5, period=period)
    assert (spend["free_part"], spend["paid_part"]) == (5, 3)

    assert tokens_db.token_refund(uid, spend["entry_id"])
    snap = tokens_db.token_status(uid, period, 5)
    assert snap == {"free_used": 0, "free_remaining": 5, "purchased": 10}
    # Replaying the refund must not mint tokens (unique ledger ref).
    assert not tokens_db.token_refund(uid, spend["entry_id"])
    assert tokens_db.token_status(uid, period, 5)["purchased"] == 10


def test_partial_refund_returns_paid_tokens_first(tokens_db):
    uid, period = "u4", "2026-08"
    tokens_db.token_credit(uid, 10, ref="cs_test_3")
    spend = tokens_db.token_spend(uid, 8, free_quota=5, period=period)  # 5 free + 3 paid
    assert tokens_db.token_refund(uid, spend["entry_id"], units=2)
    snap = tokens_db.token_status(uid, period, 5)
    # The 2 refunded units come back as purchased (they never expire).
    assert snap["purchased"] == 9 and snap["free_used"] == 5


def test_refund_across_months_never_resurrects_free_tokens(tokens_db):
    """A refund of LAST month's spend must neither restore expired free
    tokens nor roll the account's period backwards (which would wipe this
    month's usage tracking). The paid part still comes back."""
    uid = "u6"
    tokens_db.token_credit(uid, 10, ref="cs_test_4")
    old = tokens_db.token_spend(uid, 8, free_quota=5, period="2026-08")  # 5 free + 3 paid
    assert tokens_db.token_spend(uid, 2, free_quota=5, period="2026-09")["ok"]

    assert tokens_db.token_refund(uid, old["entry_id"])
    snap = tokens_db.token_status(uid, "2026-09", 5)
    # 10 bought - 3 spent Aug + 3 back = 10; Aug's 5 free stay expired, and
    # Sept's 2 used free tokens stay used.
    assert snap == {"free_used": 2, "free_remaining": 3, "purchased": 10}


def test_credit_does_not_reset_free_usage(tokens_db):
    uid = "u7"
    tokens_db.token_spend(uid, 4, free_quota=5, period="2026-08")
    tokens_db.token_credit(uid, 50, ref="cs_test_5")
    snap = tokens_db.token_status(uid, "2026-08", 5)
    assert snap == {"free_used": 4, "free_remaining": 1, "purchased": 50}


def test_purchase_credit_is_idempotent_by_ref(tokens_db):
    uid = "u5"
    first = tokens_db.token_credit(uid, 120, ref="cs_live_abc")
    again = tokens_db.token_credit(uid, 120, ref="cs_live_abc")
    assert first["ok"] and not first["already"]
    assert again["ok"] and again["already"]
    assert tokens_db.token_status(uid, "2026-08", 0)["purchased"] == 120


def test_new_account_first_concurrent_spends_do_not_fall_through_to_fail_open(
        tokens_db, monkeypatch):
    """The account row is created on the first spend, and FOR UPDATE cannot
    lock a row that does not exist yet, so two concurrent first-ever spends
    both INSERT. The loser used to surface as a DB error, which the caller
    treats as an outage and FAILS OPEN (un-metered AI) — turning a double-tap
    on a new account into free AI.

    Set up as the real race: the winning writer has already committed the row
    from another session, and this caller's first lookup predates it, so the
    INSERT hits a genuine primary-key violation from the database.
    """
    uid, period = "race-1", "2026-08"

    # The winner, from its own session/transaction.
    with tokens_db.Session(tokens_db._get_engine()) as other:
        other.add(tokens_db.TokenAccount(
            user_id=uid, purchased=7, free_used=0, free_period="",
            updated_at=tokens_db._now()))
        other.commit()

    orig_get = tokens_db.Session.get
    stale = {"done": False}

    def get_with_stale_first_read(self, entity, ident, **kw):
        # Only the first TokenAccount lookup misses — the state this caller
        # was in when it decided to INSERT.
        if not stale["done"] and entity is tokens_db.TokenAccount:
            stale["done"] = True
            return None
        return orig_get(self, entity, ident, **kw)

    monkeypatch.setattr(tokens_db.Session, "get", get_with_stale_first_read)
    try:
        res = tokens_db.token_spend(uid, 5, free_quota=50, period=period,
                                    feature="identify")
    finally:
        monkeypatch.undo()

    # Metered normally rather than collapsing to None (which means fail-open).
    assert res is not None, "insert race fell through to the fail-open path"
    assert res["ok"] and res["free_part"] == 5
    snap = tokens_db.token_status(uid, period, 50)
    assert snap["free_used"] == 5
    assert snap["purchased"] == 7, "the winner's row was clobbered, not reused"


def test_get_listing_distinguishes_absent_from_unavailable(tokens_db, monkeypatch):
    """The ownership guard reads through this: 'no such listing' must never be
    confused with 'the database is down', or one blip disables the check."""
    assert tokens_db.get_listing_strict("nope") is None      # genuinely absent

    def boom(*a, **kw):
        raise RuntimeError("neon unreachable")

    monkeypatch.setattr(tokens_db, "_get_engine", boom)
    assert tokens_db.get_listing_strict("nope") is tokens_db.UNAVAILABLE
    # The lenient wrapper keeps its old contract for callers that just want
    # the record.
    assert tokens_db.get_listing("nope") is None

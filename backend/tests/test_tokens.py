"""The monetization engine: spend arithmetic, monthly reset, refunds, and
purchase idempotency.

The pure helpers (plan_spend, periods, Stripe signature check) test directly;
the balance invariants run against the real db.token_* transaction code on a
throwaway SQLite file — the same SQLAlchemy paths production uses on Postgres,
minus the row locking (which SQLite ignores).

db.plan_spend is the function db.token_spend itself calls, which is the only
reason testing it directly means anything. It was previously a second copy in
services/tokens.py, exercised here while token_spend ran its own inline split —
every assertion below passed against code production never reached.
test_the_ledger_applies_the_split_plan_spend_predicts guards that, against the
real transaction rather than by identity (module reloads in this suite make an
`is` check a test of import order).
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
    assert db.plan_spend(50, 0, 100, 5) == (5, 0)


def test_plan_spend_splits_across_free_and_purchased():
    # 2 free left, needs 5 -> 2 free + 3 purchased
    assert db.plan_spend(50, 48, 10, 5) == (2, 3)


def test_plan_spend_uses_purchased_when_free_exhausted():
    assert db.plan_spend(50, 50, 10, 5) == (0, 5)


def test_plan_spend_declines_when_broke():
    assert db.plan_spend(50, 50, 4, 5) is None
    assert db.plan_spend(0, 0, 0, 1) is None


def test_plan_spend_zero_cost_is_free():
    assert db.plan_spend(0, 0, 0, 0) == (0, 0)


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


def _sign_multi(payload: bytes, secrets: list[str], ts: int) -> str:
    """The header Stripe sends while a webhook secret is being rotated: one
    v1 per active endpoint secret, over the same timestamp and body."""
    macs = [hmac.new(sec.encode(), f"{ts}.".encode() + payload,
                     hashlib.sha256).hexdigest() for sec in secrets]
    return ",".join([f"t={ts}"] + [f"v1={m}" for m in macs])


def test_a_rotating_secret_verifies_whichever_v1_is_ours():
    """Rotating a webhook secret means both are live for a while, and every
    delivery in that window arrives signed twice.

    Parsing the header into a dict kept only the LAST v1, so verification
    became a coin flip decided by which order Stripe listed them: half the
    deliveries were rejected as forged. Rejected deliveries are not cosmetic
    here — that is a paid token pack never credited, or a refund never
    clawed back, with Stripe retrying against the same coin flip.
    """
    payload = b'{"type":"checkout.session.completed"}'
    ours, theirs = "whsec_ours", "whsec_the_new_one"
    now = int(time.time())
    # Ours first, then ours last: both orders must verify.
    assert tokens.verify_stripe_signature(
        payload, _sign_multi(payload, [ours, theirs], now), ours)
    assert tokens.verify_stripe_signature(
        payload, _sign_multi(payload, [theirs, ours], now), ours)


def test_multiple_signatures_do_not_weaken_the_check():
    """Accepting any of several candidates must not become accepting
    anything: a header full of signatures, none of them ours, is still a
    forgery — and the timestamp still has to be fresh."""
    payload = b'{"type":"checkout.session.completed"}'
    now = int(time.time())
    header = _sign_multi(payload, ["whsec_a", "whsec_b", "whsec_c"], now)
    assert not tokens.verify_stripe_signature(payload, header, "whsec_ours")
    # Right secret, wrong body.
    good = _sign_multi(payload, ["whsec_a", "whsec_ours"], now)
    assert not tokens.verify_stripe_signature(b'{"type":"other"}', good, "whsec_ours")
    # Right secret, replayed from an hour ago.
    stale = _sign_multi(payload, ["whsec_a", "whsec_ours"], now - 3600)
    assert not tokens.verify_stripe_signature(payload, stale, "whsec_ours")


def test_a_header_with_no_usable_signature_is_rejected():
    payload = b"{}"
    now = int(time.time())
    assert not tokens.verify_stripe_signature(payload, f"t={now}", "whsec_ours")
    assert not tokens.verify_stripe_signature(payload, f"t={now},v1=", "whsec_ours")
    assert not tokens.verify_stripe_signature(payload, "v1=abc", "whsec_ours")


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
    import pytest as _pytest

    from backend import errors

    assert tokens_db.get_listing_strict("nope") is None      # genuinely absent

    def boom(*a, **kw):
        raise RuntimeError("neon unreachable")

    monkeypatch.setattr(tokens_db, "_get_engine", boom)
    assert tokens_db.get_listing_strict("nope") is tokens_db.UNAVAILABLE

    # `get_listing` used to collapse the two back into None, and the comment
    # here called that "the lenient wrapper ... for callers that just want the
    # record". Ten route handlers were not those callers: they turned None
    # into `404 "Listing not found"`, so an unreadable store told the seller
    # their listing did not exist. It raises now, and the tolerance moved to
    # an explicitly-named twin for the two callers that really do want a
    # blank. See test_a_listing_we_cannot_read_is_not_missing.py.
    with _pytest.raises(errors.StorageUnavailable):
        tokens_db.get_listing("nope")
    assert tokens_db.get_listing_best_effort("nope") is None


# --- purchase reversal (refunds & chargebacks) ------------------------------

def test_reversal_debits_the_purchase(tokens_db):
    uid = "rev-1"
    tokens_db.token_credit(uid, 50, ref="cs_ref_1")
    assert tokens_db.token_status(uid, "2026-08", 0)["purchased"] == 50

    res = tokens_db.token_reverse_purchase("cs_ref_1", reason="charge.refunded")
    assert res["ok"] and not res["already"]
    assert (res["reversed"], res["shortfall"]) == (50, 0)
    assert tokens_db.token_status(uid, "2026-08", 0)["purchased"] == 0


def test_reversal_is_idempotent(tokens_db):
    """Stripe redelivers events; a second delivery must not debit again."""
    uid = "rev-2"
    tokens_db.token_credit(uid, 50, ref="cs_ref_2")
    tokens_db.token_reverse_purchase("cs_ref_2")
    again = tokens_db.token_reverse_purchase("cs_ref_2")
    assert again["already"] is True
    assert tokens_db.token_status(uid, "2026-08", 0)["purchased"] == 0


def test_reversal_floors_at_zero_and_reports_the_shortfall(tokens_db):
    """Tokens already spent can't be taken back. The balance must not go
    negative — that would silently eat the buyer's next purchase — but the
    operator still needs to see what the refund actually cost."""
    uid, period = "rev-3", "2026-08"
    tokens_db.token_credit(uid, 50, ref="cs_ref_3")
    tokens_db.token_spend(uid, 30, free_quota=0, period=period, feature="identify")
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 20

    res = tokens_db.token_reverse_purchase("cs_ref_3", reason="dispute lost")
    assert (res["reversed"], res["shortfall"]) == (20, 30)
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 0


def test_reversal_only_touches_the_refunded_purchase(tokens_db):
    """A buyer with two packs who refunds one keeps the other."""
    uid, period = "rev-4", "2026-08"
    tokens_db.token_credit(uid, 50, ref="cs_keep")
    tokens_db.token_credit(uid, 120, ref="cs_drop")
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 170

    tokens_db.token_reverse_purchase("cs_drop")
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 50


def test_reversal_of_unknown_or_non_purchase_ref_is_a_noop(tokens_db):
    uid, period = "rev-5", "2026-08"
    tokens_db.token_credit(uid, 50, ref="cs_ref_5")
    spend = tokens_db.token_spend(uid, 5, free_quota=0, period=period)
    assert tokens_db.token_reverse_purchase("no-such-session") is None
    # A spend entry is not a purchase and must never be reversible this way.
    assert tokens_db.token_reverse_purchase(spend["entry_id"]) is None
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 45


def test_reversal_leaves_the_free_allowance_alone(tokens_db):
    """Free tokens were never bought, so a refund can't claw them back."""
    uid, period = "rev-6", "2026-08"
    tokens_db.token_credit(uid, 50, ref="cs_ref_6")
    tokens_db.token_spend(uid, 10, free_quota=20, period=period)  # free first
    tokens_db.token_reverse_purchase("cs_ref_6")
    snap = tokens_db.token_status(uid, period, 20)
    assert snap["purchased"] == 0
    assert snap["free_used"] == 10 and snap["free_remaining"] == 10


# --- partial refunds: two of the same size must both land -------------------

def test_the_ledger_applies_the_split_plan_spend_predicts(tokens_db):
    """Not a tautology: token_spend used to carry its own inline copy of this
    arithmetic, so every plan_spend assertion above could pass while the
    ledger did something else. Checked against the real transaction rather
    than by `is`, which in this suite only tests module-reload order."""
    uid, period, quota = "split", "2026-08", 6
    assert tokens_db.token_credit(uid, 20, ref="pack_split")["ok"]
    for cost in (5, 3, 4):
        snap = tokens_db.token_status(uid, period, quota)
        predicted = tokens_db.plan_spend(
            quota, snap["free_used"], snap["purchased"], cost)
        res = tokens_db.token_spend(uid, cost, free_quota=quota, period=period)
        assert res["ok"], res
        assert (res["free_part"], res["paid_part"]) == predicted


def test_two_equal_partial_refunds_both_credit(tokens_db):
    """A bulk batch refunds the failed cutouts mid-run and the unused
    remainder in its finally. When those are equal — a 4-photo batch where 2
    cutouts fail and the run then aborts — both were keyed `<entry>:2`, the
    second hit the unique ref, and db swallowed it. The seller was silently
    short."""
    uid, period = "partial", "2026-08"
    assert tokens_db.token_credit(uid, 10, ref="pack_1")["ok"]
    spend = tokens_db.token_spend(uid, 4, free_quota=0, period=period, feature="image_ai")
    assert spend["ok"] and spend["paid_part"] == 4
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 6

    assert tokens_db.token_refund(uid, spend["entry_id"], units=2) is True
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 8
    assert tokens_db.token_refund(uid, spend["entry_id"], units=2) is True
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 10


def test_partial_refunds_never_exceed_the_spend(tokens_db):
    """The clamp was against the spend total, not what was left of it, so
    without the ref collision that used to hide it, repeated partials would
    hand back more than was ever charged."""
    uid, period = "overrefund", "2026-08"
    assert tokens_db.token_credit(uid, 10, ref="pack_2")["ok"]
    spend = tokens_db.token_spend(uid, 4, free_quota=0, period=period, feature="image_ai")
    assert spend["ok"]
    for _ in range(3):
        tokens_db.token_refund(uid, spend["entry_id"], units=3)
    # 4 charged, at most 4 back — never 10 + 9.
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 10


def test_a_full_refund_is_still_replay_safe(tokens_db):
    """refund_all re-runs on every boot, so the full path must stay keyed on
    the bare entry id and no-op the second time."""
    uid, period = "replay", "2026-08"
    assert tokens_db.token_credit(uid, 10, ref="pack_3")["ok"]
    spend = tokens_db.token_spend(uid, 4, free_quota=0, period=period, feature="identify")
    assert tokens_db.token_refund(uid, spend["entry_id"]) is True
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 10
    assert tokens_db.token_refund(uid, spend["entry_id"]) is False
    assert tokens_db.token_status(uid, period, 0)["purchased"] == 10

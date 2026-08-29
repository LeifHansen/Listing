"""Charges taken up front, when the process holding the receipt is killed.

Every in-process refund path keeps the spend dict in a local variable, so a
machine that is killed rather than stopped (an OOM, a platform replacing the
VM) takes the only record of the charge with it: no finally runs, no refund
happens, and the seller has paid for a draft that was never saved. A later
boot has to settle up from what was written down.

The load-bearing safety property is in the ledger, not here: a FULL refund is
keyed by the spend's own entry id, so re-running this on every boot cannot pay
anyone twice. Which is exactly why only all-or-nothing charges may be recorded
— a partial refund is keyed by its amount instead, and would go through
alongside a full one.
"""
from __future__ import annotations

import pytest

from backend.services import tokens


# --- what gets written down -----------------------------------------------
def test_a_receipt_carries_what_a_refund_needs_and_nothing_else():
    spent = {"ok": True, "entry_id": "e1", "user_id": "u1",
             "free_remaining": 12, "purchased": 3, "tokens": 5}
    assert tokens.receipts(spent) == [
        {"ok": True, "entry_id": "e1", "user_id": "u1"}]


def test_a_receipt_round_trips_back_through_refund(monkeypatch):
    """The whole point: the mirrored dict has to be something refund() accepts,
    not a lookalike that silently no-ops."""
    calls = []
    monkeypatch.setattr(tokens.db, "token_refund",
                        lambda uid, eid, units=None: calls.append((uid, eid, units)))

    (receipt,) = tokens.receipts({"ok": True, "entry_id": "e1", "user_id": "u1"})
    tokens.refund(receipt)
    assert calls == [("u1", "e1", None)]


@pytest.mark.parametrize("spent", [
    None,                                             # billing off / free feature
    {"ok": False, "entry_id": "e1", "user_id": "u1"},  # declined
    {"ok": True, "user_id": "u1"},                     # un-metered: nothing to undo
])
def test_nothing_to_give_back_is_recorded_as_nothing(spent):
    assert tokens.receipts(spent) == []


def test_refund_all_is_a_full_refund_every_time(monkeypatch):
    """units must stay None. A partial refund is keyed in the ledger by its
    amount, which is what makes repeats unsafe."""
    calls = []
    monkeypatch.setattr(tokens.db, "token_refund",
                        lambda uid, eid, units=None: calls.append((uid, eid, units)))

    n = tokens.refund_all([{"ok": True, "entry_id": "e1", "user_id": "u1"},
                           {"ok": True, "entry_id": "e2", "user_id": "u1"}])
    assert n == 2
    assert [c[2] for c in calls] == [None, None]


def test_a_torn_or_missing_record_is_survivable(monkeypatch):
    monkeypatch.setattr(tokens.db, "token_refund", lambda *a, **k: None)
    assert tokens.refund_all(None) == 0
    assert tokens.refund_all([]) == 0
    assert tokens.refund_all(["not-a-dict", None]) == 0


# --- the ledger guarantee the startup pass leans on ------------------------
# Deliberately no free allowance, so spends and refunds run entirely against
# the PURCHASED balance. The free half is clamped (free_used never goes below
# zero), which quietly absorbs an over-refund and would let a real double-pay
# pass as fine — purchased tokens have no such floor, and they are the ones
# the seller actually paid money for.
QUOTA = 0


def _billing(dbmod, monkeypatch):
    monkeypatch.setattr(tokens, "enabled", lambda: True)
    monkeypatch.setattr(tokens.db, "token_spend", dbmod.token_spend)
    monkeypatch.setattr(tokens.db, "token_refund", dbmod.token_refund)
    monkeypatch.setattr(tokens.config, "FREE_TOKENS_PER_MONTH", QUOTA)
    dbmod.token_credit("u1", 100, ref="seed")


def _balance(dbmod):
    snap = dbmod.token_status("u1", tokens.period_now(), QUOTA)
    return snap["free_remaining"] + snap["purchased"]


def test_the_same_full_refund_twice_does_not_pay_twice(dbmod, monkeypatch):
    """Against a real ledger. This is the reason the startup pass needs no
    "already refunded" flag of its own, so if it ever stops being true the
    whole design is unsound and this must fail."""
    _billing(dbmod, monkeypatch)

    spent = tokens.spend("u1", "identify")
    assert spent and spent["ok"]
    charged = spent["free_part"] + spent["paid_part"]
    after_spend = _balance(dbmod)

    (receipt,) = tokens.receipts(spent)
    tokens.refund_all([receipt])
    assert _balance(dbmod) == after_spend + charged, "the first refund did not land"

    tokens.refund_all([receipt])          # the next boot tries again
    assert _balance(dbmod) == after_spend + charged, (
        "a second boot paid the seller a second time")


def test_a_full_refund_after_a_partial_one_returns_only_the_remainder(
        dbmod, monkeypatch):
    """A partial refund followed by a full one must return the charge exactly
    once. This used to hand back MORE than was charged — the two were separate
    ledger entries and nothing summed them — and the note here recorded that as
    the reason tokens.receipts refuses partially-refundable charges. The cap
    now lives in db.token_refund, computed from the ledger, so it survives the
    killed process that motivated the rule."""
    _billing(dbmod, monkeypatch)

    spent = tokens.spend("u1", "identify")
    charged = spent["free_part"] + spent["paid_part"]
    after_spend = _balance(dbmod)

    tokens.refund(spent, units=1)          # one unit back, in process
    tokens.refund(spent)                   # ...then a full refund on top

    assert _balance(dbmod) - after_spend == charged

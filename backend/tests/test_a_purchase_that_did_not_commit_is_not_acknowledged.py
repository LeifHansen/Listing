"""A token purchase whose commit failed is not "already applied".

token_credit and token_reverse_purchase caught EVERY exception on commit and
answered {"ok": True, "already": True} — the answer for losing the idempotency
race. The Stripe webhook acknowledged that with a 200, Stripe stopped
redelivering, no ledger row existed, and the seller had paid for tokens they
never received (or, for a reversal, kept tokens whose money had gone back).

Only an IntegrityError on the ref is "already". Anything else is None, which
handle_webhook turns into a 503 so Stripe tries again.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError


@pytest.fixture()
def db(dbmod):
    assert dbmod.create_user("buyer-1", "buyer@example.com", "not-a-real-hash")
    return dbmod


def _uid(db) -> str:
    return "buyer-1"


def _commit_raises(monkeypatch, db, exc):
    real = db.Session.commit
    calls = {"n": 0}

    def fake(self):
        calls["n"] += 1
        if calls["n"] == 1:
            self.rollback()
            raise exc
        return real(self)
    monkeypatch.setattr(db.Session, "commit", fake)
    return calls


def test_a_dropped_connection_mid_purchase_is_not_already(db, monkeypatch):
    uid = _uid(db)
    _commit_raises(monkeypatch, db, OperationalError("COMMIT", {}, Exception("gone")))
    assert db.token_credit(uid, 50, ref="cs_live_1") is None
    # Nothing landed, and the retry Stripe will make lands cleanly.
    assert db.token_credit(uid, 50, ref="cs_live_1") == {
        "ok": True, "already": False, "purchased": 50}


def test_losing_the_idempotency_race_is_still_already(db, monkeypatch):
    uid = _uid(db)
    _commit_raises(monkeypatch, db, IntegrityError("INSERT", {}, Exception("dup")))
    assert db.token_credit(uid, 50, ref="cs_live_2") == {"ok": True, "already": True}


def test_a_clawback_that_did_not_commit_is_retried(db, monkeypatch):
    uid = _uid(db)
    assert db.token_credit(uid, 50, ref="cs_live_3")["ok"]
    _commit_raises(monkeypatch, db, OperationalError("COMMIT", {}, Exception("gone")))
    assert db.token_reverse_purchase("cs_live_3", "refund") is None
    out = db.token_reverse_purchase("cs_live_3", "refund")
    assert out and out["already"] is False and out["reversed"] == 50

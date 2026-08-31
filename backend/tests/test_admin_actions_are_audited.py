"""Every admin MUTATION is written down before it runs.

The audit row is the difference between an operator console and a backdoor:
"who granted these tokens, who locked this account, and when" must always
have an answer, so db.admin_audit runs BEFORE the mutation and an action
that cannot be recorded does not run at all.

For token grants the two trails are tied together mechanically: the ledger
row's unique `ref` carries the audit row's id, which also makes a retried
grant a no-op instead of a double credit — the same idempotency the Stripe
webhook already relies on.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient

from backend import errors, main, ratelimit

PASSWORD = "password123"


@pytest.fixture()
def console(dbmod, monkeypatch):
    monkeypatch.setattr(main, "db", dbmod)
    ratelimit.reset()
    admin = TestClient(main.app)
    assert admin.post("/api/auth/signup",
                      json={"email": "op@example.com",
                            "password": PASSWORD}).status_code < 400
    admin_uid = dbmod.get_user_by_email("op@example.com")["id"]
    dbmod.set_user_role(admin_uid, "superadmin")
    dbmod.create_user("u-target", "t@example.com", "hash")
    return admin, dbmod, admin_uid


def test_a_grant_writes_one_audit_row_and_ties_the_ledger_to_it(console):
    admin, db, admin_uid = console

    res = admin.post("/api/admin/users/u-target/grant-tokens",
                     json={"tokens": 25, "note": "goodwill"})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "granted": 25, "already": False}

    rows = db.admin_audit_list()
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "grant_tokens"
    assert row["actor_id"] == admin_uid
    assert row["actor_email"] == "op@example.com"
    assert row["target_id"] == "u-target"
    assert row["data"] == {"tokens": 25, "note": "goodwill"}

    ledger = db.admin_ledger(user_id="u-target")
    assert len(ledger) == 1
    assert ledger[0]["kind"] == "grant"
    assert ledger[0]["tokens"] == 25
    assert ledger[0]["ref"] == f"admin:{row['id']}"


def test_a_replayed_grant_ref_credits_nothing(console):
    """The unique ref settles a retry the same way it settles the Stripe
    webhook race: the second application reports already=True and moves no
    tokens."""
    admin, db, _ = console
    assert admin.post("/api/admin/users/u-target/grant-tokens",
                      json={"tokens": 25}).status_code == 200
    ref = db.admin_ledger(user_id="u-target")[0]["ref"]

    replay = db.token_credit("u-target", 25, ref=ref, kind="grant")
    assert replay == {"ok": True, "already": True}
    assert len(db.admin_ledger(user_id="u-target")) == 1


def test_an_action_that_cannot_be_recorded_does_not_run(console, monkeypatch):
    """The ordering is the control. If the audit write fails, the grant and
    the revocation must not happen — an unrecorded admin action is the
    backdoor shape this table exists to prevent."""
    admin, db, _ = console

    def _boom(*a, **k):
        raise errors.StorageUnavailable("nope")

    monkeypatch.setattr(db, "admin_audit", _boom)
    res = admin.post("/api/admin/users/u-target/grant-tokens",
                     json={"tokens": 25})
    assert res.status_code == 503
    assert db.admin_ledger(user_id="u-target") == []

    res = admin.post("/api/admin/users/u-target/revoke-sessions")
    assert res.status_code == 503
    assert db.get_user_by_id("u-target")["sessions_valid_from"] is None


def test_a_forced_signout_is_audited_and_lands(console):
    admin, db, _ = console
    res = admin.post("/api/admin/users/u-target/revoke-sessions")
    assert res.status_code == 200
    assert db.get_user_by_id("u-target")["sessions_valid_from"] is not None
    assert [r["action"] for r in db.admin_audit_list()] == ["revoke_sessions"]


def test_disabling_locks_revokes_and_is_audited(console):
    admin, db, _ = console
    res = admin.post("/api/admin/users/u-target/disable",
                     json={"disabled": True})
    assert res.status_code == 200, res.text

    row = db.get_user_by_id("u-target")
    assert row["disabled_at"] is not None
    assert row["sessions_valid_from"] is not None, \
        "a lockout must reach tokens that are already minted"

    assert admin.post("/api/admin/users/u-target/disable",
                      json={"disabled": False}).status_code == 200
    assert db.get_user_by_id("u-target")["disabled_at"] is None
    assert [r["action"] for r in db.admin_audit_list()] == \
        ["enable_account", "disable_account"]


def test_the_console_cannot_disable_its_own_operators(console):
    """Yourself: you'd be locking yourself out of the console that unlocks
    accounts. Another superadmin: removing an operator goes through the
    grant script — deliberate, audited, out-of-band — not a console click."""
    admin, db, admin_uid = console
    db.create_user("u-op2", "op2@example.com", "hash")
    db.set_user_role("u-op2", "superadmin")

    for target in (admin_uid, "u-op2"):
        res = admin.post(f"/api/admin/users/{target}/disable",
                         json={"disabled": True})
        assert res.status_code == 400, target
        assert db.get_user_by_id(target)["disabled_at"] is None
    assert db.admin_audit_list() == [], "refusals are not actions"


def test_a_grant_is_bounded_and_typed(console):
    """A typo'd magnitude is a real balance somebody spends; there is no
    undo that claws back what was already used."""
    admin, db, _ = console
    for bad in ({"tokens": 0}, {"tokens": -5}, {"tokens": 1000001},
                {"tokens": "lots"}, {}, None):
        res = admin.post("/api/admin/users/u-target/grant-tokens", json=bad)
        assert res.status_code == 400, bad
    assert db.admin_ledger(user_id="u-target") == []
    assert db.admin_audit_list() == []

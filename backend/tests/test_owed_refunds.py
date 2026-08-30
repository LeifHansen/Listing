"""A refund that did not commit is money the seller does not get back.

"Only pay for AI that worked" is the promise. When the AI fails, the charge is
given back — `tokens.refund` calls `db.token_refund`, which returns False if
the write did not happen, and `tokens.refund` threw that answer away.

So a database blip in the refund window meant: the seller was charged, the AI
failed, the refund never landed, and nothing anywhere recorded that it was
owed. The existing recovery — `_settle_interrupted_jobs` — only covers jobs
whose PROCESS died; a job that finished normally with a failed refund was
never revisited by anything.

The debt is recorded on the VOLUME, not in the database. That is the whole
point: the reason the refund failed is usually that the database is
unreachable, so a debt written there would fail for the same reason. The
jobstore mirror already works this way, for the same reason.

Retrying is safe by construction: a full refund is keyed in the ledger by the
spend's own entry id, so the database rejects a second one rather than paying
the seller twice.
"""
from __future__ import annotations

import pytest

from backend.services import owed_refunds


@pytest.fixture()
def store(monkeypatch, tmp_path):
    from backend import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(owed_refunds, "_DIR", tmp_path / "owed-refunds")
    return owed_refunds


RECEIPT = {"ok": True, "entry_id": "ledger-1", "user_id": "u1"}


# ------------------------------------------------------------ recording

def test_a_failed_refund_is_written_down(store):
    store.owe(RECEIPT)

    owed = store.pending()
    assert [o["entry_id"] for o in owed] == ["ledger-1"]
    assert owed[0]["user_id"] == "u1"


def test_a_partial_refund_records_its_size(store):
    """A partial refund is keyed by amount, so retrying it needs the amount."""
    store.owe(RECEIPT, units=3)
    assert store.pending()[0]["units"] == 3


def test_the_same_debt_twice_is_one_debt(store):
    """The same failing refund can be attempted more than once before a pass
    ever runs. Two rows would be two retries of the same money."""
    store.owe(RECEIPT)
    store.owe(RECEIPT)

    assert len(store.pending()) == 1


def test_a_full_and_a_partial_of_one_spend_are_different_debts(store):
    """They are different amounts against the same ledger entry, and the
    ledger keys them apart — so this must too."""
    store.owe(RECEIPT)
    store.owe(RECEIPT, units=3)

    assert len(store.pending()) == 2


def test_a_receipt_with_nothing_to_refund_is_not_recorded(store):
    """A declined or un-metered spend bought nothing and owes nothing."""
    store.owe(None)
    store.owe({"ok": False, "entry_id": "x"})
    store.owe({"ok": True})

    assert store.pending() == []


def test_recording_never_raises(store, monkeypatch):
    """It runs on a failure path, often inside a `finally`. Throwing here
    would replace a lost refund with a lost response."""
    monkeypatch.setattr(store, "_DIR", None)
    store.owe(RECEIPT)  # must not raise


# ------------------------------------------------------------- settling

def test_a_settled_debt_stops_being_owed(store, monkeypatch):
    from backend import db

    store.owe(RECEIPT)
    monkeypatch.setattr(db, "token_refund", lambda *a, **k: True)

    assert store.settle() == 1
    assert store.pending() == []


def test_a_debt_that_still_cannot_be_paid_is_kept(store, monkeypatch):
    from backend import db

    store.owe(RECEIPT)
    monkeypatch.setattr(db, "token_refund", lambda *a, **k: False)

    assert store.settle() == 0
    assert len(store.pending()) == 1, "an unpaid refund was written off"


def test_settling_passes_the_partial_amount_through(store, monkeypatch):
    from backend import db

    seen: list = []
    store.owe(RECEIPT, units=3)
    monkeypatch.setattr(db, "token_refund",
                        lambda uid, entry, units=None: seen.append(units) or True)

    store.settle()
    assert seen == [3]


def test_one_stuck_debt_does_not_block_the_others(store, monkeypatch):
    from backend import db

    store.owe(RECEIPT)
    store.owe({"ok": True, "entry_id": "ledger-2", "user_id": "u2"})
    monkeypatch.setattr(db, "token_refund",
                        lambda uid, entry, units=None: entry != "ledger-1")

    assert store.settle() == 1
    assert [o["entry_id"] for o in store.pending()] == ["ledger-1"]


def test_settling_nothing_is_fine(store):
    assert store.settle() == 0


def test_settling_never_raises(store, monkeypatch):
    monkeypatch.setattr(store, "_DIR", None)
    assert store.settle() == 0


# --------------------------------------------- and tokens.refund uses it

def test_a_refund_that_fails_becomes_a_recorded_debt(store, monkeypatch):
    from backend import db
    from backend.services import tokens

    monkeypatch.setattr(db, "token_refund", lambda *a, **k: False)
    tokens.refund({"ok": True, "entry_id": "ledger-9", "user_id": "u1"})

    assert [o["entry_id"] for o in store.pending()] == ["ledger-9"]


def test_a_refund_that_works_records_nothing(store, monkeypatch):
    from backend import db
    from backend.services import tokens

    monkeypatch.setattr(db, "token_refund", lambda *a, **k: True)
    tokens.refund({"ok": True, "entry_id": "ledger-9", "user_id": "u1"})

    assert store.pending() == []

"""A restart must not re-arm the eBay sweep for every account at once.

`sync_guard` rations the per-item probe sweeps -- up to ~100 Trading calls per
account -- to once per six hours, because eBay's Trading quota is a DAILY
allowance for the whole application (5,000 calls by default) and the app
re-checks statuses in the background while a tab is open. The module's own
docstring says what running out costs: "once it's gone EVERY Trading call
fails -- including the AddFixedPriceItem that publishes a listing. Publishes
then fail for no reason the seller can see or fix."

The cooldown that prevents that lived in a process-local dict, so it was
forgotten on every restart -- and a restart is not a rare event here: it is
every deploy, every OOM (background removal is the app's memory-hungry step),
and every machine move. Each one re-armed the sweep for EVERY account with a
tab open, at ~100 calls apiece, against an allowance shared by every seller.
Six deploys in a working day with a dozen active sellers is the whole quota,
and the sellers who then cannot publish did nothing wrong and can do nothing
about it.

So the mark has to outlive the process. It is a stamp on the user row, read
only when the in-memory answer says the sweep might be due -- i.e. on the call
that is about to spend the quota, never on the ordinary polls that are already
cooling down.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from backend.errors import StorageUnavailable
from backend.services import sync_guard


def _restart() -> None:
    """What a deploy does to this module: the process-local dict goes away.

    Deliberately NOT sync_guard.reset() -- that is the operator's "sweep on the
    next sync" control and is meant to forget the durable mark too. This is the
    involuntary kind of forgetting, which must not.
    """
    sync_guard._last_sweep.clear()


@pytest.fixture()
def store(dbmod, monkeypatch):
    """A real database, and a user with an account row to stamp."""
    sync_guard.reset()
    dbmod.create_user("u1", "a@b.c", "hash")
    dbmod.create_user("u2", "d@e.f", "hash")
    monkeypatch.setattr(sync_guard, "_store", lambda: dbmod)
    yield dbmod
    sync_guard.reset()


# ------------------------------------------------------- the regression

def test_a_restart_does_not_re_arm_the_sweep(store):
    assert sync_guard.sweep_due("u1") is True, "the first sweep is free"
    _restart()
    # The whole finding: with the mark in process memory alone, this was True
    # again -- one deploy handed every account with an open tab a fresh
    # ~100-call sweep against a quota shared by every seller.
    assert sync_guard.sweep_due("u1") is False


def test_the_cooldown_stays_per_account_across_a_restart(store):
    assert sync_guard.sweep_due("u1") is True
    _restart()
    # u2 has never swept, so its first check is still free -- a durable mark
    # must not become a global one.
    assert sync_guard.sweep_due("u2") is True
    assert sync_guard.sweep_due("u1") is False


def test_the_durable_mark_expires_like_the_in_memory_one(store):
    assert sync_guard.sweep_due("u1") is True
    _restart()
    # Age the stamp past the window rather than sleeping through six hours.
    stale = (_dt.datetime.now(_dt.timezone.utc)
             - _dt.timedelta(seconds=sync_guard.COOLDOWN_SECONDS + 60))
    store.mark_sweep("u1", at=stale)
    _restart()
    assert sync_guard.sweep_due("u1") is True


def test_a_deliberate_sync_still_runs_after_a_restart(store):
    assert sync_guard.sweep_due("u1") is True
    _restart()
    # The "Sync with eBay" button is an explicit request and must never be
    # silently downgraded to the cheap pass.
    assert sync_guard.sweep_due("u1", force=True) is True


def test_a_forced_sweep_re_arms_the_durable_cooldown(store):
    assert sync_guard.sweep_due("u1", force=True) is True
    _restart()
    # A force refreshes the cooldown, so the next background poll -- including
    # one after a restart -- must not sweep again.
    assert sync_guard.sweep_due("u1") is False


# --------------------------------------------- what an outage may not do

def test_an_unreadable_mark_does_not_grant_a_background_sweep(store, monkeypatch):
    def _down(*_a, **_k):
        raise StorageUnavailable("nope")

    monkeypatch.setattr(store, "last_sweep", _down)
    # We cannot tell whether this account swept ten seconds ago or never. A
    # background poll is not worth ~100 calls of a shared daily quota on that
    # unknown, and the cheap finished-list pass still runs either way.
    assert sync_guard.sweep_due("u1") is False


def test_a_deliberate_sync_survives_an_unreadable_mark(store, monkeypatch):
    def _down(*_a, **_k):
        raise StorageUnavailable("nope")

    monkeypatch.setattr(store, "last_sweep", _down)
    monkeypatch.setattr(store, "mark_sweep", _down)
    # A person pressed the button. Refusing it because a bookkeeping row could
    # not be read would be an outage turned into a silently downgraded sync.
    assert sync_guard.sweep_due("u1", force=True) is True


def test_a_sweep_that_cannot_be_recorded_is_not_granted(store, monkeypatch):
    def _down(*_a, **_k):
        raise StorageUnavailable("nope")

    monkeypatch.setattr(store, "mark_sweep", _down)
    # Same rule the rest of this branch follows for anything metered: if the
    # spend cannot be written down, it cannot be rationed, so it is not spent.
    assert sync_guard.sweep_due("u1") is False


# ------------------------------------------------ no database configured

def test_without_a_database_the_cooldown_is_memory_only(monkeypatch):
    from backend import db

    sync_guard.reset()
    monkeypatch.setattr(db.config, "DATABASE_URL", "")
    # Running without DATABASE_URL is a supported configuration, not a
    # failure: there is nowhere durable to put the mark, so the in-process
    # cooldown is the whole answer and must still work.
    assert sync_guard.sweep_due("u1") is True
    assert sync_guard.sweep_due("u1") is False
    sync_guard.reset()

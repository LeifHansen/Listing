"""How often a background sync may spend eBay API calls.

eBay caps Trading API calls per DAY for the whole application, and a store
sync is not one call: the per-item probe sweeps cost up to ~100 (60 imported
+ 40 app listings, each its own round trip). The cheap half of a sync —
eBay's own sold/unsold lists — is two calls and is what actually moves a
record that ended or sold, so the sweeps only exist to correct what those
lists miss.

That distinction matters because the app re-checks statuses in the
background while a tab is open. Running the sweeps on every one of those
checks drains the day's whole allowance from a single tab, and once it's
gone EVERY Trading call fails — including the AddFixedPriceItem that
publishes a listing. Publishes then fail for no reason the seller can see or
fix: the listing is fine, the quota is gone.

So background syncs run the cheap pass and take the sweeps at most once per
cooldown, while a deliberate sync (the "Sync with eBay" button) always runs
in full.

The mark outlives the process. It used to live in this module's dict alone,
which meant a restart forgave it for every account at once — and a restart
here is every deploy, every OOM (background removal is the memory-hungry
step) and every machine move, each one re-arming a ~100-call sweep for every
account with a tab open. Six deploys in a working day across a dozen active
sellers is the whole application allowance, spent on accounts that were
already up to date, and the sellers who then cannot publish did nothing and
can do nothing. The dict is now a cache in front of a stamp on the user row:
it answers the ordinary polls (which are cooling down and cost nothing), and
the row is consulted only on the call that is actually about to spend.

Deliberately independent of backend.main: the CI suite must be able to test
this without the app's heavy image/AI imports. `backend.db` is imported
lazily for the same reason — this module is collected in a job that has no
SQLAlchemy, and a module-scope import would take the whole suite down with
it.
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
import time

from ..config import log
from ..errors import StorageUnavailable

# Six hours by default: long enough that a day of background checks costs a
# handful of sweeps rather than hundreds, short enough that a record the
# finished-lists pass can't see is still corrected several times a day.
COOLDOWN_SECONDS = float(
    os.getenv("EBAY_SWEEP_COOLDOWN_SECONDS", "21600") or 21600)

_last_sweep: dict[str, float] = {}
_lock = threading.Lock()

# Nothing ever removed from this dict, so on an always-on machine it gained a
# row per account that ever synced and never gave one back. An entry older
# than the cooldown answers exactly the same as no entry at all, so past this
# many they are dropped on the next call rather than kept forever.
_MAX_TRACKED = 4096


def _store():
    """The durable home for the mark.

    Imported here rather than at module scope so this file stays importable
    without SQLAlchemy — see the note in the module docstring. Also the seam
    the tests bind a scratch database to.
    """
    from .. import db
    return db


def _epoch(stamp: _dt.datetime) -> float:
    """A stored stamp as epoch seconds, in UTC.

    SQLite hands back naive datetimes and Postgres aware ones. Treating a
    naive one as local time would shift the cooldown by the machine's offset,
    and shifting it in the lenient direction is a sweep that runs early.
    """
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    return stamp.timestamp()


def _remember(user_id: str, at: float, now: float) -> None:
    """Cache a sweep time in process memory. Caller holds the lock."""
    _last_sweep[user_id] = at
    if len(_last_sweep) > _MAX_TRACKED:
        for key in [k for k, t in _last_sweep.items()
                    if now - t >= COOLDOWN_SECONDS]:
            _last_sweep.pop(key, None)


def sweep_due(user_id: str, force: bool = False) -> bool:
    """True when the per-item probe sweeps may run for this account.

    `force` is the deliberate sync — it always runs, and refreshes the
    cooldown so the next background check doesn't immediately sweep again.
    Records the timestamp when it returns True, so concurrent callers (two
    tabs polling at once) can't both get a yes.

    Ask only when there is something to sweep: a call that returns True STARTS
    the cooldown, so asking speculatively spends the whole window on nothing.

    A background sweep needs the cooldown PROVEN, and needs the spend
    recorded before it happens. When the mark can be neither read nor written
    — a database blip — the answer is no: an unreadable mark is not evidence
    that nothing has swept, and a spend nobody wrote down cannot be rationed
    (the next process to start would grant the same account another one).
    Refusing costs the seller nothing they will notice, because the cheap
    finished-list pass runs either way and it is what actually moves an ended
    or sold record. A forced sync is exempt from both: a person pressed the
    button, and turning a bookkeeping outage into a silently downgraded sync
    is the failure this branch exists to remove.

    Running with no DATABASE_URL is a supported configuration rather than a
    failure, and there the in-process cooldown is the whole answer.
    """
    now = time.time()
    # The lock is held across the database round trip, which is deliberate:
    # it is what stops two tabs polling at once from both being told yes. It
    # is affordable because the query happens only on the path that is about
    # to spend ~100 eBay calls — every ordinary poll returns above without
    # touching it — and because the pool is configured to fail fast rather
    # than hang (connect_timeout=3, pool_timeout=5 in db._get_engine), so the
    # worst case is a few seconds, not an indefinite stall.
    with _lock:
        if not force and now - _last_sweep.get(user_id, 0.0) < COOLDOWN_SECONDS:
            return False            # cooling down: no query, no sweep
        store = _store()
        durable = store.enabled()
        if durable and not force:
            try:
                mark = store.last_sweep(user_id)
            except StorageUnavailable as exc:
                log.info("sweep: cooldown unreadable for user=%s (%s) — "
                         "skipping the per-item sweeps", user_id, exc)
                return False
            if mark is not None:
                swept = _epoch(mark)
                if now - swept < COOLDOWN_SECONDS:
                    # Survived a restart. Cache it so the polls that follow
                    # answer from memory instead of querying every time.
                    _remember(user_id, swept, now)
                    return False
        if durable:
            try:
                store.mark_sweep(user_id)
            except StorageUnavailable as exc:
                if not force:
                    log.info("sweep: couldn't record the spend for user=%s "
                             "(%s) — skipping the per-item sweeps",
                             user_id, exc)
                    return False
                # Forced: the sweep runs, but say plainly that the cooldown it
                # should have started did not survive this process.
                log.warning("sweep: forced sync for user=%s ran without "
                            "recording its cooldown (%s)", user_id, exc)
        _remember(user_id, now, now)
        return True


def reset(user_id: str = "") -> None:
    """Forget the last-sweep time (all accounts when no id is given).

    For tests, and for an operator who wants the next sync to sweep. Clears
    process memory only: the durable mark is per-account and there is no
    bulk-clear worth having on a table this app writes one row at a time. An
    operator who means it for one account ages that row directly.
    """
    with _lock:
        if user_id:
            _last_sweep.pop(user_id, None)
        else:
            _last_sweep.clear()

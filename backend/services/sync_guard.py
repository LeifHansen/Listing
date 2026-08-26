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

Stdlib only, and deliberately independent of backend.main: the CI suite must
be able to test this without the app's heavy image/AI imports.
"""
from __future__ import annotations

import os
import threading
import time

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


def sweep_due(user_id: str, force: bool = False) -> bool:
    """True when the per-item probe sweeps may run for this account.

    `force` is the deliberate sync — it always runs, and refreshes the
    cooldown so the next background check doesn't immediately sweep again.
    Records the timestamp when it returns True, so concurrent callers (two
    tabs polling at once) can't both get a yes.

    Ask only when there is something to sweep: a call that returns True STARTS
    the cooldown, so asking speculatively spends the whole window on nothing.
    """
    now = time.time()
    with _lock:
        if not force and now - _last_sweep.get(user_id, 0.0) < COOLDOWN_SECONDS:
            return False
        _last_sweep[user_id] = now
        if len(_last_sweep) > _MAX_TRACKED:
            for key in [k for k, t in _last_sweep.items()
                        if now - t >= COOLDOWN_SECONDS]:
                _last_sweep.pop(key, None)
        return True


def reset(user_id: str = "") -> None:
    """Forget the last-sweep time (all accounts when no id is given). For
    tests, and for an operator who wants the next sync to sweep."""
    with _lock:
        if user_id:
            _last_sweep.pop(user_id, None)
        else:
            _last_sweep.clear()

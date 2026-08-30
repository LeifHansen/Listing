"""A small fixed-window rate limiter for the auth endpoints.

Login, signup, and the account-delete password check are the only endpoints
an attacker can hammer for free: every other expensive path sits behind the
AI token gate, but password guessing and signup spam cost nothing. Bcrypt
makes each attempt expensive for US too, so an unthrottled login is a CPU
exhaustion vector on a shared-cpu VM as much as a credential-stuffing one.

Deliberately in-process rather than Redis-backed: fly.toml runs a single
always-on machine, so a dict is an accurate view of all traffic and adds no
dependency. Past one machine the limit becomes per-machine — still a
meaningful ceiling, and worth revisiting only if the app ever scales out.

Kept out of main.py so the tests can exercise it without importing the image
stack (CI installs only the light dependencies).
"""
from __future__ import annotations

import threading
import time

WINDOW_SECONDS = 900        # 15 minutes
MAX_ATTEMPTS = 10           # per client, per window, per bucket
# The photo-studio endpoints run the local rembg/ONNX model, which is
# serialized process-wide behind a single inference lock — so an unmetered
# flood there stalls every seller's background removal at once, and (because
# those handlers occupy threadpool slots) eventually the whole site. They
# cannot be metered like the other AI features: the border re-check fires
# automatically after every crop and save, so charging it would bill people
# for ordinary editing. A generous ceiling instead — far above real editing,
# far below what it takes to wedge the machine.
STUDIO_MAX_CALLS = 120
# The eBay lookups that need no login: category suggestions, item aspects and
# price comps. They call eBay with the APPLICATION token, which is why they
# need no seller — and why an unauthenticated flood spends an allowance shared
# by EVERY seller (eBay's default is 5,000 calls a day for the whole
# application). The answers are cached, but the cache is bounded, so distinct
# queries evict it and force a live call each.
#
# Exhausting it does not degrade the attacker; it degrades every seller at
# once — no categories, no item specifics, no price comps, on a listing they
# are trying to publish. Same argument as STUDIO_MAX_CALLS above, with a
# third-party quota in place of the CPU.
#
# Generous on purpose: an identify looks up categories, aspects and comps per
# item and a bulk batch does that for a pile, so this has to sit far above a
# real session and far below a day's allowance.
TAXONOMY_MAX_CALLS = 300
# Cap the number of tracked keys so a spray across many IPs can't grow the
# dict without bound; cold entries are swept when the cap is reached, and if
# none are cold the coldest are dropped anyway (see check).
_MAX_KEYS = 2048

_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


def check(key: str, now: float | None = None,
          max_attempts: int | None = None) -> bool:
    """Record an attempt for `key`; return True when it is still within the
    allowance and False once the window's limit is exceeded.

    Every attempt is counted, successful or not: a real person logs in rarely
    enough never to notice, while credential stuffing trips it immediately.

    `max_attempts` overrides the default for callers whose traffic is
    legitimately chattier than an auth endpoint (see STUDIO_MAX_CALLS).
    """
    now = time.time() if now is None else now
    limit = MAX_ATTEMPTS if max_attempts is None else max_attempts
    with _lock:
        hits = [t for t in _hits.get(key, []) if now - t < WINDOW_SECONDS]
        hits.append(now)
        _hits[key] = hits
        if len(_hits) > _MAX_KEYS:
            _evict(now, key)
        return len(hits) <= limit


def _evict(now: float, keep: str) -> None:
    """Bring the tracked-key count back under the cap. Caller holds the lock.

    Expired keys go first — they are free, and in normal traffic they are all
    there is to drop. But a burst that touches thousands of distinct keys
    inside ONE window leaves nothing expired to collect, which is exactly the
    spray _MAX_KEYS exists to survive: the sweep found no candidates, the dict
    kept growing, and the cap was a comment rather than a bound. So when the
    cheap pass isn't enough, fall back to dropping the coldest keys until the
    dict fits.

    Dropping a key forgives its attempts so far, which is why the coldest go
    first: a key idle since early in the window is the one least likely to be
    mid-flood, and a live attacker's own key is the last thing evicted (never
    `keep`, the caller's, which was just touched). Under a spray big enough to
    reach here the attacker is paying for the eviction of their own earlier
    keys, one bcrypt hash at a time.
    """
    for k in [k for k, v in _hits.items()
              if k != keep and (not v or now - v[-1] > WINDOW_SECONDS)]:
        _hits.pop(k, None)
    if len(_hits) <= _MAX_KEYS:
        return
    coldest = sorted((v[-1] if v else 0.0, k) for k, v in _hits.items()
                     if k != keep)
    for _last, k in coldest[:len(_hits) - _MAX_KEYS]:
        _hits.pop(k, None)


def reset() -> None:
    """Forget all state (tests)."""
    with _lock:
        _hits.clear()

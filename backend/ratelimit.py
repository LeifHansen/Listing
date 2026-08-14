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
# Cap the number of tracked keys so a spray across many IPs can't grow the
# dict without bound; cold entries are swept when the cap is reached.
_MAX_KEYS = 2048

_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


def check(key: str, now: float | None = None) -> bool:
    """Record an attempt for `key`; return True when it is still within the
    allowance and False once the window's limit is exceeded.

    Every attempt is counted, successful or not: a real person logs in rarely
    enough never to notice, while credential stuffing trips it immediately.
    """
    now = time.time() if now is None else now
    with _lock:
        hits = [t for t in _hits.get(key, []) if now - t < WINDOW_SECONDS]
        hits.append(now)
        _hits[key] = hits
        if len(_hits) > _MAX_KEYS:
            for k in [k for k, v in _hits.items()
                      if not v or now - v[-1] > WINDOW_SECONDS]:
                _hits.pop(k, None)
        return len(hits) <= MAX_ATTEMPTS


def reset() -> None:
    """Forget all state (tests)."""
    with _lock:
        _hits.clear()

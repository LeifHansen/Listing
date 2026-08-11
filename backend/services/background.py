"""Fire-and-forget background execution for mirror/bookkeeping work."""
from __future__ import annotations

import threading

from ..config import log


def run_in_background(fn, *args, what: str = "") -> None:
    """Run fn(*args) on a daemon thread — for mirror/bookkeeping work (R2
    pushes/deletes, updated_at bumps, directory cleanup) that shouldn't hold
    up the response. The user-visible change is already done locally by the
    time this runs; failures are logged, never surfaced."""
    def _run() -> None:
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 - background work is best-effort
            log.warning("background %s failed: %s",
                        what or getattr(fn, "__name__", "task"), exc)
    threading.Thread(target=_run, daemon=True).start()

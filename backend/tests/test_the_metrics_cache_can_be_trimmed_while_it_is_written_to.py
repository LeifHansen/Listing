"""Trimming the metrics cache never fails because another request wrote to it.

The listing-metrics caches are plain dicts shared by every request thread,
written without a lock. _make_room walked the live dict, so a second request
inserting its entry mid-walk raised "dictionary changed size during
iteration" -- and the dashboard answered 500 over a cache trim. It walks a
snapshot now.
"""
from __future__ import annotations

import sys
import threading
import time

import pytest

pytest.importorskip("httpx")

from backend.services import metrics  # noqa: E402


def test_a_trim_survives_a_writer_on_another_thread():
    cache = {f"k{i}": (time.time(), {}, set()) for i in range(5000)}
    stop = threading.Event()

    def _writer():
        i = 0
        while not stop.is_set():
            cache[f"w{i}"] = (time.time(), {}, set())
            cache.pop(f"w{i - 1}", None)
            i += 1

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads as often as possible
    writer = threading.Thread(target=_writer)
    writer.start()
    try:
        for _ in range(200):
            metrics._make_room(cache, ttl=3600, cap=10**9)
    finally:
        stop.set()
        writer.join()
        sys.setswitchinterval(old)


def test_it_still_drops_the_stale_and_then_the_oldest():
    now = time.time()
    cache = {"stale": (now - 999, {}, set()), "old": (now - 5, {}, set()),
             "new": (now, {}, set())}
    metrics._make_room(cache, ttl=60, cap=2)
    assert set(cache) == {"new"}

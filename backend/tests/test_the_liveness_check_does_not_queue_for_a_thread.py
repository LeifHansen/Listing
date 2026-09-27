"""/api/health answers on the event loop, never in the threadpool's queue.

Fly polls /api/health every 15 seconds with a 5-second timeout and replaces
the machine when it misses — killing whatever batch was running. As a plain
`def` it ran in the threadpool, where it waited behind every sync handler
holding a slot on a slow eBay call, so a busy afternoon could fail liveness on
a perfectly live process. Nothing it reads touches the network or the disk.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

from backend import main  # noqa: E402


def test_the_liveness_route_is_a_coroutine():
    route = next(r for r in main.app.routes
                 if getattr(r, "path", "") == "/api/health")
    assert inspect.iscoroutinefunction(route.endpoint), (
        "a sync handler waits for a threadpool slot before it can say it is alive")


def test_it_still_answers_what_the_ui_reads():
    body = asyncio.run(main.health())
    assert body["ok"] is True
    for key in ("build", "anthropic_configured", "google_ai_configured",
                "identify_provider", "ebay_configured", "taxonomy_configured",
                "ended_grace_days"):
        assert key in body

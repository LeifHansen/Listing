"""A reference page has a total deadline, not just a per-read one.

The reader's docstring promised "a total deadline". What it had was httpx's
timeout, which bounds each read separately -- and each chunk that arrives
starts the clock again. A server that sends a byte every few seconds never
trips it, so one reference link could hold a background thread for as long
as the server cared to keep dripping, times three attempts, times four
redirect hops.

So the whole fetch now runs against one clock, checked between chunks,
between attempts and before every wait, and running out of it is a
TemporaryFailure: the page has not been seen, which says nothing about it.
"""
from __future__ import annotations

import time

import pytest

httpx = pytest.importorskip("httpx")

from backend.services import reference_fetch as rf  # noqa: E402


def _pin_transport(monkeypatch, transport):
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_a_page_that_drips_in_is_given_up_on_at_the_deadline(monkeypatch):
    monkeypatch.setattr(rf, "check", lambda url: (url, ["93.184.216.34"]))
    monkeypatch.setattr(rf, "DEADLINE", 0.5, raising=False)

    def drip():
        for _ in range(200):          # four seconds of page, a chunk at a time
            time.sleep(0.02)
            yield b"<p>Fenton glass marks by decade.</p>"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=drip())

    _pin_transport(monkeypatch, httpx.MockTransport(handler))
    started = time.monotonic()
    with pytest.raises(rf.TemporaryFailure):
        rf.fetch("https://slow.example.test/marks")
    assert time.monotonic() - started < 2.0


def test_the_waits_between_attempts_count_against_the_same_clock(monkeypatch):
    """A site that answers 429 with "come back in ten seconds" is not waited
    on past the deadline: the wait is cut short and the fetch gives up."""
    monkeypatch.setattr(rf, "check", lambda url: (url, ["93.184.216.34"]))
    monkeypatch.setattr(rf, "DEADLINE", 0.3, raising=False)
    slept = []
    real_sleep = time.sleep
    monkeypatch.setattr(rf.time, "sleep",
                        lambda s: (slept.append(s), real_sleep(min(s, 0.4))))

    def handler(request):
        return httpx.Response(429, headers={"retry-after": "10"})

    _pin_transport(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(rf.TemporaryFailure):
        rf.fetch("https://busy.example.test/marks")
    assert slept and max(slept) <= 0.3


def test_an_ordinary_page_is_read_well_inside_the_deadline(monkeypatch):
    monkeypatch.setattr(rf, "check", lambda url: (url, ["93.184.216.34"]))

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<p>Hallmarks</p>")

    _pin_transport(monkeypatch, httpx.MockTransport(handler))
    assert "Hallmarks" in rf.fetch("https://example.test/marks")
    assert rf.TIMEOUT <= rf.DEADLINE <= 120

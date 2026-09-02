"""Nothing recorded or logged carries a credential or a seller's identity.

This matters more than it did. Until now the only reader of a log line was a
person running a workflow by hand against a window measured in hours. Error
text is now persisted in a database, served over an API, archived, and read by
an automated job — so a secret that reaches a log line no longer expires.

The repository has already paid for this once: commit b0e8498 stopped a logged
database error from carrying the data that caused it, found by sweeping every
`log.*` call in the backend. That was per-call-site discipline, and it remains
the real rule (crypto.py logs `type(exc).__name__`, never the value). What is
tested here is the BACKSTOP under it, for the ones that slip: client IPs,
seller emails, Stripe ids, OAuth codes in an httpx exception's URL.

The support reference must survive. It is not a secret, it identifies nothing
on its own, and it is the entire join between a seller's complaint and a row.
Redacting it would make the record useless in the name of protecting nothing.
"""
from __future__ import annotations

import io
import logging

import pytest

pytest.importorskip("sqlalchemy")

from backend import redact
from backend.services import errorlog

LEAKY = (
    "stripe charge pi_3ABCdefGHIjklMNO failed with sk_live_51H4xAbCdEfGh "
    "for seller@example.com from 203.0.113.9, token=ya29.AveryLongTokenValue, "
    "session eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl, "
    "GET https://api.ebay.com/oauth?client_secret=shhhhhh&code=abc123def "
    "digest 5d41402abc4b2a76b9719d911017c592aabbccddeeff0011"
)

MUST_NOT_APPEAR = (
    "sk_live_51H4xAbCdEfGh", "seller@example.com", "203.0.113.9",
    "ya29.AveryLongTokenValue", "eyJhbGciOiJIUzI1NiJ9", "shhhhhh",
    "abc123def", "5d41402abc4b2a76b9719d911017c592aabbccddeeff0011",
)


def test_none_of_it_survives_a_scrub():
    cleaned = redact.scrub(LEAKY)
    for secret in MUST_NOT_APPEAR:
        assert secret not in cleaned, secret


def test_the_shape_survives_so_the_line_stays_diagnosable():
    """Redaction that erases the KIND of secret erases the diagnosis with it.

    "a live Stripe key was in play" is the first thing an operator needs; the
    key itself is the one thing they must not have.
    """
    cleaned = redact.scrub(LEAKY)
    assert "sk_live_<redacted>" in cleaned
    assert "<email>" in cleaned and "<ip>" in cleaned and "<jwt>" in cleaned


def test_the_support_reference_and_the_build_sha_survive():
    """Both are short hex and both must be readable — they are the joins."""
    cleaned = redact.scrub("lookup failed [a1b2c3d4] at build 62ec7e8")
    assert "a1b2c3d4" in cleaned
    assert "62ec7e8" in cleaned


def test_the_log_line_itself_is_scrubbed(monkeypatch):
    """The args, not the format string, are where the secret actually is.

    `log.warning("...: %s", exc)` is the shape used at essentially every call
    site in this codebase, so a redactor that only saw the template would
    protect nothing at all.
    """
    from backend import config

    handler = config.log.handlers[0]
    buffer = io.StringIO()
    monkeypatch.setattr(handler, "stream", buffer, raising=False)
    config.log.warning("stripe failed: %s", LEAKY)

    written = buffer.getvalue()
    assert written, "the line should still be emitted, just cleaned"
    for secret in MUST_NOT_APPEAR:
        assert secret not in written, secret


def test_the_recorded_row_is_scrubbed_in_message_and_traceback(dbmod):
    try:
        raise ValueError(LEAKY)
    except ValueError as exc:
        errorlog.record(kind="backend", level="ERROR", exc=exc)
    errorlog.flush()

    rows = dbmod.error_events_list()
    assert len(rows) == 1
    haystack = rows[0]["message"] + rows[0]["traceback"]
    for secret in MUST_NOT_APPEAR:
        assert secret not in haystack, secret


def test_a_value_that_cannot_be_printed_does_not_take_the_line_down():
    class Hostile:
        def __str__(self):
            raise RuntimeError("no")

    assert redact.scrub(Hostile()) == "<unprintable>"


def test_a_long_line_is_truncated_after_scrubbing_not_before():
    """A secret must not survive by sitting past the cut."""
    padded = "x" * (redact.MAX_LEN + 100) + " sk_live_51H4xAbCdEfGh"
    cleaned = redact.scrub(padded)
    assert "sk_live_51H4xAbCdEfGh" not in cleaned


def test_the_capture_handler_never_records_below_warning(dbmod):
    """INFO is where this codebase says ordinary things. Capturing it would
    make the table a log file, which is the thing it exists not to be."""
    handler = errorlog.CaptureHandler()
    assert handler.level == logging.WARNING

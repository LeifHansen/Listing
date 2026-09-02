"""One bug is one row with a count, however many times it happens.

This is the property the whole design rests on. A table with one row per
OCCURRENCE is unreadable by the third hour of an incident and unbounded by
traffic; a table with one row per DISTINCT failure is a work-list. The daily
triage job reads that work-list, so a fingerprint that drifts means the same
bug looks new every morning and the same pull request gets opened forever.

What must NOT change the fingerprint:

- the varying arguments at a call site. `log.warning("failed (%s): %s", a, b)`
  hands `logging` a template that is identical every time, which is exactly
  why redact.py redacts in a formatter rather than a filter — a filter would
  have had to overwrite `record.msg` to reach the secret in the args, and the
  template would have been destroyed to protect it.
- ids inside an f-string message, for the minority of sites that use one.
- the line number, so a refactor does not reopen every closed bug.
- the release sha, so a deploy does not.

What MUST: a different call site, or a different kind of failure at one.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from backend.services import errorlog


def _fp(**over) -> str:
    base = dict(logger="thryft", level="WARNING", module="listing_sync",
                func="reconcile", template="lookup failed (%s) [%s]: %s",
                exc_type="ValueError", kind="backend")
    base.update(over)
    return errorlog.fingerprint(**base)


def test_the_same_call_site_is_one_fingerprint():
    assert _fp() == _fp()


def test_a_different_call_site_is_a_different_fingerprint():
    assert _fp() != _fp(func="sweep")
    assert _fp() != _fp(module="ebay_provider")


def test_a_different_failure_at_one_site_is_a_different_fingerprint():
    assert _fp() != _fp(exc_type="KeyError")


def test_the_line_number_is_not_part_of_it():
    """A refactor that moves code must not reopen every bug in it.

    fingerprint() takes no line number at all — this asserts the signature,
    which is the thing a future change would be tempted to alter.
    """
    import inspect

    assert "lineno" not in inspect.signature(errorlog.fingerprint).parameters


def test_the_release_is_not_part_of_it():
    """Same argument as the line number, one deploy louder."""
    import inspect

    params = inspect.signature(errorlog.fingerprint).parameters
    assert "build" not in params and "release" not in params


def test_ids_inside_an_f_string_message_normalize_away():
    """The 80-odd call sites that interpolate before logging still collapse."""
    a = _fp(template="record 4 of 91 failed for sess-9f3a1b2c")
    b = _fp(template="record 7 of 91 failed for sess-0011aabb")
    assert a == b


def test_a_genuinely_different_sentence_does_not():
    assert (_fp(template="record 4 of 91 failed")
            != _fp(template="the store import gave up"))


def test_the_count_accumulates_and_the_first_evidence_is_kept(dbmod):
    """A later occurrence must not overwrite the first one's evidence.

    The first is the one that happened BEFORE anything else started failing
    in response to it, which is the one worth reading.
    """
    for i in range(5):
        dbmod.record_error_event(fingerprint="deadbeef", severity="high",
                                 message=f"attempt {i}",
                                 traceback="the original traceback")
    rows = dbmod.error_events_list()

    assert len(rows) == 1
    assert rows[0]["count"] == 5
    assert rows[0]["message"] == "attempt 0"
    assert rows[0]["traceback"] == "the original traceback"


def test_a_flush_of_many_occurrences_is_one_write(dbmod):
    """Coalescing: a broken loop is one upsert per flush, not one per event."""
    events = [{"fingerprint": "aa11", "severity": "high", "message": "boom"}
              for _ in range(200)]
    merged = errorlog.coalesce(events)

    assert len(merged) == 1
    assert merged[0]["occurrences"] == 200

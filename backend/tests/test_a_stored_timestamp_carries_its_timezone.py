"""An "aware datetime" that is only sometimes aware writes the wrong time.

Trap #2 on this branch was `sessions_valid_from` added to the migration list
as a bare `TIMESTAMP` against a model declaring `DateTime(timezone=True)`, and
the cost was spelled out then: Postgres reads a NAIVE datetime written into a
`timestamptz` column as being in the SESSION's timezone, so on a deployment
not running in UTC the stored value is off by the offset. SQLite ignores all
of this, which is why nothing here caught it.

This is the same shape one layer up, on the value rather than the column.
`listing_sync._started_at` says in its own first line that it returns an aware
datetime, and it is what an imported listing's `updated_at` becomes — a
`DateTime(timezone=True)` column. It parses eBay's `"...Z"` form, which is
aware, and does nothing at all if the string it is handed is not in that form.
Its two siblings that parse the same kind of value both check:
`duplicates._listed_at` ends `parsed if parsed.tzinfo else
parsed.replace(tzinfo=timezone.utc)`, and `recommender._age_days` does the
same before subtracting.

Nothing sends it a naive string today. The point is that nothing stops one:
the field is read off a listing dict that has round-tripped through storage
and through a client, and the function's contract is a comment rather than a
check. Three functions doing the same job, two of which are careful, is a
fragile place to leave the third — and "most recent first" quietly ordered by
the wrong instant is not a failure anyone would report.
"""
from __future__ import annotations

import datetime as _dt

from backend.services import listing_sync


def test_ebays_own_format_is_aware():
    """The everyday case, and the reason this looked fine."""
    got = listing_sync._started_at({"ebay_start_time": "2026-07-30T18:04:11.000Z"})
    assert got is not None
    assert got.tzinfo is not None
    assert got.utcoffset() == _dt.timedelta(0)


def test_an_offset_that_is_not_utc_is_kept_as_sent():
    got = listing_sync._started_at(
        {"ebay_start_time": "2026-07-30T18:04:11+02:00"})
    assert got.utcoffset() == _dt.timedelta(hours=2)


def test_a_timestamp_with_no_zone_is_read_as_utc_not_left_naive():
    """The gap. A naive value here is written into a timestamptz column, where
    Postgres reads it in the session's timezone -- the same silent offset as
    trap #2, arriving from the value side instead of the column side."""
    got = listing_sync._started_at({"ebay_start_time": "2026-07-30T18:04:11"})
    assert got is not None
    assert got.tzinfo is not None, (
        "returned a naive datetime from a function whose contract is 'as an "
        "aware datetime', on the value that becomes the row's updated_at")
    assert got.utcoffset() == _dt.timedelta(0)


def test_nothing_useful_is_invented_from_nothing():
    assert listing_sync._started_at({}) is None
    assert listing_sync._started_at({"ebay_start_time": ""}) is None
    assert listing_sync._started_at({"ebay_start_time": "not a date"}) is None


def test_the_three_parsers_agree_with_each_other():
    """They read the same kind of value for the same reason; a naive one has
    to mean the same thing in all three or the app has two clocks."""
    from backend.services import duplicates, recommender

    naive = "2026-07-30T18:04:11"
    assert listing_sync._started_at({"ebay_start_time": naive}).tzinfo is not None
    assert duplicates._listed_at(
        {"listing": {"ebay_start_time": naive}}).tzinfo is not None
    # _age_days returns an int rather than a datetime; a naive input raising
    # TypeError inside it is the failure this asserts is not happening.
    assert recommender._age_days(naive) is not None

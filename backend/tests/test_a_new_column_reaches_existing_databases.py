"""A column added to a model does not add itself to a live database.

`create_all` creates missing TABLES, which is why a wholly new table needs
nothing else. It does not add a missing COLUMN to a table that already exists.
That is what the guarded `ALTER TABLE` list in db.py is for, and it is
entirely by hand: nothing connects the two, and every test in this suite
starts from a fresh SQLite file where `create_all` produces the full schema.
So a column added to a model without a matching ALTER works perfectly
everywhere it is tested and is missing on every database that already exists —
which is production, and only production.

The existing parity test (test_session_revocation) checks the direction that
has a statement to read: every ALTER matches the type `create_all` would emit.
It cannot check the other direction, because a column with no ALTER leaves
nothing behind to notice. This does, by holding the schema as it stands and
failing when it grows.

That failure is not "you did it wrong" — it is a question with two right
answers. Either add the ALTER, or add the column here because the whole table
is new and `create_all` covers it. Both are one line; the point is that
somebody decides.

This branch already shipped one bug of exactly this family:
`sessions_valid_from` had its ALTER but declared a bare `TIMESTAMP` against a
model saying `DateTime(timezone=True)`, which SQLite ignores entirely. The
type half is pinned in test_session_revocation; the existence half is here.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("sqlalchemy")

# The schema as it stands. Not a description of what SHOULD exist — a record
# of what does, so that growth is visible.
SHIPPED = {
    "admin_audit_log": (
        "action", "actor_email", "actor_id", "created_at", "data", "id",
        "ip", "target_id", "target_type",
    ),
    "ebay_accounts": (
        "ebay_email", "ebay_user_id", "ebay_username",
        "fulfillment_policy_id", "merchant_location_key",
        "payment_policy_id", "refresh_token", "return_policy_id",
        "ship_from_postal", "updated_at", "user_id",
    ),
    "ebay_deletion_notices": (
        "attempts", "completed_at", "ebay_user_id", "last_error",
        "notification_id", "payload_digest", "received_at", "state",
    ),
    "listings": (
        "created_at", "data", "id", "status", "title", "updated_at",
        "user_id",
    ),
    "marketplace_accounts": (
        "external_id", "external_username", "marketplace", "refresh_token",
        "settings", "updated_at", "user_id",
    ),
    "media_purges": (
        "attempts", "last_error", "listing_id", "requested_at", "user_id",
    ),
    "notifications": (
        "body", "created_at", "data", "dedupe_key", "id", "kind",
        "listing_id", "read_at", "title", "user_id",
    ),
    "token_accounts": (
        "free_period", "free_used", "purchased", "updated_at", "user_id",
    ),
    "token_ledger": (
        "created_at", "feature", "free_part", "id", "kind", "note",
        "paid_part", "period", "ref", "tokens", "user_id",
    ),
    "users": (
        "created_at", "display_name", "email", "id", "last_sweep_at",
        "password_hash", "prefs", "sessions_valid_from",
    ),
}


def _migrated_columns() -> set[tuple[str, str]]:
    """(table, column) pairs the guarded ALTER list adds."""
    from backend import db

    found = set()
    for stmt in db._MIGRATIONS:
        m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)\b", stmt)
        if m:
            found.add((m.group(1), m.group(2)))
    return found


def _model_columns() -> dict[str, set[str]]:
    from backend import db

    return {t.name: {c.name for c in t.c}
            for t in db.Base.metadata.tables.values()}


def test_every_new_column_on_an_existing_table_has_a_migration():
    models, migrated = _model_columns(), _migrated_columns()
    missing = []
    for table, columns in models.items():
        if table not in SHIPPED:
            continue        # a whole new table — create_all covers it
        for column in sorted(columns - set(SHIPPED[table])):
            if (table, column) not in migrated:
                missing.append(f"{table}.{column}")
    assert not missing, (
        "These are on the model and would exist on a FRESH database, but "
        "nothing adds them to one that already exists:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd an `ALTER TABLE ... ADD COLUMN` to db._MIGRATIONS (matching "
          "the type create_all emits — see test_session_revocation), or add "
          "the column to SHIPPED here if the whole table is new.")


def test_a_dropped_column_does_not_go_unnoticed():
    """The other direction. A column removed from a model while an ALTER still
    adds it, or while code still reads it, is the same trap facing backwards —
    and a stale entry here would silently excuse a real omission later."""
    models = _model_columns()
    stale = []
    for table, columns in SHIPPED.items():
        if table not in models:
            stale.append(f"{table} (whole table)")
            continue
        for column in sorted(set(columns) - models[table]):
            stale.append(f"{table}.{column}")
    assert not stale, (
        "listed here but no longer on the models:\n  " + "\n  ".join(stale))


def test_the_record_covers_every_table_the_models_declare():
    """A new table is fine on a live database, but leaving it out of SHIPPED
    means the FIRST test above stops watching it — every column added to it
    afterwards would then be invisible."""
    models = _model_columns()
    unrecorded = sorted(set(models) - set(SHIPPED))
    assert not unrecorded, (
        "new table(s) — create_all will create them, but add them here so "
        "their future columns are still checked:\n  "
        + "\n  ".join(unrecorded))

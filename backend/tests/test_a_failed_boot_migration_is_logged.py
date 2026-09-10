"""A boot-time ALTER that fails for a real reason says so.

db._MIGRATIONS is re-applied on every boot, so on any database past its
first, every ADD COLUMN in it answers "duplicate column" -- expected, and
swallowed. The swallow was `except Exception: pass`, which also swallowed a
lock that timed out, a permission the role lost and a type the server
refused: the column was then missing, and the first thing to notice was a
query somewhere else failing on it, with nothing in the log from the boot
that lost it. Now the expected answers are told apart from the rest, and
the rest is logged with the statement.
"""
from __future__ import annotations

import logging

import pytest


@pytest.mark.parametrize("stmt,message,applied", [
    ("ALTER TABLE users ADD COLUMN role VARCHAR(16)",
     "duplicate column name: role", True),                          # SQLite
    ("ALTER TABLE users ADD COLUMN role VARCHAR(16)",
     'column "role" of relation "users" already exists', True),      # Postgres
    ("ALTER TABLE ebay_accounts ALTER COLUMN refresh_token TYPE VARCHAR(4096)",
     'near "ALTER": syntax error', True),          # SQLite has no ALTER COLUMN
    ("ALTER TABLE users ADD COLUMN role VARCHAR(16)",
     "database is locked", False),
    ("ALTER TABLE users ADD COLUMN role VARCHAR(16)",
     "permission denied for table users", False),
    ("ALTER TABLE users ADD COLUMN role VARCHAR(16)",
     'near "ALTER": syntax error', False),   # a syntax error on an ADD COLUMN is real
])
def test_the_expected_answers_are_told_apart_from_the_rest(stmt, message, applied):
    from backend import db
    assert db._migration_already_applied(stmt, RuntimeError(message)) is applied


def _reboot(db):
    db._engine = None
    db._initialized = False
    return db._get_engine()


def test_a_real_failure_is_logged_with_its_statement(dbmod, monkeypatch, caplog):
    bad = "ALTER TABLE no_such_table ADD COLUMN x INTEGER"
    monkeypatch.setattr(dbmod, "_MIGRATIONS", (bad, *dbmod._MIGRATIONS))
    with caplog.at_level(logging.WARNING, logger="thryft"):
        assert _reboot(dbmod) is not None, "boot must still complete"
    failed = [r.getMessage() for r in caplog.records if "migration failed" in r.getMessage()]
    assert len(failed) == 1 and "no_such_table" in failed[0], failed


def test_a_second_boot_is_silent(dbmod, caplog):
    """Every ADD COLUMN in the list is a duplicate on the second boot, and
    none of them is worth a line -- sixteen warnings a boot is how a real one
    gets ignored."""
    _reboot(dbmod)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="thryft"):
        _reboot(dbmod)
    assert not [r for r in caplog.records if "migration failed" in r.getMessage()]

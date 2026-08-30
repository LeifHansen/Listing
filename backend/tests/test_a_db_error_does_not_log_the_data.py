"""A database error must not carry the values that caused it into the log.

`db.py` has 44 sites shaped like:

    except Exception as exc:
        log.warning(f"db: create_user failed: {exc}")

which is the right instinct — a swallowed failure with no trace is worse. But
SQLAlchemy's exception string is not just the error. By default it appends the
statement AND the bound parameters:

    (sqlite3.IntegrityError) UNIQUE constraint failed: users.id
    [SQL: INSERT INTO users (id, email, password_hash, ...) VALUES (?, ?, ?, ...)]
    [parameters: ('abc', 'seller@example.com', '$2b$12$...')]

So one Postgres hiccup during signup writes a seller's email address and the
bcrypt hash of their password into the application log — and on Fly that goes
to the platform log stream and any drain attached to it. The same shape covers
every other write: the encrypted marketplace token blob through
`save_marketplace_account`, the whole listing document through
`upsert_listing`, an eBay user id through the deletion inbox.

`hide_parameters=True` on the engine is the fix, and it belongs on the engine
rather than at 44 call sites: it is one decision that cannot be forgotten by
the next `except` block someone writes. The statement text stays — it is our
own SQL and names columns, not values — so the log still says what failed.
"""
from __future__ import annotations

import importlib

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("bcrypt")

from backend import config, db  # noqa: E402

EMAIL = "seller@example.com"
# Shaped like a real bcrypt hash; the point is that it is the password
# verifier, so logging it is logging the credential.
PW_HASH = "$2b$12$abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQR"


@pytest.fixture
def sqlite_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/leak.db")
    importlib.reload(config)
    db._engine = None
    db._initialized = False
    yield db
    db._engine = None
    db._initialized = False
    importlib.reload(config)


def test_a_failed_signup_does_not_log_the_email_or_the_hash(sqlite_db, caplog):
    """The realistic path: two accounts collide on the primary key.

    create_user's own duplicate-EMAIL check passes (the addresses differ), the
    INSERT then violates the id constraint, and the except block logs the
    exception — with the new account's email and password hash bound to it.
    """
    assert sqlite_db.create_user("dup-id", "first@example.com", "$2b$12$aaa")

    caplog.clear()
    with caplog.at_level("WARNING"):
        assert sqlite_db.create_user("dup-id", EMAIL, PW_HASH) is None

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "create_user failed" in logged, "the failure must still be reported"
    assert EMAIL not in logged, f"the seller's email reached the log:\n{logged}"
    assert PW_HASH not in logged, f"the password hash reached the log:\n{logged}"


def test_the_statement_still_says_what_failed(sqlite_db, caplog):
    """Hiding values must not turn the log into an unreadable stub — an
    operator still needs to know which write broke and why."""
    assert sqlite_db.create_user("dup-id", "first@example.com", "$2b$12$aaa")
    caplog.clear()
    with caplog.at_level("WARNING"):
        sqlite_db.create_user("dup-id", EMAIL, PW_HASH)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "IntegrityError" in logged
    assert "INSERT INTO users" in logged


def test_the_engine_hides_parameters(sqlite_db):
    """Pinned on the engine, not on the call sites: the next `except` block
    someone writes in db.py inherits this without having to know about it."""
    engine = sqlite_db._get_engine()
    assert engine is not None
    assert engine.hide_parameters is True

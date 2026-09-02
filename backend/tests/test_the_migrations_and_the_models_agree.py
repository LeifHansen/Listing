"""The migrations have to build the schema the models describe.

P1-11 asks for a real migration framework before beta, and the reason is
written into this repo's own history: `sessions_valid_from` was added to
`db._MIGRATIONS` as a bare `TIMESTAMP` against a model declaring
`DateTime(timezone=True)`, and nothing could see it because SQLite ignores
column types. The guarded ALTER list is a migration framework the way a
shell script is a build system — it works until the second person uses it.

So there is an alembic revision set now, generated from the models and
checked against them here. The check is the whole point: a migration set that
nobody compares to the code is a second description of the schema that will
quietly stop being true.

**The boot path is deliberately unchanged.** The app still calls
`create_all` plus the guarded ALTERs, because swapping a live Neon database
onto `alembic upgrade head` is a deploy step with a one-time
`alembic stamp head` in front of it, and it cannot be proved from here. What
CAN be proved from here is that the two descriptions agree — which is exactly
what makes that cutover safe to do later, and what this test is for.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("alembic")

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from backend import db

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.cmd_opts = type("O", (), {"x": [f"url={url}"]})()
    return cfg


@pytest.fixture()
def migrated(tmp_path):
    """A database built by running every migration, from nothing."""
    url = f"sqlite:///{tmp_path/'migrated.db'}"
    command.upgrade(_config(url), "head")
    return create_engine(url)


@pytest.fixture()
def created(tmp_path):
    """The same schema built the way the app boots today."""
    url = f"sqlite:///{tmp_path/'created.db'}"
    engine = create_engine(url)
    db.Base.metadata.create_all(engine)
    return engine


def test_the_migrations_leave_nothing_for_autogenerate_to_add(migrated):
    """The check that matters: after `upgrade head`, the models and the
    database describe the same thing.

    A diff here means a migration was written for a model that has since
    changed, or a model changed with no migration — the second being the one
    that breaks a deploy rather than a dev machine, because `create_all` on a
    fresh database papers over it every time.
    """
    with migrated.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, db.Base.metadata)
    assert diff == [], (
        "the migrations and the models disagree:\n  "
        + "\n  ".join(repr(d) for d in diff)
        + "\n\nRun `alembic revision --autogenerate -m '...'` and commit "
          "what it writes.")


def test_both_paths_build_the_same_tables(migrated, created):
    # alembic_version is alembic's own bookkeeping and exists only on the
    # migrated side, which is the point of it.
    built = sorted(t for t in inspect(migrated).get_table_names()
                   if t != "alembic_version")
    assert built == sorted(inspect(created).get_table_names())


def test_both_paths_build_the_same_columns(migrated, created):
    a, b = inspect(migrated), inspect(created)
    for table in sorted(inspect(created).get_table_names()):
        cols_m = {c["name"] for c in a.get_columns(table)}
        cols_c = {c["name"] for c in b.get_columns(table)}
        assert cols_m == cols_c, (
            f"{table}: migrations build {sorted(cols_m)}, "
            f"create_all builds {sorted(cols_c)}")


def test_both_paths_build_the_same_indexes(migrated, created):
    """Including the composite one the unread badge polls on.

    That index used to live only in `db._MIGRATIONS`, so it was invisible to
    anything reading the models — a person, or autogenerate. It runs on every
    boot, so a fresh database did get it; what was missing was any way to
    know that from the code. It is declared on the model now, which is what
    lets this comparison be meaningful at all.
    """
    a, b = inspect(migrated), inspect(created)
    for table in sorted(inspect(created).get_table_names()):
        idx_m = {i["name"] for i in a.get_indexes(table)}
        idx_c = {i["name"] for i in b.get_indexes(table)}
        assert idx_m == idx_c, (
            f"{table}: migrations build {sorted(idx_m)}, "
            f"create_all builds {sorted(idx_c)}")
    assert "ix_notifications_user_unread" in {
        i["name"] for i in a.get_indexes("notifications")}


def test_every_column_the_alter_list_adds_is_in_the_migrations(migrated):
    """The old mechanism and the new one have to describe the same schema.

    `_MIGRATIONS` is what an EXISTING deployment gets; the revision set is
    what a new one gets. A column in one and not the other is two populations
    running the same code against different tables — which is the failure the
    whole of P1-11 is about.
    """
    import re

    columns: dict[str, set[str]] = {}
    for table in inspect(migrated).get_table_names():
        columns[table] = {c["name"] for c in inspect(migrated).get_columns(table)}

    checked = 0
    for stmt in db._MIGRATIONS:
        m = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", stmt)
        if not m:
            continue
        table, column = m.groups()
        assert table in columns, f"{stmt}: the migrations build no {table}"
        assert column in columns[table], (
            f"{stmt}: an existing deployment gets {table}.{column} and a "
            f"new one does not")
        checked += 1
    assert checked >= 6, "the ALTER list stopped being readable"


def test_there_is_exactly_one_head():
    """Two heads means somebody branched the history and `upgrade head`
    stops being a single, decidable thing."""
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(_config("sqlite://")).get_heads()
    assert len(heads) == 1, f"the revision history has {len(heads)} heads: {heads}"

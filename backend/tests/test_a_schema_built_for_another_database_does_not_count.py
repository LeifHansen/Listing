"""The schema check names the engine it checked.

db._get_engine() builds the tables once and then remembers that it has. It
used to remember that as a bare True, which said "built" without saying for
WHICH engine -- and the suite swaps engines: a fixture points the module at a
fresh SQLite file by clearing db._engine and the flag. A thread left running
by an earlier test could be midway through building the old engine's schema
at that moment. It finished after the reset, put the flag back to True, and
the next caller made an engine for the new file, read "built" and skipped
create_all, so every query on it failed with "no such table".

That is how test_tokens.py's reversal test failed in CI, with the other
thread's last boot migration logging "'NoneType' object has no attribute
'begin'" in its setup. This replays that interleaving with a real second
thread, and requires the new database to come out with its tables.
"""
from __future__ import annotations

import threading

from sqlalchemy import inspect


def test_a_reset_during_another_threads_boot_still_builds_the_new_schema(
        dbmod, monkeypatch, tmp_path):
    db = dbmod
    real_create_all = db.Base.metadata.create_all
    inside = threading.Event()
    go_on = threading.Event()
    got = []

    def booting_thread():
        got.append(db._get_engine())

    booting = threading.Thread(target=booting_thread, daemon=True)

    def slow_create_all(engine, *args, **kwargs):
        if threading.current_thread() is booting:
            inside.set()
            assert go_on.wait(10)
        return real_create_all(engine, *args, **kwargs)

    monkeypatch.setattr(db.Base.metadata, "create_all", slow_create_all)
    booting.start()
    assert inside.wait(10)      # the other thread is building the first file

    # What a fixture does to point the module at another database.
    monkeypatch.setattr(db.config, "DATABASE_URL",
                        f"sqlite:///{tmp_path / 'second.db'}")
    db._engine = None
    db._initialized = False

    go_on.set()
    booting.join(10)
    assert not booting.is_alive()
    try:
        engine = db._get_engine()
        assert str(engine.url).endswith("second.db")
        assert "token_ledger" in inspect(engine).get_table_names()
    finally:
        for other in got:
            if other is not None and other is not db._engine:
                other.dispose()

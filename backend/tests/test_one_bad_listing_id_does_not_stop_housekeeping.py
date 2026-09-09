"""One row with an id outside the accepted form must not switch off every
housekeeping pass.

storage.safe_session_name now REJECTS an id it used to rewrite, and the orphan
sweep asked it for every listing id in the database as the first statement of
reclaim_space. One legacy row that failed the rule raised out of that set
comprehension, and with it went the originals prune, the exports prune, the R2
offload, the pending deletions and the owed refunds — every cycle, for as long
as the row existed, with nothing but a line in the reclaim loop's log.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main, storage  # noqa: E402


def test_the_sweep_skips_the_bad_id_and_keeps_the_rest(monkeypatch):
    monkeypatch.setattr(main.db, "all_listing_ids",
                        lambda: ["good-id-1", "bad id!", "", "ebay-123"])
    asked: dict = {}

    def fake_sweep(dir_names, max_age_seconds=0):
        asked["names"] = set(dir_names)
        return []
    monkeypatch.setattr(storage, "sweep_orphan_sessions", fake_sweep)

    main._sweep_orphans()  # must not raise

    assert asked["names"] == {"good-id-1", "ebay-123"}

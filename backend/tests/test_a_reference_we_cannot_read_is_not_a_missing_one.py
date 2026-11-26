"""A reference link we cannot read is a 503, not a crash and not a 404.

`expert_knowledge_get` had no handler at all, so a Neon blip escaped as a raw
SQLAlchemy error: a 500 and an error_events row on the edit, delete and
refresh routes, where every other read in db.py answers "try again".

(`expert_knowledge_count` still answers 0 on a failure, deliberately: see its
entry in test_every_storage_failure_is_classified.WHY_A_BLANK_IS_SAFE.)
"""
from __future__ import annotations

import pytest

from backend import errors


@pytest.fixture()
def broken_reads(dbmod, monkeypatch):
    dbmod._get_engine()          # build the schema while the database works

    class _Down:
        def __init__(self, *a, **k):
            raise RuntimeError("server closed the connection unexpectedly")

    monkeypatch.setattr(dbmod, "Session", _Down)
    return dbmod


def test_a_failed_lookup_is_storage_unavailable_not_a_crash(broken_reads):
    with pytest.raises(errors.StorageUnavailable):
        broken_reads.expert_knowledge_get("some-id")


def test_both_still_answer_normally(dbmod):
    rid = dbmod.expert_knowledge_add("art", "https://example.com/r", "note",
                                     scope="account", account_id="u-1",
                                     added_by="u-1")
    assert dbmod.expert_knowledge_get(rid)["id"] == rid
    assert dbmod.expert_knowledge_get("no-such-id") is None
    assert dbmod.expert_knowledge_count("u-1", "art") == 1

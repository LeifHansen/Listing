"""Two swallows stacked up and dropped an erasure obligation.

The durable purge exists because the photos and the row that names them are
deleted at different times: `delete_user` records what is owed inside its own
transaction, and a later pass reads that record back and does the work. The
queue's contract is precise — and `db.finish_media_purge`'s docstring states
it — *"only ever called after a purge that raised nothing"*:

    try:
        purge_media(lid)
    except Exception as exc:
        db.note_media_purge_failure(lid, str(exc))   # keep the debt
        continue
    db.finish_media_purge(lid)                        # drop the debt

Nothing raised. `objstore.delete_prefix` catches every failure and returns
`0`, and `_purge_session_images` wraps the whole thing in `except Exception`
because it is ALSO the cleanup after a merge, a delete and a sale, where a
failed cleanup must not fail the request. So with R2 unreachable, the queue
saw a purge that "succeeded", dropped the debt, and the photos stayed in the
bucket **for ever** — with the account that owned them already gone, which is
the exact outcome the durable queue was built to prevent, reached from one
layer down.

`ebay_deletion.purge` depends on the same raise, and says so in its own
comment: "leaving the row alone is what hands the object to the next resume
pass." It was leaving nothing alone.

One function was serving two callers with opposite needs. It is two now: the
strict one raises and is what the erasure paths use; the best-effort one keeps
the old behaviour for cleanup after a merge, a delete or a sale, where the
seller's request must not fail over a photo that will be swept later anyway.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main, objstore
from backend.services import deletion_queue


class _BrokenBucket:
    """An object store that is reachable enough to try and not to finish."""

    def get_paginator(self, _name):
        raise RuntimeError("connection reset by peer")


@pytest.fixture()
def broken_r2(monkeypatch):
    monkeypatch.setattr(objstore, "_get_client", lambda: _BrokenBucket())
    monkeypatch.setattr(objstore, "enabled", lambda: True)
    monkeypatch.setattr(objstore.config, "R2_BUCKET", "test-bucket")


def test_the_strict_delete_says_when_it_could_not_delete(broken_r2):
    assert hasattr(objstore, "delete_prefix_strict"), (
        "there is no way to ask the bucket to delete and be told if it did "
        "not — which is what let a failed erasure be recorded as finished")
    with pytest.raises(objstore.ObjectStoreUnavailable):
        objstore.delete_prefix_strict("sessions/abc/")


def test_the_strict_delete_still_reports_what_it_removed(monkeypatch):
    """It has to be usable, not just loud."""
    class _Page(dict):
        pass

    class _Bucket:
        def get_paginator(self, _name):
            class P:
                def paginate(self, **_k):
                    return [{"Contents": [{"Key": "a"}, {"Key": "b"}]}]
            return P()

        def delete_objects(self, **_k):
            return {}

    monkeypatch.setattr(objstore, "_get_client", lambda: _Bucket())
    monkeypatch.setattr(objstore.config, "R2_BUCKET", "test-bucket")
    assert objstore.delete_prefix_strict("sessions/abc/") == 2


def test_the_best_effort_delete_still_never_raises(broken_r2):
    """The orphan sweep and the post-sale cleanup rely on this."""
    assert objstore.delete_prefix("sessions/abc/") == 0


def test_purging_a_deleted_account_s_photos_raises_when_it_cannot(broken_r2):
    with pytest.raises(Exception):
        main._purge_session_images("sess-1")


def test_cleanup_after_a_merge_or_a_sale_still_swallows(broken_r2):
    """The other half of the split. A photo that could not be tidied up must
    not fail the merge that succeeded, or the sold notification."""
    main._purge_session_images_best_effort("sess-1")   # must not raise


def test_the_queue_keeps_the_debt_when_the_purge_could_not_run(monkeypatch):
    """The behaviour all of the above exists for."""
    kept: list[str] = []
    finished: list[str] = []
    monkeypatch.setattr(deletion_queue.db, "pending_media_purges",
                        lambda **_k: [{"listing_id": "l1", "attempts": 0}])
    monkeypatch.setattr(deletion_queue.db, "pending_deletion_notices",
                        lambda **_k: [])
    monkeypatch.setattr(deletion_queue.db, "note_media_purge_failure",
                        lambda lid, err: kept.append(lid))
    monkeypatch.setattr(deletion_queue.db, "finish_media_purge",
                        lambda lid: finished.append(lid))

    def cannot(_lid):
        raise RuntimeError("R2 unreachable")

    out = deletion_queue.run_pending(purge_media=cannot)

    assert kept == ["l1"], "the debt was not kept"
    assert finished == [], "the debt was dropped on a purge that did not happen"
    assert out["media"] == 0, "reported finished work it did not do"


def test_the_queue_drops_the_debt_when_the_purge_really_ran(monkeypatch):
    finished: list[str] = []
    monkeypatch.setattr(deletion_queue.db, "pending_media_purges",
                        lambda **_k: [{"listing_id": "l1", "attempts": 0}])
    monkeypatch.setattr(deletion_queue.db, "pending_deletion_notices",
                        lambda **_k: [])
    monkeypatch.setattr(deletion_queue.db, "finish_media_purge",
                        lambda lid: finished.append(lid))

    out = deletion_queue.run_pending(purge_media=lambda _lid: None)

    assert finished == ["l1"]
    assert out["media"] == 1


def test_the_erasure_paths_use_the_strict_one():
    """Read off the source, because wiring the wrong one back is silent.

    Both of these hand `purge_media` to code that decides whether an erasure
    is finished. The cleanup paths are free to use the tolerant one; these
    two are not.
    """
    import inspect
    src = inspect.getsource(main)
    for call in ("deletion_queue.run_pending(purge_media=_purge_session_images)",
                 "ebay_deletion.purge(subject, purge_media=_purge_session_images)"):
        assert call in src, (
            f"{call!r} is not wired to the strict purge any more — an erasure "
            "that fails will be recorded as done")

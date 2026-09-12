"""The ten-minute latch re-opened the hole the strict delete was built to close.

`test_an_erasure_that_failed_is_not_finished.py` fixed the case where the
bucket answers and the operation throws. It could not see this one, because
this one never reaches an operation at all.

`objstore._get_client()` returns None for two opposite reasons:

  * R2 was never configured. There is genuinely nothing in object storage to
    erase, and 0 is the true answer.
  * R2 IS configured — it holds the seller's photos — but a single failed
    init has latched the module off for `_RETRY_AFTER` (ten minutes). Nothing
    was looked at. 0 is a lie.

`delete_prefix_strict` returned 0 for both, and `_purge_session_images` did
not even call it while latched, because it asked `objstore.enabled()` first —
and `enabled()` is false in exactly the case that must raise.

Either way the purge returned without raising, so `db.finish_media_purge`
dropped the debt (its own docstring: *"Only ever called after a purge that
raised nothing"*) and the photos stayed in the bucket for ever, with the
account that owned them already deleted.

The window is not hypothetical. `probe()` runs once on a background thread at
startup, and this app restarts often — the background remover is memory-hungry
— so one DNS blip at boot latches storage off for the next ten minutes. An
account deletion, an eBay account-deletion notice, or the housekeeping pass
retrying a pending purge inside that window hit this.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main, objstore


@pytest.fixture()
def latched(monkeypatch):
    """R2 configured, and latched off by a failure moments ago.

    Set through the module's own state rather than by patching `_latched`, so
    the test exercises the real path from `_fail` to `_get_client` returning
    None.
    """
    monkeypatch.setattr(objstore.config, "r2_configured", lambda: True)
    monkeypatch.setattr(objstore.config, "R2_BUCKET", "test-bucket")
    monkeypatch.setattr(objstore, "_client", None)
    monkeypatch.setattr(objstore, "_error", "R2 unreachable: [Errno -2] Name or service not known")
    monkeypatch.setattr(objstore, "_error_at", time.time())
    assert not objstore.enabled(), "the fixture has to actually be latched"
    return objstore


@pytest.fixture()
def never_configured(monkeypatch):
    monkeypatch.setattr(objstore.config, "r2_configured", lambda: False)
    monkeypatch.setattr(objstore, "_client", None)
    monkeypatch.setattr(objstore, "_error", None)
    monkeypatch.setattr(objstore, "_error_at", 0.0)
    return objstore


def test_a_latched_bucket_is_not_an_empty_one(latched):
    """"We could not look" and "there was nothing there" are different."""
    with pytest.raises(objstore.ObjectStoreUnavailable):
        objstore.delete_prefix_strict("sessions/abc/")


def test_no_bucket_configured_is_still_genuinely_nothing_to_erase(never_configured):
    """The other half. A deployment with no R2 must not fail every erasure."""
    assert objstore.delete_prefix_strict("sessions/abc/") == 0


def test_the_purge_raises_rather_than_skipping_a_latched_bucket(latched):
    """It used to ask `enabled()` first and quietly do nothing.

    Returning cleanly here is what drops the debt, so this must raise for the
    deletion queue to keep it.
    """
    with pytest.raises(objstore.ObjectStoreUnavailable):
        main._purge_session_images("sess-1")


def test_the_purge_still_erases_local_photos_with_no_bucket(never_configured, tmp_path,
                                                            monkeypatch):
    """A no-R2 deployment keeps working: the disk is the whole store there."""
    d = tmp_path / "sess-1"
    (d / "optimized").mkdir(parents=True)
    (d / "optimized" / "img_000.jpg").write_bytes(b"x")
    monkeypatch.setattr(main.storage, "session_dir", lambda _s: d)

    main._purge_session_images("sess-1")

    assert not d.exists(), "the local copies are the erasure when there is no bucket"


def test_cleanup_after_a_merge_or_a_sale_still_swallows_a_latch(latched):
    """The best-effort twin is unchanged: a failed tidy-up must not fail a
    merge that succeeded, or the sold notification behind it."""
    main._purge_session_images_best_effort("sess-1")   # must not raise


def test_an_expired_latch_lets_the_erasure_be_attempted_again(monkeypatch):
    """The latch is a backoff, not a verdict. Once it lapses the purge tries."""
    monkeypatch.setattr(objstore.config, "r2_configured", lambda: True)
    monkeypatch.setattr(objstore.config, "R2_BUCKET", "test-bucket")
    monkeypatch.setattr(objstore, "_client", None)
    monkeypatch.setattr(objstore, "_error", "R2 unreachable: yesterday")
    monkeypatch.setattr(objstore, "_error_at", time.time() - objstore._RETRY_AFTER - 1)

    tried: list[str] = []

    class _Bucket:
        def get_paginator(self, _name):
            class P:
                def paginate(self, **kw):
                    tried.append(kw.get("Prefix") or "")
                    return [{"Contents": []}]
            return P()

    monkeypatch.setattr(objstore, "_get_client", lambda: _Bucket())

    assert objstore.delete_prefix_strict("sessions/abc/") == 0
    assert tried == ["sessions/abc/"], "an expired latch must not still refuse"

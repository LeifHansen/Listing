"""Object keys and on-disk session dirs must share one naming rule — split
keys are how imported listings' photos became invisible to the offload sweep."""
from __future__ import annotations

import pytest

from backend import objstore, storage


@pytest.mark.parametrize("session_id", [
    "3aaeb40637a1",          # a normal uploaded session
    "ebay-168433981627",     # an imported eBay listing
    "THRYFT-abc123",
])
def test_key_matches_session_dir_name(fresh_config, session_id):
    fresh_config()
    key = objstore.key_for(session_id, "img_000.jpg")
    assert key == f"sessions/{storage.session_dir(session_id).name}/optimized/img_000.jpg"


def test_imported_ids_keep_their_own_key(fresh_config):
    """This used to assert the key was "sessions/ebay123/..." — the hyphen
    deleted. Stripping collapsed distinct ids onto one prefix, which is both
    how two sellers' photos could share a directory and how the ownership
    guard was bypassed (test_session_id_aliasing.py). The id is now the key."""
    fresh_config()
    assert objstore.key_for("ebay-123", "img_001.jpg") \
        == "sessions/ebay-123/optimized/img_001.jpg"
    assert objstore.key_for("ebay-123", "img_001.jpg") \
        != objstore.key_for("ebay123", "img_001.jpg")


def test_unusable_id_raises(fresh_config):
    fresh_config()
    with pytest.raises(ValueError):
        objstore.key_for("../..", "img_000.jpg")


def test_url_for_public_mode(fresh_config):
    fresh_config(R2_ACCOUNT_ID="a", R2_ACCESS_KEY_ID="k",
                 R2_SECRET_ACCESS_KEY="s",
                 R2_PUBLIC_BASE_URL="https://img.example.com")
    assert objstore.url_for("sessions/x/optimized/img_000.jpg") \
        == "https://img.example.com/sessions/x/optimized/img_000.jpg"


def test_url_for_disabled_is_none(fresh_config):
    fresh_config()
    assert objstore.url_for("sessions/x/optimized/img_000.jpg") is None


def test_init_failure_latch_expires(fresh_config):
    """A failed init disables storage with a reason — but only for a while.
    A permanent latch once turned a boot-time DNS blip into 'R2 silently off
    until the next deploy'."""
    fresh_config(R2_ACCOUNT_ID="a", R2_ACCESS_KEY_ID="k",
                 R2_SECRET_ACCESS_KEY="s")
    assert objstore.enabled()
    objstore._fail("R2 unreachable: test blip")
    assert not objstore.enabled()
    assert "test blip" in objstore.last_error()
    # Age the failure past the retry window: storage offers itself again.
    objstore._error_at -= objstore._RETRY_AFTER + 1
    assert objstore.enabled()
    assert objstore.last_error() is None


def test_session_prefix_covers_every_key_for_that_session(fresh_config):
    """delete_prefix's prefix must actually contain the keys upload writes —
    the whole point of purging by prefix is that it does not depend on what
    is still on local disk."""
    fresh_config()
    for session_id in ("3aaeb40637a1", "ebay-168433981627"):
        prefix = objstore.session_prefix(session_id)
        for name in ("img_000.jpg", "img_017.jpg"):
            assert objstore.key_for(session_id, name).startswith(prefix)


def test_delete_prefix_removes_every_page(fresh_config, monkeypatch):
    """Deleting a session's photos must not depend on the local directory:
    the reclaim pass uploads to R2 and unlinks the local copy, so for an older
    listing the local dir is empty while the bucket still holds every photo.
    Paginated results must all be deleted, not just the first page."""
    fresh_config(R2_ACCOUNT_ID="a", R2_ACCESS_KEY_ID="k", R2_SECRET_ACCESS_KEY="s")
    deleted: list[str] = []

    class _Paginator:
        def paginate(self, Bucket, Prefix):  # noqa: N803 - boto3's kwarg names
            assert Prefix == "sessions/abc123/"
            yield {"Contents": [{"Key": f"{Prefix}optimized/img_{i:03d}.jpg"}
                                for i in range(3)]}
            yield {"Contents": [{"Key": f"{Prefix}optimized/img_003.jpg"}]}
            yield {}  # a page with nothing in it must not break the walk

    class _Client:
        def get_paginator(self, _name):
            return _Paginator()

        def delete_objects(self, Bucket, Delete):  # noqa: N803
            deleted.extend(o["Key"] for o in Delete["Objects"])

    monkeypatch.setattr(objstore, "_get_client", lambda: _Client())
    assert objstore.delete_prefix("sessions/abc123/") == 4
    assert len(deleted) == 4


def test_delete_prefix_is_best_effort(fresh_config, monkeypatch):
    """Like the rest of this module, a bucket problem must never raise into
    a request — deletion of the DB row has usually already happened."""
    fresh_config(R2_ACCOUNT_ID="a", R2_ACCESS_KEY_ID="k", R2_SECRET_ACCESS_KEY="s")

    class _Boom:
        def get_paginator(self, _name):
            raise RuntimeError("R2 down")

    monkeypatch.setattr(objstore, "_get_client", lambda: _Boom())
    assert objstore.delete_prefix("sessions/abc123/") == 0


def test_delete_prefix_noop_when_disabled(fresh_config):
    fresh_config()  # no R2 credentials
    assert objstore.delete_prefix("sessions/abc123/") == 0

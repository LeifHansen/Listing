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


def test_imported_ids_are_sanitized(fresh_config):
    fresh_config()
    assert objstore.key_for("ebay-123", "img_001.jpg") \
        == "sessions/ebay123/optimized/img_001.jpg"


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

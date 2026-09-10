"""Two savings that only matter because items are drafted several at a time.

The first is per photo. A listing goes through four to ten vision calls, and
every one of them read the same eight files off the volume and base64'd them
again — work that is pure CPU on a box with two shared cores, now contended by
three drafting workers.

The second is per batch. The identify prompt's static half is prompt-cached,
and a cache entry only becomes readable once the request that wrote it starts
answering. Drafted one at a time that is invisible, because the second item
reads what the first wrote. Started together, three items all MISS and all
three pay to write the same several thousand tokens. One cheap prefill first
turns those writes into reads.
"""
from __future__ import annotations

import base64
import types

import pytest

pytest.importorskip("PIL")
pytest.importorskip("anthropic")

from PIL import Image  # noqa: E402

from backend import config  # noqa: E402
from backend.services import claude_ai  # noqa: E402


@pytest.fixture(autouse=True)
def _empty_cache():
    claude_ai._B64_CACHE.clear()
    yield
    claude_ai._B64_CACHE.clear()


def _photo(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 40), (200, 120, 60)).save(path, "JPEG")
    return path


# --- reading the same photo once --------------------------------------------

def test_a_photo_is_read_and_encoded_once_across_a_listings_calls(tmp_path,
                                                                  monkeypatch):
    photo = _photo(tmp_path / "img_000.jpg")
    reads = []
    real_read = type(photo).read_bytes

    def counting_read(self):
        reads.append(str(self))
        return real_read(self)

    monkeypatch.setattr(type(photo), "read_bytes", counting_read)

    first = claude_ai._image_b64(photo)
    rest = [claude_ai._image_b64(photo) for _ in range(5)]

    assert all(r == first for r in rest)
    assert first == base64.standard_b64encode(real_read(photo)).decode("ascii")
    assert len(reads) == 1, "six calls, one read — the other five were the same"


def test_a_photo_the_seller_edited_is_read_again(tmp_path):
    """The studio writes a new file over the same path. Sending the version
    the seller just cropped away would be worse than any saving."""
    photo = _photo(tmp_path / "img_000.jpg")
    before = claude_ai._image_b64(photo)

    Image.new("RGB", (40, 40), (10, 10, 10)).save(photo, "JPEG")
    import os
    st = os.stat(photo)
    os.utime(photo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    assert claude_ai._image_b64(photo) != before


def test_the_cache_cannot_grow_without_bound(tmp_path):
    """An entry is a whole photo as base64. A 250-photo batch would hold a
    quarter of a gigabyte of them if this were unbounded."""
    for i in range(claude_ai._B64_CACHE_MAX + 10):
        claude_ai._image_b64(_photo(tmp_path / f"img_{i:03d}.jpg"))
    assert len(claude_ai._B64_CACHE) == claude_ai._B64_CACHE_MAX


def test_a_photo_that_vanished_still_raises_rather_than_serving_nothing(tmp_path):
    with pytest.raises(OSError):
        claude_ai._image_b64(tmp_path / "never_existed.jpg")


# --- warming the prompt cache ------------------------------------------------

class _FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = types.SimpleNamespace(create=self._create)

    def with_options(self, **kw):
        return self

    def _create(self, **kw):
        self.calls.append(kw)
        return types.SimpleNamespace(content=[], stop_reason="max_tokens",
                                     usage=None)


def test_the_warm_up_writes_the_same_prefix_the_real_calls_read(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(claude_ai, "_client", lambda: fake)

    assert claude_ai.warm_identify_cache() is True

    call = fake.calls[0]
    assert call["max_tokens"] == 0, (
        "prefill only — it fills the cache and bills no output tokens")
    assert call["system"] == [{"type": "text", "text": claude_ai._IDENTIFY_SYSTEM,
                               "cache_control": {"type": "ephemeral"}}], (
        "byte-identical to what identify() sends, or it caches a prefix "
        "nothing will read")
    # The breakpoint goes on the shared system block, never on the throwaway
    # user message — that would key the cache to the placeholder.
    assert "cache_control" not in str(call["messages"])
    assert call["messages"][0]["role"] == "user"


def test_a_warm_up_that_fails_is_not_a_failure(monkeypatch):
    """A cold cache costs money, not correctness. A batch must never die
    because the prefill did."""
    def _boom():
        raise RuntimeError("overloaded")

    monkeypatch.setattr(config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(claude_ai, "_client", _boom)

    assert claude_ai.warm_identify_cache() is False


def test_the_warm_up_does_not_run_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "anthropic_ready", lambda: False)
    monkeypatch.setattr(claude_ai, "_client",
                        lambda: pytest.fail("asked the API with no key"))

    assert claude_ai.warm_identify_cache() is False

"""A storage failure must not be reported as somebody else's fault.

Making the session and account reads raise (rather than answer "not signed
in" / "not connected") sends a typed StorageUnavailable up through routes that
already had broad `except Exception` handlers of their own. Two of them
relabel it, and a relabelled failure is worse than a bare one because it tells
the seller where to look — at the wrong thing:

  * `POST /api/price-suggestions` answered `502 eBay price lookup failed:
    <the storage message>`. eBay was fine. It also puts an internal sentence
    inside a sentence about eBay, which is the shape P2-07 exists to stop.
  * `POST /api/identify` and its background twin answered with an AI error
    (`claude_ai.ai_error_message`), which sends the seller to retry the model
    or check their photos.

Both keep everything else about their handlers — the identify paths still
refund the tokens first, because the seller must not pay for a call that
could not happen. What changes is that the failure keeps its own name and
reaches the central 503, which already says the right thing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend.errors import StorageUnavailable  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    from backend import main
    return main, TestClient(main.app, raise_server_exceptions=False)


def test_a_price_lookup_does_not_blame_ebay_for_a_storage_failure(
        client, monkeypatch):
    main, api = client
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "_taxonomy_guard", lambda *a, **k: None)

    def _boom(*a, **k):
        raise StorageUnavailable("We couldn't verify your session just now.")
    monkeypatch.setattr(main, "_pricing_strategy", _boom)

    res = api.post("/api/price-suggestions", json={"query": "levis 501"})
    assert res.status_code == 503, f"answered {res.status_code}: {res.text[:200]}"
    assert "ebay" not in res.text.lower(), (
        f"blamed eBay for a storage failure: {res.text[:200]}")


def test_a_real_ebay_failure_is_still_reported_as_one(client, monkeypatch):
    """The relabelling handler stays for what it was written for."""
    main, api = client
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "_taxonomy_guard", lambda *a, **k: None)
    monkeypatch.setattr(main, "_pricing_strategy", lambda *a, **k: "")
    monkeypatch.setattr(main.pricing, "suggest",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("eBay returned 500")))

    res = api.post("/api/price-suggestions", json={"query": "levis 501"})
    assert res.status_code == 502
    assert "ebay" in res.text.lower()


def test_identify_does_not_call_a_storage_failure_an_ai_failure(
        client, monkeypatch, tmp_path):
    main, api = client
    refunds: list = []
    monkeypatch.setattr(main.config, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(main, "_assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.storage, "optimized_dir", lambda sid: tmp_path)
    monkeypatch.setattr(main.storage, "list_optimized", lambda sid: ["a.jpg"])
    monkeypatch.setattr(main, "_charge_ai", lambda *a, **k: {"units": 1})
    monkeypatch.setattr(main.tokens, "refund", lambda spent: refunds.append(spent))

    def _boom(*a, **k):
        raise StorageUnavailable("We couldn't verify your session just now.")
    monkeypatch.setattr(main, "_pricing_strategy", _boom)

    res = api.post("/api/identify/s1")
    assert res.status_code == 503, f"answered {res.status_code}: {res.text[:200]}"
    assert refunds == [{"units": 1}], (
        "the seller must not pay for a call that could not happen")

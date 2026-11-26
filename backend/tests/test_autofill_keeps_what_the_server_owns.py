"""Auto-fill saves the editor's copy the way every other save does.

`/api/autofill-specifics` takes a full Listing from the client and wrote it
over the stored record wholesale. /api/save and /api/refine both put the
server-owned state back first (_restore_server_state); this route did not, so
one press -- or the editor opening, which runs it by itself -- wrote the
editor's stale copy of:

  * `ebay_listing_id`, `source`, `marketplaces`: blanked, so the next publish
    created a DUPLICATE live listing instead of revising the one on eBay;
  * `dirty_fields`: replaced by the editor's, so an edit the server had
    already marked to send was never sent.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.models import ItemSpecific  # noqa: E402

ASPECTS = [{"name": "Material", "required": False, "mode": "SELECTION_ONLY",
            "values": ["Ceramic"], "cardinality": "SINGLE",
            "data_type": "STRING", "format": "", "max_length": 0}]
STORED = {"title": "Vintage bowling trophy", "category_id": "11450",
          "images": ["1.jpg"], "price": 30.0,
          "ebay_listing_id": "123456789012", "source": "ebay",
          "marketplaces": {"ebay": {"status": "published",
                                    "listing_id": "123456789012"}},
          "dirty_fields": ["price"]}


@pytest.fixture
def saved(monkeypatch):
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.deps, "assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.deps, "uid", lambda *a, **k: "u1")
    monkeypatch.setattr(main.deps, "charge_ai", lambda *a, **k: None)
    monkeypatch.setattr(main.taxonomy, "item_aspects",
                        lambda *a, **k: {"aspects": ASPECTS})
    monkeypatch.setenv("SPECIFICS_COVERAGE", "0")
    opt = main.storage.optimized_dir("live1")
    opt.mkdir(parents=True, exist_ok=True)
    (opt / "1.jpg").write_bytes(b"never decoded here")
    rec = {"id": "live1", "user_id": "u1", "status": "live", "listing": STORED}
    monkeypatch.setattr(main.db, "get_listing", lambda *a, **k: rec)
    monkeypatch.setattr(main.db, "get_listing_best_effort", lambda *a, **k: rec)
    monkeypatch.setattr(main.claude_ai, "fill_aspects", lambda *a, **k: [
        ItemSpecific(name="Material", value="Ceramic", confidence="high")])
    out: dict = {}

    def upsert(sid, data, status="", **k):
        out.update(data)
        out["_status"] = status
        return True
    monkeypatch.setattr(main.db, "upsert_listing", upsert)
    return out


def test_autofill_keeps_the_live_listing_ids_and_the_pending_edit(saved):
    client = TestClient(main.app, raise_server_exceptions=False)
    # The editor's copy, loaded before the price edit and with none of the
    # server-owned fields on it.
    stale = {"title": "Vintage bowling trophy", "category_id": "11450",
             "images": ["1.jpg"], "price": 30.0, "dirty_fields": []}

    res = client.post("/api/autofill-specifics/live1",
                      json={"session_id": "live1", "listing": stale})

    assert res.status_code == 200, res.text[:300]
    assert saved["ebay_listing_id"] == "123456789012"
    assert saved["source"] == "ebay"
    assert saved["marketplaces"]["ebay"]["listing_id"] == "123456789012"
    # the server's pending edit survives, and the new specifics join it so
    # the next revise sends them to eBay
    assert "price" in saved["dirty_fields"]
    assert "item_specifics" in saved["dirty_fields"]
    assert saved["_status"] == "live"

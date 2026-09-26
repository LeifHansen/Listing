"""A fill that ran leaves a record of having run — including the one route
that used to do the work and say nothing about it.

`enriched_at` is the app's single answer to "has the AI read this listing's
photos against eBay's aspect list for its category". It is what ENDS the
asking: the dashboard's "Fill in details" group drops a listing that carries
it (services/recommender), and the bulk finisher skips one rather than
charging a second time for the same empty answer (main._finish_all_job).
Neither can be decided by counting blanks, because a listing whose photos
genuinely cannot answer its category has blank specifics before the fill and
blank specifics after it.

`/api/autofill-specifics` was the hole in that. It runs exactly the fill the
flag describes — eBay's aspect list for the category, read off this listing's
own photos, plus the coverage pass over what the first look left blank — and
left `enriched_at` empty. So a seller who filled a listing in the editor was
told by the dashboard, seconds later, that the AI had never looked at it, and
paid again to be told there was nothing to add.

The stamp is not conditional on the fill finding anything. A run that added
nothing is the run that matters: it is the listing whose photos cannot answer
its category, and the only one at risk of being asked forever.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.models import ItemSpecific  # noqa: E402
from backend.services import recommender  # noqa: E402

CATEGORY = "11450"
ASPECTS = [
    {"name": "Material", "required": False, "mode": "SELECTION_ONLY",
     "values": ["Ceramic", "Glass"], "cardinality": "SINGLE",
     "data_type": "STRING", "format": "", "max_length": 0},
    {"name": "Era", "required": False, "mode": "SELECTION_ONLY",
     "values": ["1970s"], "cardinality": "SINGLE",
     "data_type": "STRING", "format": "", "max_length": 0},
]


@pytest.fixture
def api(monkeypatch, tmp_path):
    """The autofill route with everything around it stood in for: eBay's
    taxonomy, the AI, the owner check and the token meter. What is real is the
    route's own bookkeeping, which is what these tests are about."""
    monkeypatch.setattr(main.config, "anthropic_ready", lambda: True)
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.deps, "assert_session_owner", lambda *a, **k: None)
    monkeypatch.setattr(main.deps, "uid", lambda *a, **k: "u1")
    monkeypatch.setattr(main, "_charge_ai", lambda *a, **k: None)
    monkeypatch.setattr(main.taxonomy, "item_aspects",
                        lambda *a, **k: {"aspects": ASPECTS})
    # The second look is its own feature with its own tests and its own kill
    # switch; off here so these tests read one fill, not two.
    monkeypatch.setenv("SPECIFICS_COVERAGE", "0")
    # One real photo on disk: the route refuses a listing whose images are
    # gone, and that refusal must not be what these tests are measuring.
    opt = main.storage.optimized_dir("s1")
    opt.mkdir(parents=True, exist_ok=True)
    (opt / "1.jpg").write_bytes(b"not really a jpeg, never decoded here")

    saved: dict = {}
    monkeypatch.setattr(main.db, "upsert_listing",
                        lambda sid, data, **k: saved.update(data))
    monkeypatch.setattr(main.db, "get_listing_best_effort", lambda *a, **k: None)
    client = TestClient(main.app, raise_server_exceptions=False)
    return client, saved


def _fill(api):
    """Run the route, and hand back the response body and the record the
    database was given."""
    client, saved = api
    res = client.post("/api/autofill-specifics/s1", json={
        "session_id": "s1",
        "listing": {"title": "Vintage bowling trophy", "category_id": CATEGORY,
                    "images": ["1.jpg"]}})
    assert res.status_code == 200, res.text[:300]
    return res.json(), saved


# ------------------------------------------------------------ the stamp lands

def test_the_editors_fill_records_that_it_ran(api, monkeypatch):
    monkeypatch.setattr(main.claude_ai, "fill_aspects",
                        lambda *a, **k: [ItemSpecific(name="Material", value="Ceramic",
                                                     confidence="high")])
    body, saved = _fill(api)
    assert body["added"] == 1
    assert saved["enriched_at"], "the stored record must carry the stamp"


def test_the_fill_that_found_nothing_records_that_it_ran(api, monkeypatch):
    """The run that matters. A listing whose photos cannot answer its
    category is the one that sat in "Fill in details" forever, being charged
    for on every press and moving the count not at all."""
    monkeypatch.setattr(main.claude_ai, "fill_aspects", lambda *a, **k: [])
    body, saved = _fill(api)
    assert body["added"] == 0
    assert saved["enriched_at"], "an empty answer is still an answer"


def test_the_editor_is_told_so_its_own_copy_does_not_go_stale(api, monkeypatch):
    """The editor holds the listing it is editing, and `collect()` sends that
    copy back on every save. Without the stamp in the reply its copy says the
    fill never ran, and the dashboard one screen over agrees."""
    monkeypatch.setattr(main.claude_ai, "fill_aspects",
                        lambda *a, **k: [ItemSpecific(name="Material", value="Ceramic",
                                                     confidence="high")])
    body, saved = _fill(api)
    assert body["enriched_at"] == saved["enriched_at"]


# --------------------------------------------- and the asking actually stops

def test_the_dashboard_stops_offering_the_fill_it_just_ran(api, monkeypatch):
    """End to end over the seam this exists for: the record the route saves,
    handed to the recommender that decides the "Fill in details" group. The
    specifics are still blank — that is the whole point — and the group no
    longer asks, because the AI has already looked."""
    monkeypatch.setattr(main.claude_ai, "fill_aspects", lambda *a, **k: [])
    _body, saved = _fill(api)
    item = {"id": "L1", "status": "published",
            "created_at": "2020-01-01T00:00:00+00:00", "listing": saved}

    types = [r["type"] for r in recommender.recommend_for(item, blank_specifics=14)]
    assert "specifics" not in types

    # ...and it is the stamp doing it, not something else about the record.
    item["listing"] = {**saved, "enriched_at": ""}
    types = [r["type"] for r in recommender.recommend_for(item, blank_specifics=14)]
    assert "specifics" in types

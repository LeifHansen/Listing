"""An anonymous caller must not be able to spend every seller's eBay quota.

`/api/category-suggestions`, `/api/item-aspects` and `/api/price-suggestions`
call eBay with the APPLICATION token — no seller login needed, which is why
they need none to reach. Their answers are cached, but the cache is bounded at
500 entries with a 24-hour TTL, so a flood of DISTINCT queries evicts it and
forces a live eBay call per request.

That quota is app-wide and shared: eBay's default allowance is 5,000 calls a
day across the whole application. Exhausting it does not degrade the attacker,
it degrades every seller at once — no category suggestions, no item specifics,
no price comps, on a listing they are trying to publish.

The photo studio already carries exactly this reasoning ("an unthrottled
caller stalls every seller's photo work at once") and exactly this brake. This
is the same argument with a third-party quota in place of the CPU.

The ceiling is generous on purpose: it has to sit far above a real drafting
session — an identify looks up categories, aspects and comps per item, and a
bulk batch does that for a pile — and far below what it takes to drain a day's
allowance.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient


@pytest.fixture()
def api(monkeypatch):
    from backend import config, main, ratelimit

    ratelimit.reset()
    monkeypatch.setattr(config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main.taxonomy, "suggest",
                        lambda *a, **k: {"suggestions": []})
    monkeypatch.setattr(main.taxonomy, "item_aspects", lambda *a, **k: {})
    monkeypatch.setattr(main.pricing, "suggest", lambda *a, **k: {"price": 1})
    yield TestClient(main.app)
    ratelimit.reset()


CALLS = [
    ("/api/category-suggestions", {"query": "lamp"}),
    ("/api/item-aspects", {"category_id": "112581"}),
    ("/api/price-suggestions", {"query": "lamp"}),
]


@pytest.mark.parametrize("path,body", CALLS)
def test_ordinary_use_is_not_blocked(api, path, body):
    """A drafting session makes several of these per listing; a bulk batch
    makes them for a pile. The ceiling must not be reachable by working."""
    for _ in range(40):
        assert api.post(path, json=body).status_code == 200


@pytest.mark.parametrize("path,body", CALLS)
def test_a_flood_is_stopped(api, path, body):
    """The finding: unbounded anonymous calls against a 5,000/day allowance
    shared by every seller."""
    from backend import ratelimit

    seen = {api.post(path, json=dict(body, query=f"q{i}", category_id=str(i)))
            .status_code
            for i in range(ratelimit.TAXONOMY_MAX_CALLS + 20)}

    assert 429 in seen, "an anonymous flood was never throttled"


def test_the_three_share_one_budget(api):
    """They draw on the same eBay allowance, so metering them separately would
    let a caller spend it three times over."""
    from backend import ratelimit

    for i in range(ratelimit.TAXONOMY_MAX_CALLS):
        api.post("/api/category-suggestions", json={"query": f"q{i}"})

    assert api.post("/api/item-aspects",
                    json={"category_id": "999"}).status_code == 429


def test_the_refusal_is_something_a_person_can_read(api):
    """A real seller can reach this by working fast, so the message has to
    tell them what to do — and say nothing about the shared allowance, which
    is an operator concern and a hint worth withholding from whoever is
    probing it."""
    from backend import ratelimit

    for i in range(ratelimit.TAXONOMY_MAX_CALLS + 1):
        resp = api.post("/api/category-suggestions", json={"query": f"q{i}"})

    assert resp.status_code == 429
    body = resp.text.lower()
    assert "wait" in body or "try again" in body, resp.text
    for leak in ("quota", "allowance", "ebay", "5,000", "application token"):
        assert leak not in body, leak


def test_the_ceiling_is_far_above_a_working_session_and_below_the_allowance():
    """Both halves matter: too low and a bulk batch trips it, too high and it
    is not a brake. eBay's default application allowance is 5,000 a day."""
    from backend import ratelimit

    assert 100 <= ratelimit.TAXONOMY_MAX_CALLS <= 1000

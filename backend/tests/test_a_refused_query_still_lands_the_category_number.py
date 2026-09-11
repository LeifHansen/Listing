"""A draft has to come out of identify with eBay's category NUMBER on it.

`category_suggestion` is a readable path the AI writes ("Clothing > Men >
Shirts"). `category_id` is the numeric leaf eBay actually files a listing
under, and the only one of the two that can publish. A draft carrying the
first and not the second looks completely filled in on every card and in the
editor's Category box -- and then fails at the Publish button on a field
nobody ever asked the seller to fill.

`main._category_queries` already exists to prevent exactly that: eBay matches
a query as a whole, and the first one (brand + an 80-character title + model
numbers + the AI's path) is the one most likely to come back with nothing, so
the ladder narrows -- the title alone, then the path, then its leaf.

The ladder had two holes, and both ended with an empty ID box:

  * eBay answers a query it will not search with a 4xx. `raise_for_status()`
    turned that into an exception, and `_resolve_category` treated ANY
    exception as "the Taxonomy API is down" and returned -- so a refusal of
    the widest query threw away the three narrower ones untried.

  * eBay answers "nothing matched" with an EMPTY BODY, and `resp.json()` on no
    content raises too. A perfectly ordinary answer arrived as an outage.

So: tell a refused query apart from an outage, keep going on the first, stand
down on the second.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

import httpx

from backend.models import Listing
from backend.services import taxonomy


@pytest.fixture(autouse=True)
def _taxonomy(monkeypatch):
    """A configured Taxonomy API with its token, its tree and its cache
    stubbed out, so each test is exactly one stubbed HTTP answer."""
    monkeypatch.setattr(taxonomy.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(taxonomy, "_app_token", lambda: "token")
    monkeypatch.setattr(taxonomy, "default_tree_id", lambda *a, **k: "0")
    taxonomy._SUGGEST_CACHE.clear()
    yield
    taxonomy._SUGGEST_CACHE.clear()


def _answers(*responses):
    """Stub taxonomy's httpx.get with one canned answer per call, and record
    the query each call carried."""
    seen: list[str] = []
    queue = list(responses)

    def _get(url, **kw):
        seen.append((kw.get("params") or {}).get("q", ""))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return _get, seen


def _resp(status: int, body=None) -> httpx.Response:
    request = httpx.Request("GET", "https://api.ebay.com/commerce/taxonomy")
    if body is None:
        return httpx.Response(status, request=request)
    return httpx.Response(status, json=body, request=request)


def _match(category_id: str, name: str) -> dict:
    return {"categorySuggestions": [{
        "category": {"categoryId": category_id, "categoryName": name},
        "categoryTreeNodeAncestors": [{"categoryName": "Clothing"}],
    }]}


# ------------------------------------------------ what eBay's answer means


def test_a_query_ebay_will_not_search_is_named_as_such(monkeypatch):
    get, _ = _answers(_resp(400))
    monkeypatch.setattr(taxonomy.httpx, "get", get)
    with pytest.raises(taxonomy.QueryRefused):
        taxonomy.suggest("Levi's 501 vintage selvedge 34x32 Clothing > Men")


def test_bad_keys_are_not_a_refused_query(monkeypatch):
    """A 401 is about the application, not the words. Calling it a refused
    query would send the caller down three more rungs to be told the same
    thing three more times."""
    get, _ = _answers(_resp(401))
    monkeypatch.setattr(taxonomy.httpx, "get", get)
    with pytest.raises(httpx.HTTPStatusError):
        taxonomy.suggest("a lamp")


def test_a_quota_refusal_is_not_a_refused_query(monkeypatch):
    get, _ = _answers(_resp(429))
    monkeypatch.setattr(taxonomy.httpx, "get", get)
    with pytest.raises(httpx.HTTPStatusError):
        taxonomy.suggest("a lamp")


def test_nothing_matched_is_an_answer_not_a_failure(monkeypatch):
    """eBay says "no match" with an empty body. `resp.json()` raises on that,
    which is how an ordinary answer reached the caller as an outage -- and an
    outage is what stopped the ladder."""
    get, _ = _answers(_resp(204))
    monkeypatch.setattr(taxonomy.httpx, "get", get)
    assert taxonomy.suggest("a thing no category fits") == {
        "query": "a thing no category fits", "tree_id": "0", "suggestions": []}


def test_a_match_still_carries_its_number(monkeypatch):
    """The ordinary path, so none of the above can quietly break it."""
    get, _ = _answers(_resp(200, _match("155226", "Shirts")))
    monkeypatch.setattr(taxonomy.httpx, "get", get)
    assert taxonomy.best_category_id("a shirt") == {
        "category_id": "155226", "category_name": "Shirts",
        "path": "Clothing > Shirts"}


# ------------------------------------------------------- and on a real draft


def _main():
    # `main` pulls the AI and image stacks; the fast CI job runs without them,
    # so these run in the smoke job (see .github/workflows/gates.yml).
    pytest.importorskip("anthropic")
    pytest.importorskip("PIL")
    from backend import main
    return main


def _draft() -> Listing:
    return Listing(
        title="Levi's 501 Original Fit Straight Leg Jeans Selvedge Redline 34x32",
        brand="Levi's", category_id="",
        category_suggestion="Clothing, Shoes & Accessories > Men > Jeans")


def test_a_refused_query_does_not_cost_the_draft_its_number(monkeypatch):
    """THE regression. eBay refuses the widest query; the narrower one below
    it matches, and the draft leaves with the number on it."""
    main = _main()
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "_fit_condition_to_category", lambda listing: None)

    asked: list[str] = []

    def _best(query, *a, **k):
        asked.append(query)
        if len(asked) == 1:
            raise taxonomy.QueryRefused("eBay would not search for that")
        return {"category_id": "11483", "path": "Men > Jeans"}

    monkeypatch.setattr(main.taxonomy, "best_category_id", _best)
    listing = _draft()

    main._resolve_category(listing)

    assert listing.category_id == "11483"
    assert listing.category_suggestion == "Men > Jeans"
    assert len(asked) == 2, "the refusal has to fall through to the next query"
    assert listing.missing_info == []


def test_every_rung_is_tried_before_the_draft_goes_out_without_one(monkeypatch):
    """All four refused is the real "we could not place this" — and only then
    does the draft carry the note that asks the seller to pick."""
    main = _main()
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)

    asked: list[str] = []

    def _best(query, *a, **k):
        asked.append(query)
        raise taxonomy.QueryRefused("eBay would not search for that")

    monkeypatch.setattr(main.taxonomy, "best_category_id", _best)
    listing = _draft()

    main._resolve_category(listing)

    assert asked == main._category_queries(listing)
    assert listing.category_id == ""
    assert any("ebay category" in note.lower() for note in listing.missing_info)


def test_an_outage_stops_the_ladder_on_the_first_rung(monkeypatch):
    """The other half of the same distinction. eBay being unreachable says
    nothing about the query, so the remaining rungs are three more failures
    and three more timeouts on a draft the seller is waiting for."""
    main = _main()
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)

    asked: list[str] = []

    def _best(query, *a, **k):
        asked.append(query)
        raise RuntimeError("eBay is down")

    monkeypatch.setattr(main.taxonomy, "best_category_id", _best)
    listing = _draft()

    main._resolve_category(listing)

    assert len(asked) == 1
    assert listing.category_id == ""
    assert any("ebay category" in note.lower() for note in listing.missing_info)


def test_the_picker_answers_a_refused_query_with_no_matches(monkeypatch):
    """The seller-facing half: the editor's "Suggest eBay categories" runs the
    same query. A refusal there is not "try again in a moment" — trying again
    sends the identical words to the identical refusal. It is the query that
    has to change, which is what the picker's own empty state says."""
    pytest.importorskip("fastapi")
    main = _main()
    from fastapi.testclient import TestClient

    from backend import ratelimit

    ratelimit.reset()
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)

    def _suggest(*a, **k):
        raise taxonomy.QueryRefused("eBay would not search for that")

    monkeypatch.setattr(main.taxonomy, "suggest", _suggest)
    try:
        res = TestClient(main.app).post("/api/category-suggestions",
                                        json={"query": "something eBay hates"})
        assert res.status_code == 200
        assert res.json()["suggestions"] == []
    finally:
        ratelimit.reset()


# ------------------------------- the second chance, on the researched title


def test_a_researched_title_gets_a_second_go_at_the_number(monkeypatch):
    """`_research_draft` replaces a hedged title with the real one AFTER the
    category was attempted — and a hedged title is exactly what eBay matches
    nothing for. The draft research just made searchable is the one that most
    needs asking again."""
    main = _main()
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "_fit_condition_to_category", lambda listing: None)

    def _best(query, *a, **k):
        # eBay places the researched name and nothing else.
        return ({"category_id": "15052", "path": "Portable Audio > Cassette"}
                if "Walkman WM-10" in query else {"category_id": ""})

    monkeypatch.setattr(main.taxonomy, "best_category_id", _best)
    listing = Listing(title="Vintage portable cassette player", category_id="")

    main._resolve_category(listing)
    assert listing.category_id == ""                    # nothing eBay could place
    assert any("ebay category" in n.lower() for n in listing.missing_info)

    listing.title = "Sony Walkman WM-10 Portable Cassette Player"   # research ran
    main._resolve_category_after_research(listing)

    assert listing.category_id == "15052"
    # ...and the draft stops asking for the field it now has.
    assert listing.missing_info == []


def test_the_second_go_never_moves_a_category_already_settled(monkeypatch):
    """It fills a blank and nothing else. A number the first pass resolved —
    or one the seller picked by hand — is not up for revision because a title
    changed."""
    main = _main()
    monkeypatch.setattr(main.config, "taxonomy_ready", lambda: True)
    monkeypatch.setattr(main, "_fit_condition_to_category", lambda listing: None)

    def _boom(*a, **k):
        raise AssertionError("a settled category must not be looked up again")

    monkeypatch.setattr(main.taxonomy, "best_category_id", _boom)
    listing = Listing(title="Sony Walkman WM-10", category_id="15052",
                      category_suggestion="Portable Audio > Cassette")

    main._resolve_category_after_research(listing)

    assert listing.category_id == "15052"
    assert listing.category_suggestion == "Portable Audio > Cassette"

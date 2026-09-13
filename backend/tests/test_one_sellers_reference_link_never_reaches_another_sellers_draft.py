"""Two sellers, two sets of references, and no path between them.

A reference link changes what the AI writes on a listing. A GLOBAL one is a
superadmin's and is meant to reach every draft in the app; an ACCOUNT one is
one seller's and must reach theirs and nobody else's — not because the content
is secret (though it may be: a seller's own pricing notes, a supplier's page),
but because it is INSTRUCTIONS, and one seller must not be able to change what
another seller's listings say.

The bug this file is written against is a specific and tempting one. A global
row carries account_id = "", so

    WHERE account_id = :uid OR account_id = ''

looks correct, passes a test written with one seller in it, and hands every
account-scoped reference in the database to any caller whose uid is empty. The
query is therefore spelled out on `scope` instead, and this file is what keeps
it that way.
"""
from __future__ import annotations

import importlib
import os
import tempfile

import pytest

pytest.importorskip("sqlalchemy")


@pytest.fixture()
def store(monkeypatch):
    """A real SQLite database, because the thing under test is a WHERE
    clause and a fake would be a second implementation of the bug."""
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("DATA_DIR", tmp)
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{os.path.join(tmp, 't.db')}")
    import backend.config as config
    importlib.reload(config)
    import backend.db as db
    importlib.reload(db)
    db._get_engine()
    yield db
    importlib.reload(config)


def _notes(rows):
    return sorted(r["note"] for r in rows)


def test_one_seller_never_reads_another_sellers_reference(store):
    store.expert_knowledge_add("art", "https://a.test/", "alice's own",
                               scope="account", account_id="alice",
                               added_by="alice")
    store.expert_knowledge_add("art", "https://b.test/", "bob's own",
                               scope="account", account_id="bob",
                               added_by="bob")
    assert _notes(store.expert_knowledge_for("art", "alice")) == ["alice's own"]
    assert _notes(store.expert_knowledge_for("art", "bob")) == ["bob's own"]


def test_a_global_reference_reaches_everybody(store):
    store.expert_knowledge_add("art", "https://g.test/", "everyone's",
                               scope="global", account_id="", added_by="admin")
    store.expert_knowledge_add("art", "https://a.test/", "alice's own",
                               scope="account", account_id="alice",
                               added_by="alice")
    assert _notes(store.expert_knowledge_for("art", "alice")) == \
        ["alice's own", "everyone's"]
    assert _notes(store.expert_knowledge_for("art", "bob")) == ["everyone's"]


def test_an_empty_account_id_reads_the_globals_and_nothing_else(store):
    """The bug this file is named for. A global row carries account_id = "",
    so a query joining on account_id would hand an anonymous caller every
    account-scoped reference in the database."""
    store.expert_knowledge_add("art", "https://g.test/", "everyone's",
                               scope="global", account_id="", added_by="admin")
    store.expert_knowledge_add("art", "https://a.test/", "alice's own",
                               scope="account", account_id="alice",
                               added_by="alice")
    assert _notes(store.expert_knowledge_for("art", "")) == ["everyone's"]
    assert _notes(store.expert_knowledge_for("art", None)) == ["everyone's"]


def test_the_query_does_not_join_on_an_empty_account_id():
    """Pinned on the source as well as on the behaviour, because the
    behavioural test above passes for a query that is one edit away from
    wrong, and that edit looks like a simplification."""
    import inspect

    import backend.db as db
    source = inspect.getsource(db.expert_knowledge_for)
    assert 'scope == "global"' in source
    assert 'account_id == ""' not in source


def test_an_account_scoped_reference_cannot_be_saved_without_an_account(store):
    """Otherwise it would be saved with account_id = "" and become, in
    effect, a global one that nobody approved."""
    with pytest.raises(ValueError):
        store.expert_knowledge_add("art", "https://a.test/", "orphan",
                                   scope="account", account_id="",
                                   added_by="")


def test_a_global_reference_is_stored_with_no_account(store):
    record_id = store.expert_knowledge_add(
        "art", "https://g.test/", "everyone's", scope="global",
        account_id="ignored", added_by="admin")
    assert store.expert_knowledge_get(record_id)["account_id"] == ""


def test_an_unknown_scope_is_treated_as_the_narrow_one(store):
    """Fail closed: anything that is not exactly "global" is an account
    reference, so a typo or a crafted value cannot widen a reference's
    reach."""
    record_id = store.expert_knowledge_add(
        "art", "https://x.test/", "n", scope="GLOBAL_but_not_quite",
        account_id="alice", added_by="alice")
    assert store.expert_knowledge_get(record_id)["scope"] == "account"


def test_references_are_filed_per_expert(store):
    store.expert_knowledge_add("art", "https://a.test/", "art one",
                               scope="global", account_id="", added_by="admin")
    store.expert_knowledge_add("denim", "https://d.test/", "denim one",
                               scope="global", account_id="", added_by="admin")
    assert _notes(store.expert_knowledge_for("art", "alice")) == ["art one"]
    assert _notes(store.expert_knowledge_for("denim", "alice")) == ["denim one"]


def test_a_disabled_reference_stops_reaching_drafts(store):
    """Which is what makes the toggle in the settings screen meaningful --
    and what a failed fetch uses to take a broken reference out of service."""
    record_id = store.expert_knowledge_add(
        "art", "https://a.test/", "alice's own", scope="account",
        account_id="alice", added_by="alice")
    assert store.expert_knowledge_for("art", "alice")
    store.expert_knowledge_update(record_id, enabled=False)
    assert store.expert_knowledge_for("art", "alice") == []
    # ...and it is still LISTED, or it could never be turned back on.
    assert _notes(store.expert_knowledge_list("alice")) == ["alice's own"]


def test_the_number_read_into_a_prompt_is_bounded(store):
    """One account cannot stuff the prompt for its own drafts either."""
    for i in range(40):
        store.expert_knowledge_add("art", f"https://a.test/{i}", f"n{i}",
                                   scope="account", account_id="alice",
                                   added_by="alice")
    assert len(store.expert_knowledge_for("art", "alice")) <= 20


# --- and the route that decides the scope -----------------------------------

def _source_of(name: str) -> str:
    """One function's real source out of main.py, by AST rather than by a
    character window -- a window is a number that silently stops covering the
    thing it was measured for the next time the function grows."""
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "main.py"
    text = path.read_text()
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{name} is not a top-level function in main.py")


def test_only_a_superadmin_can_create_a_global_reference():
    """The scope is derived server-side from the session, never taken from
    the request. Pinned on the source: exercising it needs a booted app and a
    signed-in superadmin, and the property is that the decision is made HERE
    rather than trusted from the body."""
    body = _source_of("add_expert_knowledge")
    assert 'user.get("role")' in body and '"superadmin"' in body
    assert 'scope = "global" if (wants_global and is_admin) else "account"' in body
    # ...and a global one is written to the admin audit trail like every other
    # superadmin action.
    assert "_audit_admin" in body


def test_a_seller_cannot_edit_or_delete_a_reference_that_is_not_theirs():
    """A global reference is read-only to a seller however they came by its
    id, and 404 rather than 403 -- possession of an id tells the holder
    nothing about whether it exists."""
    body = _source_of("_owned_knowledge")
    assert 'row.get("scope") != "account"' in body
    assert 'row.get("account_id") != uid' in body
    assert "404" in body
    # Every route that takes a record_id goes through it.
    for route in ("update_expert_knowledge", "delete_expert_knowledge",
                  "refresh_expert_knowledge"):
        assert "_owned_knowledge(record_id, uid)" in _source_of(route), route


def test_the_page_is_fetched_off_the_request_thread():
    """A slow or hostile host must not hold a request open -- which is also
    what stops this being a denial-of-service amplifier pointed at somebody
    else's server."""
    body = _source_of("add_expert_knowledge")
    assert "run_in_background(_distill_reference_row" in body
    assert "reference_fetch.fetch" not in body


def test_a_reference_that_cannot_be_read_says_so_instead_of_going_quiet():
    """A reference that silently stopped working is worse than none: the
    seller believes the AI is reading something it is not."""
    body = _source_of("_distill_reference_row")
    assert "fetch_error" in body
    assert "enabled=False" in body

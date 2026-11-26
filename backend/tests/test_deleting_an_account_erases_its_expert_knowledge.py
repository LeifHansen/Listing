"""Deleting an account erases the reference links that account saved.

An account-scoped expert reference is the seller's own words (the note) about
a page they chose (the URL), and the delete dialog promises "everything saved
to it" goes. delete_user cleared every other table keyed to the user and left
these behind, because the table arrived after the list was written.

A GLOBAL reference is a superadmin's contribution to every draft in the app,
not the deleted account's private data, so it stays.
"""
from __future__ import annotations

from backend import auth


def test_deleting_an_account_erases_its_own_references(dbmod):
    uid = dbmod.create_user("u-ek", "ek@example.com",
                            auth.hash_password("hunter2hunter2"))["id"]
    other = dbmod.create_user("u-other", "other@example.com",
                              auth.hash_password("hunter2hunter2"))["id"]
    dbmod.expert_knowledge_add("art", "https://example.com/mine", "my note",
                               scope="account", account_id=uid, added_by=uid)
    theirs = dbmod.expert_knowledge_add(
        "art", "https://example.com/theirs", "their note",
        scope="account", account_id=other, added_by=other)
    shared = dbmod.expert_knowledge_add(
        "art", "https://example.com/global", "for everyone",
        scope="global", account_id=uid, added_by=uid)

    assert dbmod.delete_user(uid) == []

    assert dbmod.expert_knowledge_list(uid, include_global=False) == []
    assert dbmod.expert_knowledge_get(theirs) is not None
    assert dbmod.expert_knowledge_get(shared) is not None

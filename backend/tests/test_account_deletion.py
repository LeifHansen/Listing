"""Account deletion — the cascade, and the guards around it.

Runs against a temporary SQLite database rather than mocks: the point of these
tests is that every table keyed to a user really is emptied, which a mock
can't tell you. (The listings table has no foreign key to users, so nothing
cascades on its own — a regression here is silent data retention.)
"""
from __future__ import annotations



from backend import auth


def _user(db, email="gone@example.com"):
    rec = db.create_user("u-1", email, auth.hash_password("hunter2hunter2"))
    assert rec not in (None, db.EMAIL_TAKEN)
    return rec["id"]


def test_delete_user_clears_every_table(dbmod):
    uid = _user(dbmod)
    dbmod.save_ebay_account(uid, refresh_token="tok", ebay_username="seller")
    dbmod.save_marketplace_account(uid, "etsy", refresh_token="etsy-tok",
                                   external_username="shop")
    dbmod.save_marketplace_account(uid, "depop", refresh_token="depop-tok")
    dbmod.save_prefs(uid, {"condition": "USED_GOOD"})
    dbmod.upsert_listing("sess0001", {"title": "A"}, user_id=uid)
    dbmod.upsert_listing("ebay-4242", {"title": "B"}, status="published", user_id=uid)
    dbmod.token_credit(uid, 50, ref="stripe-1")
    dbmod.token_spend(uid, 5, free_quota=50, period="2026-08", feature="identify")

    ids = dbmod.delete_user(uid)

    assert sorted(ids) == ["ebay-4242", "sess0001"]  # returned for photo purge
    assert dbmod.get_user_by_id(uid) is None
    assert dbmod.get_ebay_account(uid) is None
    assert dbmod.get_prefs(uid) == {}
    assert dbmod.list_listings(user_id=uid) == []
    assert dbmod.get_listing("sess0001") is None
    # Live OAuth credentials for other marketplaces must not survive.
    assert dbmod.get_marketplace_account(uid, "etsy") is None
    assert dbmod.get_marketplace_account(uid, "depop") is None
    # Billing rows: balance gone, ledger gone.
    assert dbmod.token_status(uid, "2026-08", 50) == {
        "free_used": 0, "free_remaining": 50, "purchased": 0}
    assert dbmod.token_history(uid) == []


def test_delete_user_leaves_other_accounts_marketplaces_and_tokens(dbmod):
    """The cascade is per-user: a deletion must not touch anyone else's
    marketplace connections or token balance."""
    mine = _user(dbmod, "mine@example.com")
    theirs = dbmod.create_user("u-2", "theirs@example.com", "hash")["id"]
    dbmod.save_marketplace_account(mine, "etsy", refresh_token="mine-tok")
    dbmod.save_marketplace_account(theirs, "etsy", refresh_token="theirs-tok")
    dbmod.token_credit(mine, 10, ref="stripe-mine")
    dbmod.token_credit(theirs, 25, ref="stripe-theirs")

    dbmod.delete_user(mine)

    assert dbmod.get_marketplace_account(theirs, "etsy")["refresh_token"] == "theirs-tok"
    assert dbmod.token_status(theirs, "2026-08", 50)["purchased"] == 25
    assert len(dbmod.token_history(theirs)) == 1


def test_delete_user_leaves_other_accounts_alone(dbmod):
    mine = _user(dbmod, "mine@example.com")
    theirs = dbmod.create_user("u-2", "theirs@example.com", "hash")["id"]
    dbmod.save_ebay_account(theirs, refresh_token="keep")
    dbmod.upsert_listing("mine01", {"title": "mine"}, user_id=mine)
    dbmod.upsert_listing("theirs01", {"title": "theirs"}, user_id=theirs)

    dbmod.delete_user(mine)

    assert dbmod.get_user_by_id(theirs) is not None
    assert dbmod.get_ebay_account(theirs)["refresh_token"] == "keep"
    assert [r["id"] for r in dbmod.list_listings(user_id=theirs)] == ["theirs01"]


def test_delete_user_reports_failure_rather_than_pretending(dbmod):
    """The one place db.py must NOT fail quietly: telling someone their account
    is gone when it isn't is the trap this feature exists to avoid."""
    assert dbmod.delete_user("no-such-user") is None


def test_anonymous_listings_are_not_swept_up(dbmod):
    """A listing with no owner belongs to no account and must survive."""
    uid = _user(dbmod)
    dbmod.upsert_listing("anon01", {"title": "anon"})  # no user_id
    dbmod.upsert_listing("mine01", {"title": "mine"}, user_id=uid)

    assert dbmod.delete_user(uid) == ["mine01"]
    assert dbmod.get_listing("anon01") is not None


def test_password_hash_lookup_is_separate_from_the_user_dict(dbmod):
    """get_user_by_id must never carry the hash — it's returned to clients."""
    uid = _user(dbmod)
    assert "password_hash" not in dbmod.get_user_by_id(uid)
    assert auth.verify_password("hunter2hunter2", dbmod.get_password_hash(uid))
    assert dbmod.get_password_hash("no-such-user") is None

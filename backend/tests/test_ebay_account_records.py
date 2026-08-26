"""Which eBay account a listing record belongs to, asked of the database.

Two helpers answer that question, and both used to answer it in Python after
dragging every one of a user's listing JSON blobs across the wire — one of
them (`count_foreign_listings`) on /api/ebay/status, which the app calls on
every boot. They now ask the database directly, with a `->>` / JSON_EXTRACT
predicate.

That rewrite is only safe if the SQL agrees with the truth test it replaced,
including on the shapes a store actually contains: a field that is absent
entirely (a record written before it existed), a field that is present and
empty, and a record from a different user. Those are what these tests pin —
a silent disagreement here does not raise, it just quietly stops labelling
the previous account's listings, which is the bug the labelling exists to fix.

Runs against a real (temporary) SQLite database via the shared `dbmod`
fixture, because a mock cannot tell you what the SQL does.
"""
from __future__ import annotations


# One of each shape a mirrored store really holds.
RECORDS = (
    ("own-1", {"title": "Ours", "ebay_listing_id": "111", "ebay_account": "alice"}),
    ("other-1", {"title": "Theirs", "ebay_listing_id": "222", "ebay_account": "bob"}),
    ("other-2", {"title": "Theirs too", "ebay_listing_id": "333", "ebay_account": "bob"}),
    # Present but blank: an eBay listing nothing has labelled yet.
    ("blank", {"title": "Unlabelled", "ebay_listing_id": "444", "ebay_account": ""}),
    # The field never existed on this row. `->>` yields NULL, not "".
    ("absent", {"title": "Older record", "ebay_listing_id": "555"}),
    # A plain local draft — never on eBay, so not eBay-scoped at all.
    ("draft", {"title": "Just a draft"}),
)


def _seed(db, user_id="u1"):
    for rid, data in RECORDS:
        db.upsert_listing(rid, data, status="published", user_id=user_id)
    return user_id


def _account_of(db, rid):
    return (db.get_listing(rid)["listing"]).get("ebay_account", "<absent>")


# --- counting another account's listings ------------------------------------

def test_counts_only_records_from_a_different_account(dbmod):
    _seed(dbmod)
    # Connected as alice: bob's two are foreign. Blank and absent are not —
    # an unlabelled record belongs to whoever is connected.
    assert dbmod.count_foreign_listings("u1", "alice") == 2


def test_the_connected_account_is_never_its_own_foreigner(dbmod):
    _seed(dbmod)
    assert dbmod.count_foreign_listings("u1", "bob") == 1  # only alice's


def test_an_unreadable_account_name_counts_everything_labelled(dbmod):
    """A connection made before the identity scope was granted can't say who
    it is. Every labelled record is then from some other account by
    definition, which is what the banner needs to be able to explain."""
    _seed(dbmod)
    assert dbmod.count_foreign_listings("u1", "") == 3


def test_counting_is_scoped_to_one_user(dbmod):
    _seed(dbmod, "u1")
    dbmod.upsert_listing("someone-else", {"title": "Not yours",
                                          "ebay_listing_id": "999",
                                          "ebay_account": "carol"},
                         status="published", user_id="u2")
    # u2's record is foreign to alice in every sense except the one that
    # matters: it isn't u1's to count.
    assert dbmod.count_foreign_listings("u1", "alice") == 2
    assert dbmod.count_foreign_listings("u2", "alice") == 1


def test_no_listings_is_zero_not_an_error(dbmod):
    assert dbmod.count_foreign_listings("nobody", "alice") == 0


# --- labelling the previous account's listings ------------------------------

def test_stamps_exactly_the_unlabelled_ebay_records(dbmod):
    _seed(dbmod)
    assert dbmod.stamp_ebay_account("u1", "previous account") == 2
    # The two that had an eBay item id and no account.
    assert _account_of(dbmod, "blank") == "previous account"
    assert _account_of(dbmod, "absent") == "previous account"
    # Already labelled: left exactly as they were.
    assert _account_of(dbmod, "own-1") == "alice"
    assert _account_of(dbmod, "other-1") == "bob"
    # Never on eBay: not eBay-scoped, so not labelled.
    assert _account_of(dbmod, "draft") == "<absent>"


def test_stamping_is_idempotent(dbmod):
    """The connect handler runs this on every account switch. A second pass
    must find nothing left to do rather than re-labelling the first pass's
    work with the new name."""
    _seed(dbmod)
    assert dbmod.stamp_ebay_account("u1", "previous account") == 2
    assert dbmod.stamp_ebay_account("u1", "previous account") == 0
    assert dbmod.stamp_ebay_account("u1", "someone new") == 0
    assert _account_of(dbmod, "blank") == "previous account"


def test_stamping_never_reaches_another_user(dbmod):
    _seed(dbmod, "u1")
    dbmod.upsert_listing("someone-else", {"title": "Not yours",
                                          "ebay_listing_id": "999"},
                         status="published", user_id="u2")
    dbmod.stamp_ebay_account("u1", "previous account")
    assert _account_of(dbmod, "someone-else") == "<absent>"


def test_a_blank_account_name_stamps_nothing(dbmod):
    """There is nothing to record. Writing "" would mark the records as
    handled while leaving them indistinguishable from unhandled ones."""
    _seed(dbmod)
    assert dbmod.stamp_ebay_account("u1", "   ") == 0
    assert _account_of(dbmod, "blank") == ""


def test_stamping_then_counting_agree(dbmod):
    """The two helpers are a pair: whatever the switch labelled as the
    previous account's is what the banner then has to count."""
    _seed(dbmod)
    dbmod.stamp_ebay_account("u1", "previous account")
    # Connected as a brand-new account: alice's, bob's, and the two just
    # labelled are all somebody else's now.
    assert dbmod.count_foreign_listings("u1", "newaccount") == 5


# --- the pool with no owner at all -------------------------------------------
#
# Stamping began in #176 (imports) and at publish alongside these tests, so a
# store that predates both holds eBay-linked records with ebay_account absent
# or blank. After an UNDETECTED account switch those are the old account's
# items wearing no label: count_foreign_listings cannot see them (it requires
# a non-empty label) and the release endpoint's default pass skips them. The
# seller ended up staring at another store's listings with no way to detect
# OR remove them — both counted and releasable now, but only explicitly.


def test_unowned_counts_ebay_linked_records_with_no_label(dbmod):
    _seed(dbmod)
    # blank ("") and absent (no field) both carry item ids -> 2. The plain
    # draft has no eBay identity, so it is not "unowned eBay", just local.
    assert dbmod.count_unowned_ebay_listings("u1") == 2


def test_unowned_is_scoped_to_the_user(dbmod):
    _seed(dbmod)
    dbmod.upsert_listing("someone-elses",
                         {"title": "x", "ebay_listing_id": "999"},
                         status="published", user_id="u2")
    assert dbmod.count_unowned_ebay_listings("u1") == 2
    assert dbmod.count_unowned_ebay_listings("u2") == 1


def test_labelled_records_are_never_unowned(dbmod):
    _seed(dbmod)
    # alice's and bob's stamped records belong to somebody; the count answers
    # a different question than count_foreign_listings and must not overlap
    # it, or the UI would double-count a record in its banner.
    assert (dbmod.count_unowned_ebay_listings("u1")
            + dbmod.count_foreign_listings("u1", "alice")) == 4  # 2 + 2, disjoint


# --- what the release endpoint may unlink ------------------------------------

def _releasable(data, connected, include_unowned=False):
    from backend.services import ebay_account
    return ebay_account.releasable(data, connected, include_unowned)


def test_release_default_touches_only_labelled_foreigners(dbmod):
    # The exact records the endpoint released before include_unowned existed.
    assert _releasable({"ebay_account": "bob", "ebay_listing_id": "1"}, "alice")
    assert _releasable({"ebay_account": "previous account",
                        "ebay_listing_id": "1"}, "alice")
    assert not _releasable({"ebay_account": "alice", "ebay_listing_id": "1"}, "alice")
    assert not _releasable({"ebay_listing_id": "1"}, "alice")
    assert not _releasable({"ebay_account": "", "ebay_listing_id": "1"}, "alice")


def test_release_unowned_is_opt_in_and_needs_an_ebay_identity(dbmod):
    # The flag reaches the unlabelled pool...
    assert _releasable({"ebay_listing_id": "1"}, "alice", include_unowned=True)
    assert _releasable({"ebay_account": "", "marketplaces": {"ebay": {"listing_id": "1"}}},
                       "alice", include_unowned=True)
    # ...but never a plain local draft: there is nothing to unlink.
    assert not _releasable({"title": "just a draft"}, "alice", include_unowned=True)
    # ...and never relabels a record stamped as the CONNECTED account's own.
    assert not _releasable({"ebay_account": "alice", "ebay_listing_id": "1"},
                           "alice", include_unowned=True)

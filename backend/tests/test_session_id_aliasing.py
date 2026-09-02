"""One session id must name exactly one session.

Session ids named two different things at once. The database keyed rows on the
id exactly as it arrived; storage keyed directories and R2 objects on the id
with every non-alphanumeric character DELETED. So "abc123" and "abc123-" were
different rows and the same directory.

That is not a tidiness problem, it is an authorization bypass, because the
ownership guard and the file operation consulted different namespaces:

  _assert_session_owner("abc123-")  -> no such row -> "unowned, allow"
  storage.session_dir("abc123-")    -> .../sessions/abc123 -> the victim's photos

Session ids ride in public /media URLs, so knowing one is the normal case, not
a breach. Appending any non-alphanumeric character to a known id produced an
alias that missed the row and hit the directory — no account, no guessing, no
collision needed.

The fix is to make canonicalization INJECTIVE: accept an id or reject it,
never rewrite it. Then name(x) == name(y) implies x == y, and an alias cannot
be constructed at all. These tests pin that property and the attacks it kills.
"""
from __future__ import annotations

import pytest

from backend import objstore, storage

# Ids that are legitimate and must keep working. The hyphen matters: imported
# listings are minted as "ebay-<item id>" (services/listing_sync.py), so the
# accepted charset cannot be pure alphanumerics.
VALID = [
    "3aaeb40637a1",            # a normal uploaded session
    "ebay-168433981627",       # an imported eBay listing
    "THRYFT-abc123",
    "a" * 128,                 # at the length bound
]

# Ids outside the accepted form. These must be REFUSED \u2014 under the old rule
# each was silently rewritten into some other session's name.
REJECTED = [
    "3aaeb40637a1.",
    "3aaeb40637a1!",
    "3aaeb40637a1/",
    "3aaeb40637a1%2e",
    "../../etc/passwd",
    "..",
    "\u0301abc",               # a leading combining accent
    "abc\u0301",               # a trailing combining accent
    "",
    "-leading",                # must start alphanumeric
    "a" * 129,                 # past the length bound
]

# Ids that the accepted charset admits and that LOOK like a neighbour's id.
# These are not rejected and must not be: they are ordinary, distinct session
# ids. What matters is that each one names its OWN storage rather than
# collapsing onto the id it resembles \u2014 which is exactly what the old
# stripping rule did.
NEIGHBOURS = [
    ("3aaeb40637a1-", "3aaeb40637a1"),
    ("e-bay123", "ebay123"),
    ("ebay-123", "ebay123"),
]


@pytest.mark.parametrize("session_id", VALID)
def test_a_valid_id_is_returned_unchanged(session_id):
    """Injectivity, stated directly: canonicalizing an accepted id is the
    identity. Any rewrite reintroduces the two-namespace bug."""
    assert storage.safe_session_name(session_id) == session_id


@pytest.mark.parametrize("session_id", REJECTED)
def test_a_malformed_id_is_rejected_not_rewritten(session_id):
    """Rejecting is the whole fix. Rewriting "abc123." down to "abc123" is
    what silently handed the caller someone else's directory."""
    with pytest.raises(ValueError):
        storage.safe_session_name(session_id)


@pytest.mark.parametrize("session_id,neighbour", NEIGHBOURS)
def test_a_lookalike_id_names_its_own_storage(session_id, neighbour):
    """The accepted charset admits ids that resemble each other. That is
    fine, and must stay fine: they are separate sessions. The bug was that
    they collapsed onto ONE directory, so possessing either reached both."""
    assert storage.safe_session_name(session_id) == session_id
    assert storage.session_dir(session_id) != storage.session_dir(neighbour)


def test_no_two_distinct_valid_ids_share_a_storage_name():
    """The property the bypass violated, asserted over the accepted set."""
    names = [storage.safe_session_name(i) for i in VALID]
    assert len(set(names)) == len(VALID)


def test_the_hyphen_in_an_imported_id_survives():
    """"ebay-123" and "ebay123" were the same directory. They are two
    different listings, and eBay item 123 is not eBay item -123."""
    assert storage.safe_session_name("ebay-123") == "ebay-123"
    assert storage.session_dir("ebay-123") != storage.session_dir("ebay123")


def test_object_keys_follow_the_same_rule(fresh_config):
    """R2 keys and on-disk dirs must agree, or the offload sweep loses track
    of imported listings' photos — the bug this naming rule already existed
    to prevent. It must keep holding under the stricter rule."""
    fresh_config()
    assert objstore.key_for("ebay-123", "img_001.jpg") \
        == "sessions/ebay-123/optimized/img_001.jpg"
    for session_id in VALID:
        key = objstore.key_for(session_id, "img_000.jpg")
        assert key == (f"sessions/{storage.session_dir(session_id).name}"
                       "/optimized/img_000.jpg")


@pytest.mark.parametrize("session_id", REJECTED)
def test_object_keys_reject_malformed_ids_too(fresh_config, session_id):
    fresh_config()
    with pytest.raises(ValueError):
        objstore.key_for(session_id, "img_000.jpg")


@pytest.mark.parametrize("session_id,neighbour", NEIGHBOURS)
def test_lookalike_ids_get_separate_object_keys(fresh_config, session_id,
                                                neighbour):
    """The R2 half of the same property. Two sellers' photos sharing one key
    prefix is how one could overwrite the other's."""
    fresh_config()
    assert objstore.key_for(session_id, "img_000.jpg") \
        != objstore.key_for(neighbour, "img_000.jpg")


def test_a_new_id_has_real_entropy():
    """12 hex characters is 48 bits and no uniqueness check. Ids are not the
    security boundary once the alias bypass is closed, but a birthday
    collision would silently merge two sellers' photos into one directory."""
    minted = storage.new_session_id()
    assert len(minted) >= 32
    assert storage.safe_session_name(minted) == minted
    assert len({storage.new_session_id() for _ in range(200)}) == 200

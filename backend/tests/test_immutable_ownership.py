"""Which eBay account a listing belongs to, decided on something immutable.

Ownership was keyed on `ebay_account`, the seller's eBay USERNAME. A username
is a display name the seller can change, so it is not a tenancy key:

  - a seller who renames orphans every record stamped with the old name;
  - a handle that is released and re-registered can make one seller's records
    match a different seller;
  - eBay increasingly omits the username from payloads, and the identity call
    403s on connections made before that scope was granted — so "" is common,
    and "" was treated as "matches everything".

That last one is the sharp edge. `belongs_to` returned True when EITHER side
was blank, so a connected account whose identity could not be read passed the
ownership test against every record in the database. GetItem answers for any
seller's item (item detail is public), so a status sweep would then happily
re-confirm another account's listings as live under this one.

eBay's immutable `userId` is now stored and preferred. The rules:

  - both sides have an immutable id  -> equality decides, with no fallback;
  - the record has one and the caller does not -> refuse (nothing is proven);
  - neither has one (a legacy record) -> fall back to the username, which is
    all such a record has, for READS;
  - writes require proof either way — an unproven record is shown, not
    published to.
"""
from __future__ import annotations

from backend.services import listing_sync

SELLER_A = {"ebay_user_id": "uid-aaa", "ebay_username": "lamp-shop"}
SELLER_B = {"ebay_user_id": "uid-bbb", "ebay_username": "other-shop"}
UNKNOWN = {"ebay_user_id": "", "ebay_username": ""}


def _rec(**over) -> dict:
    base = {"title": "Blue lamp", "ebay_listing_id": "110000000001"}
    base.update(over)
    return base


# ------------------------------------------------------- immutable identity

def test_the_immutable_id_decides_when_both_sides_have_one():
    owned = _rec(ebay_account_id="uid-aaa", ebay_account="lamp-shop")
    assert listing_sync.owns(owned, SELLER_A)
    assert not listing_sync.owns(owned, SELLER_B)


def test_a_rename_does_not_orphan_the_sellers_own_listing():
    """The whole reason for an immutable key. The seller renamed; the record
    still carries the old display name and is still theirs."""
    renamed = dict(SELLER_A, ebay_username="lamp-emporium")
    owned = _rec(ebay_account_id="uid-aaa", ebay_account="lamp-shop")
    assert listing_sync.owns(owned, renamed)


def test_a_reused_handle_does_not_capture_someone_elses_listing():
    """Usernames can be released and re-registered. Matching on one would
    hand the previous holder's listings to whoever took the name."""
    impostor = {"ebay_user_id": "uid-zzz", "ebay_username": "lamp-shop"}
    owned = _rec(ebay_account_id="uid-aaa", ebay_account="lamp-shop")
    assert not listing_sync.owns(owned, impostor)


def test_the_immutable_id_wins_over_a_matching_username():
    """No fallback once both sides are identified: a username agreeing does
    not rescue an id that disagrees."""
    owned = _rec(ebay_account_id="uid-aaa", ebay_account="lamp-shop")
    same_name = {"ebay_user_id": "uid-bbb", "ebay_username": "lamp-shop"}
    assert not listing_sync.owns(owned, same_name)


# ------------------------------------------------------------- failing open

def test_an_unidentified_caller_does_not_own_an_identified_record():
    """The fail-open half. A connected account whose identity could not be
    read used to pass against every record in the database."""
    owned = _rec(ebay_account_id="uid-aaa", ebay_account="lamp-shop")
    assert not listing_sync.owns(owned, UNKNOWN)


def test_an_unidentified_caller_cannot_write_to_a_legacy_record():
    """Reads of legacy records still work, so nothing strands. Writes need
    proof: a revise sent to the wrong account's item is not recoverable."""
    legacy = _rec()
    assert not listing_sync.may_write(legacy, UNKNOWN)


def test_a_legacy_record_still_reads_under_a_matching_username():
    """Records predating immutable ids have only the name. Refusing to show
    them would strand sellers who did nothing wrong."""
    legacy = _rec(ebay_account="lamp-shop")
    assert listing_sync.owns(legacy, SELLER_A)
    assert not listing_sync.owns(legacy, SELLER_B)


def test_an_unstamped_legacy_record_still_reads():
    """Oldest records carry no account at all."""
    assert listing_sync.owns(_rec(), SELLER_A)


def test_a_proven_record_may_be_written():
    owned = _rec(ebay_account_id="uid-aaa", ebay_account="lamp-shop")
    assert listing_sync.may_write(owned, SELLER_A)
    assert not listing_sync.may_write(owned, SELLER_B)


def test_a_legacy_record_may_be_written_by_a_named_matching_account():
    """A named username match is the best proof such a record can offer, and
    refusing it would block every pre-existing listing from ever being
    edited again."""
    legacy = _rec(ebay_account="lamp-shop")
    assert listing_sync.may_write(legacy, SELLER_A)
    assert not listing_sync.may_write(legacy, SELLER_B)


# ---------------------------------------------- a public read proves nothing

def test_the_previous_account_sentinel_is_never_owned():
    """UNKNOWN_ACCOUNT means "we could not name the account". It must not
    match whoever is connected now — that is how a sweep re-confirmed the old
    account's listings under the new one."""
    stamped = _rec(ebay_account=listing_sync.UNKNOWN_ACCOUNT)
    assert not listing_sync.owns(stamped, SELLER_A)
    assert not listing_sync.may_write(stamped, SELLER_A)


def test_identity_can_be_read_from_a_creds_bundle():
    """The publish path carries creds, not a bare username. Ownership has to
    be answerable from what the caller actually holds."""
    creds = {"access_token": "tok", "ebay_user_id": "uid-aaa",
             "ebay_username": "lamp-shop"}
    owned = _rec(ebay_account_id="uid-aaa")
    assert listing_sync.owns(owned, creds)

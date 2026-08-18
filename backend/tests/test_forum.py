"""The community forum — persistence, ownership, and the vote invariant.

Runs against a temporary SQLite database (same approach as the notification
and account-deletion tests): the guarantees under test — one vote per person
per item, counts that match reality, a delete that leaves nothing behind —
live in the schema and the transactions, which a mock can't exercise.

Deliberately imports only db + services.forum, never main: CI installs the
light dependency set and never has the image stack available.
"""
from __future__ import annotations

import importlib

import pytest

from backend import auth
from backend.services import forum


@pytest.fixture
def dbmod(monkeypatch, tmp_path):
    """A fresh db module bound to a scratch SQLite file."""
    from backend import config, db

    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    importlib.reload(db)
    monkeypatch.setattr(db.config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    return db


def _user(db, uid="u-1", email="seller@example.com", name=""):
    rec = db.create_user(uid, email, auth.hash_password("hunter2hunter2"))
    assert rec not in (None, db.EMAIL_TAKEN)
    if name:
        db.update_user(rec["id"], display_name=name)
    return rec["id"]


@pytest.fixture
def alice(dbmod):
    return _user(dbmod, "u-1", "alice@example.com", "Alice")


@pytest.fixture
def bob(dbmod):
    return _user(dbmod, "u-2", "bob@example.com")


# --- posting ---------------------------------------------------------------

def test_a_post_appears_on_the_board_with_its_author(dbmod, alice):
    post = dbmod.create_forum_post(alice, "price-check", "Worth flipping?",
                                   "Found this at Goodwill for $4.")
    assert post and post["author"] == "Alice"
    assert post["replies"] == 0 and post["votes"] == 0 and post["edited"] is False

    page = dbmod.list_forum_posts()
    assert page["total"] == 1
    assert [p["id"] for p in page["posts"]] == [post["id"]]


def test_the_author_falls_back_to_the_email_local_part_never_the_address(dbmod, bob):
    post = dbmod.create_forum_post(bob, "general", "Hi", "New here.")
    assert post["author"] == "bob"  # not bob@example.com


def test_replying_bumps_the_count_and_the_activity_clock(dbmod, alice, bob):
    post = dbmod.create_forum_post(alice, "general", "Best box size?", "Ideas?")
    before = post["last_activity_at"]

    reply = dbmod.add_forum_reply(post["id"], bob, "12x12x8 covers most of it.")
    assert reply and reply["author"] == "bob"

    thread = dbmod.get_forum_post(post["id"])
    assert thread["replies"] == 1
    assert [r["id"] for r in thread["replies_list"]] == [reply["id"]]
    assert thread["last_activity_at"] >= before


def test_replying_to_a_missing_post_writes_nothing(dbmod, alice):
    assert dbmod.add_forum_reply("no-such-post", alice, "hello?") is None


def test_a_listing_snapshot_rides_along_with_the_post(dbmod, alice):
    post = dbmod.create_forum_post(
        alice, "price-check", "Priced right?", "Thoughts?",
        listing_id="sess0001",
        attachment={"listing_id": "sess0001", "title": "Levi's 501",
                    "price": 48.0, "currency": "USD", "image": ""})
    fetched = dbmod.get_forum_post(post["id"])
    assert fetched["attachment"]["title"] == "Levi's 501"
    assert fetched["listing_id"] == "sess0001"


# --- votes -----------------------------------------------------------------

def test_one_person_can_only_upvote_once(dbmod, alice, bob):
    post = dbmod.create_forum_post(alice, "wins", "Bought $4, sold $180", "!")

    assert dbmod.set_forum_vote(bob, "post", post["id"]) == {"votes": 1, "voted": True}
    # Double tap / retried request — still one vote.
    assert dbmod.set_forum_vote(bob, "post", post["id"]) == {"votes": 1, "voted": True}

    assert dbmod.set_forum_vote(alice, "post", post["id"])["votes"] == 2
    assert dbmod.get_forum_post(post["id"], viewer_id=bob)["voted"] is True
    assert dbmod.get_forum_post(post["id"])["voted"] is False  # logged out


def test_un_voting_is_idempotent_too(dbmod, alice, bob):
    post = dbmod.create_forum_post(alice, "wins", "Bought $4, sold $180", "!")
    dbmod.set_forum_vote(bob, "post", post["id"])

    assert dbmod.set_forum_vote(bob, "post", post["id"], on=False) == {
        "votes": 0, "voted": False}
    assert dbmod.set_forum_vote(bob, "post", post["id"], on=False)["votes"] == 0


def test_replies_can_be_voted_on_independently(dbmod, alice, bob):
    post = dbmod.create_forum_post(alice, "general", "Question", "?")
    reply = dbmod.add_forum_reply(post["id"], bob, "Answer")

    dbmod.set_forum_vote(alice, "reply", reply["id"])
    thread = dbmod.get_forum_post(post["id"], viewer_id=alice)
    assert thread["votes"] == 0
    assert thread["replies_list"][0]["votes"] == 1
    assert thread["replies_list"][0]["voted"] is True


def test_voting_on_something_that_is_gone_reports_it(dbmod, alice):
    assert dbmod.set_forum_vote(alice, "post", "no-such-post") is None
    assert dbmod.set_forum_vote(alice, "spam", "x") is None  # bad target type


# --- ownership -------------------------------------------------------------

def test_only_the_author_can_edit_or_delete_a_post(dbmod, alice, bob):
    post = dbmod.create_forum_post(alice, "general", "Mine", "Body")

    assert dbmod.edit_forum_post(post["id"], bob, title="Hijacked title") is None
    assert dbmod.delete_forum_post(post["id"], bob) is False
    assert dbmod.get_forum_post(post["id"])["title"] == "Mine"

    edited = dbmod.edit_forum_post(post["id"], alice, body="Body, revised")
    assert edited["body"] == "Body, revised" and edited["edited"] is True
    assert dbmod.delete_forum_post(post["id"], alice) is True
    assert dbmod.get_forum_post(post["id"]) is None


def test_only_the_author_can_edit_or_delete_a_reply(dbmod, alice, bob):
    post = dbmod.create_forum_post(alice, "general", "Question", "?")
    reply = dbmod.add_forum_reply(post["id"], bob, "Answer")

    assert dbmod.edit_forum_reply(reply["id"], alice, "Not yours") is None
    assert dbmod.delete_forum_reply(reply["id"], alice) is False

    assert dbmod.edit_forum_reply(reply["id"], bob, "Answer, clarified")["edited"]
    assert dbmod.delete_forum_reply(reply["id"], bob) is True
    assert dbmod.get_forum_post(post["id"])["replies"] == 0


def test_deleting_a_thread_takes_its_replies_and_votes_with_it(dbmod, alice, bob):
    post = dbmod.create_forum_post(alice, "general", "Going away", "Body")
    reply = dbmod.add_forum_reply(post["id"], bob, "Noted")
    dbmod.set_forum_vote(bob, "post", post["id"])
    dbmod.set_forum_vote(alice, "reply", reply["id"])

    assert dbmod.delete_forum_post(post["id"], alice) is True
    # Nothing orphaned: a re-created id must not inherit the old votes.
    assert dbmod.edit_forum_reply(reply["id"], bob, "still here?") is None
    with dbmod.Session(dbmod._get_engine()) as s:
        assert s.query(dbmod.ForumVote).count() == 0
        assert s.query(dbmod.ForumReply).count() == 0


# --- the board's filters ---------------------------------------------------

def test_category_filter_search_and_unanswered(dbmod, alice, bob):
    ship = dbmod.create_forum_post(alice, "shipping", "Cheapest way to ship a mirror",
                                   "40lb and fragile.")
    win = dbmod.create_forum_post(alice, "wins", "Flipped a Pyrex bowl",
                                  "Paid 3, sold 60.")
    dbmod.add_forum_reply(win["id"], bob, "Nice find")

    assert [p["id"] for p in dbmod.list_forum_posts(category="shipping")["posts"]] \
        == [ship["id"]]
    assert [p["id"] for p in dbmod.list_forum_posts(query="mirror")["posts"]] \
        == [ship["id"]]
    assert [p["id"] for p in dbmod.list_forum_posts(query="pyrex")["posts"]] \
        == [win["id"]]  # case-insensitive
    assert [p["id"] for p in dbmod.list_forum_posts(sort="unanswered")["posts"]] \
        == [ship["id"]]
    assert [p["id"] for p in dbmod.list_forum_posts(author_id=bob)["posts"]] == []


def test_search_treats_wildcards_as_literal_text(dbmod, alice):
    hit = dbmod.create_forum_post(alice, "general", "Sold at 50% off", "Still profit.")
    dbmod.create_forum_post(alice, "general", "Unrelated thread", "Nothing here.")

    found = dbmod.list_forum_posts(query="50%")["posts"]
    assert [p["id"] for p in found] == [hit["id"]]


def test_top_sorts_by_votes_and_recent_by_activity(dbmod, alice, bob):
    quiet = dbmod.create_forum_post(alice, "general", "Quiet thread", "Body")
    loud = dbmod.create_forum_post(alice, "general", "Loud thread", "Body")
    dbmod.set_forum_vote(bob, "post", loud["id"])
    dbmod.add_forum_reply(bob, "x", "y")  # no-op, keeps activity ordering honest
    dbmod.add_forum_reply(quiet["id"], bob, "Bumped")

    assert [p["title"] for p in dbmod.list_forum_posts(sort="top")["posts"]][0] \
        == "Loud thread"
    assert [p["title"] for p in dbmod.list_forum_posts(sort="recent")["posts"]][0] \
        == "Quiet thread"  # a reply lifts it back to the top


def test_paging_reports_the_total_for_the_same_filters(dbmod, alice):
    for i in range(5):
        dbmod.create_forum_post(alice, "general", f"Thread number {i}", "Body")
    page = dbmod.list_forum_posts(limit=2, offset=2)
    assert page["total"] == 5 and len(page["posts"]) == 2


def test_stats_count_people_who_actually_spoke(dbmod, alice, bob):
    post = dbmod.create_forum_post(alice, "general", "First thread", "Body")
    dbmod.add_forum_reply(post["id"], bob, "First reply")
    _user(dbmod, "u-3", "lurker@example.com")

    assert dbmod.forum_stats() == {"posts": 1, "replies": 1, "members": 2}


# --- account deletion ------------------------------------------------------

def test_deleting_an_account_erases_its_forum_footprint(dbmod, alice, bob):
    mine = dbmod.create_forum_post(alice, "general", "My thread", "Body")
    theirs = dbmod.create_forum_post(bob, "general", "Their thread", "Body")
    dbmod.add_forum_reply(theirs["id"], alice, "My reply on their thread")
    dbmod.set_forum_vote(alice, "post", theirs["id"])
    dbmod.add_forum_reply(mine["id"], bob, "Their reply on my thread")

    assert dbmod.delete_user(alice) is not None

    assert dbmod.get_forum_post(mine["id"]) is None       # thread gone
    remaining = dbmod.get_forum_post(theirs["id"])
    assert remaining is not None                          # theirs survives
    assert remaining["replies_list"] == []                # my reply is gone
    assert remaining["replies"] == 0
    assert remaining["votes"] == 0                        # my vote is gone
    with dbmod.Session(dbmod._get_engine()) as s:
        assert s.query(dbmod.ForumVote).count() == 0


def test_a_deleted_author_never_leaves_an_email_on_the_board(dbmod, alice, bob):
    """A reply outlives its author only if the thread does — but if a row ever
    is orphaned, the board must not fall back to showing an address."""
    assert dbmod._author_name("", "") == "Former member"
    assert dbmod._author_name("", "someone@example.com") == "someone"


# --- what the composer will and won't accept -------------------------------

def test_titles_are_single_line_and_bounded():
    assert forum.clean_title("  Is   this  worth flipping?  ") \
        == "Is this worth flipping?"
    assert forum.clean_title("Pasted\nfrom\na\ndraft note") \
        == "Pasted from a draft note"
    assert len(forum.clean_title("x" * 500)) == forum.TITLE_MAX

    with pytest.raises(forum.ForumError):
        forum.clean_title("short")
    with pytest.raises(forum.ForumError):
        forum.clean_title(None)


def test_bodies_and_replies_are_bounded():
    assert forum.clean_body("  keeps\n\ninterior breaks  ") \
        == "keeps\n\ninterior breaks"
    with pytest.raises(forum.ForumError):
        forum.clean_body("   ")
    with pytest.raises(forum.ForumError):
        forum.clean_body("x" * (forum.BODY_MAX + 1))
    with pytest.raises(forum.ForumError):
        forum.clean_reply("x")
    with pytest.raises(forum.ForumError):
        forum.clean_reply("x" * (forum.REPLY_MAX + 1))


def test_an_unknown_category_files_the_post_rather_than_losing_it():
    assert forum.normalize_category("Price-Check") == "price-check"
    assert forum.normalize_category("nonsense") == forum.DEFAULT_CATEGORY
    assert forum.normalize_category(None) == forum.DEFAULT_CATEGORY


# --- no database -----------------------------------------------------------

def test_without_a_database_the_board_is_empty_rather_than_broken(monkeypatch):
    from backend import config, db

    monkeypatch.setattr(config, "DATABASE_URL", "")
    importlib.reload(db)
    monkeypatch.setattr(db.config, "DATABASE_URL", "")

    assert db.list_forum_posts() == {"posts": [], "total": 0}
    assert db.get_forum_post("x") is None
    assert db.create_forum_post("u", "general", "Title here", "Body") is None
    assert db.add_forum_reply("p", "u", "Body") is None
    assert db.set_forum_vote("u", "post", "p") is None
    assert db.delete_forum_post("p", "u") is False
    assert db.forum_stats() == {"posts": 0, "replies": 0, "members": 0}

"""The seller community: what a thread may say, and where it may live.

Persistence is db.py's job and the HTTP shape is main.py's; this module owns
the rules that sit between them — the category catalog and the validation
every write goes through. Kept separate so both the routes and the tests can
reach it without importing the image stack.
"""
from __future__ import annotations

from typing import Optional

# The board's sections. `id` is what's stored (and what old rows keep saying),
# so renaming a label is free but changing an id is a migration.
CATEGORIES = [
    {"id": "general", "label": "General",
     "blurb": "Anything reselling — say hi, swap notes."},
    {"id": "price-check", "label": "Price checks",
     "blurb": "What's this worth? Post the item and let the room weigh in."},
    {"id": "sourcing", "label": "Sourcing",
     "blurb": "Thrift stores, estate sales, and what's actually worth grabbing."},
    {"id": "shipping", "label": "Shipping",
     "blurb": "Boxes, rates, labels, and the ones that went wrong."},
    {"id": "platforms", "label": "Platforms",
     "blurb": "eBay, Etsy, Depop — fees, policies, and getting seen."},
    {"id": "wins", "label": "Wins",
     "blurb": "Bought for $4, sold for $180. Show the room."},
]

CATEGORY_IDS = {c["id"] for c in CATEGORIES}
DEFAULT_CATEGORY = "general"

TITLE_MAX = 140
TITLE_MIN = 8
BODY_MAX = 8000
REPLY_MAX = 4000
REPLY_MIN = 2


class ForumError(ValueError):
    """A post the board won't accept, with a message meant for the person who
    wrote it — the routes turn this straight into a 400."""


def normalize_category(value: Optional[str]) -> str:
    """An unknown category lands in General rather than 400ing: the catalog
    can shrink between a client's page load and its POST, and losing someone's
    typed-out post to that is a worse outcome than filing it one shelf over."""
    value = (value or "").strip().lower()
    return value if value in CATEGORY_IDS else DEFAULT_CATEGORY


def clean_title(raw: Optional[str]) -> str:
    """Titles are single-line: newlines pasted from a draft would otherwise
    break every row of the board's list."""
    title = " ".join((raw or "").split())
    if len(title) < TITLE_MIN:
        raise ForumError(
            f"Give your post a title — at least {TITLE_MIN} characters, so "
            "people can tell what it's about from the list.")
    return title[:TITLE_MAX]


def clean_body(raw: Optional[str], *, required: bool = True) -> str:
    """Trailing whitespace goes; interior line breaks stay (people format
    their posts with them)."""
    body = (raw or "").strip()
    if required and not body:
        raise ForumError("Write something in the post before you share it.")
    if len(body) > BODY_MAX:
        raise ForumError(
            f"That's longer than the {BODY_MAX:,}-character limit — trim it "
            "or split it into two posts.")
    return body


def clean_reply(raw: Optional[str]) -> str:
    reply = (raw or "").strip()
    if len(reply) < REPLY_MIN:
        raise ForumError("Write a reply before you post it.")
    if len(reply) > REPLY_MAX:
        raise ForumError(
            f"Replies top out at {REPLY_MAX:,} characters — post the long "
            "version as its own thread.")
    return reply

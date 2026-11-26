"""A refine sends the model the listing COPY, not the whole record.

refine() takes back only REFINED_FIELDS from the model's answer -- the title,
the description, the specifics and the rest of the copy. It sent the whole
model_dump() anyway, and told the model to "return the FULL updated listing
... with the same fields". On a listing that has been live, that whole record
carries eBay's last copy of it (remote_shadow), any conflicts held between
the two, the marketplace states, the Etsy and Depop fields, the image URLs:
the description two or three times over, paid for going in and again coming
back, against an 8,192-token cap that raises "too long" when it is reached.
"""
from __future__ import annotations

import json
import types

import pytest

pytest.importorskip("anthropic")

from backend.models import ItemSpecific, Listing  # noqa: E402
from backend.services import claude_ai  # noqa: E402

LONG = ("Opening line of this one pair. "
        + "Soft, faded, broken-in denim with a chain-stitched hem. " * 40)


class _Client:
    def __init__(self):
        self.sent = []
        self.messages = self

    def create(self, **kw):
        self.sent.append(kw)
        prompt = kw["messages"][0]["content"]
        draft = json.loads(prompt.split("JSON:\n\n", 1)[1].split("\n\nApply", 1)[0])
        draft["title"] = "Levi's 501 Jeans Punchier"
        return types.SimpleNamespace(
            stop_reason="end_turn", usage=None,
            content=[types.SimpleNamespace(type="text", text=json.dumps(draft))])


def _live_listing() -> Listing:
    return Listing(
        title="Levi's 501 Jeans", description=LONG, price=45.0,
        category_id="11483", item_specifics=[ItemSpecific(name="Size", value="32")],
        images=["img_000.jpg"], image_urls=["https://cdn.test/img_000.jpg"],
        remote_shadow={"title": "Levi's 501 Jeans", "description": LONG},
        conflicts={"description": {"local": LONG, "remote": LONG}},
        dirty_fields=["description"])


def test_only_the_fields_a_refine_writes_are_sent(monkeypatch):
    client = _Client()
    monkeypatch.setattr(claude_ai, "_client", lambda: client)
    listing = _live_listing()
    updated = claude_ai.refine(listing, "make the title punchier")

    prompt = client.sent[-1]["messages"][0]["content"]
    sent = json.loads(prompt.split("JSON:\n\n", 1)[1].split("\n\nApply", 1)[0])
    assert set(sent) <= set(claude_ai.REFINED_FIELDS)
    assert "remote_shadow" not in prompt and "conflicts" not in prompt
    assert prompt.count("Opening line of this one pair.") == 1, \
        "the description goes in once"

    # ...and the answer is read exactly as before: the rewrite lands, and what
    # the model never saw stands as the seller left it.
    assert updated.title == "Levi's 501 Jeans Punchier"
    assert updated.description == LONG.strip()
    assert updated.price == 45.0
    assert updated.category_id == "11483"
    assert updated.remote_shadow == listing.remote_shadow
    assert updated.image_urls == listing.image_urls
    assert [(s.name, s.value) for s in updated.item_specifics] == [("Size", "32")]

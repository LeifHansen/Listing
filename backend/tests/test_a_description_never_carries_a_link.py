"""A listing description may not point the buyer off the listing page.

eBay's links policy is not a style preference. A description carrying a URL,
an email address, a phone number or a "visit our store" gets the listing
demoted and then REMOVED, and a seller who collects enough removals loses the
account. That makes it the most expensive thing the description rule can get
wrong: a weak keyword costs a little search position, a link costs the whole
listing after the photos have already been paid for.

Sellers were losing live listings to exactly this, so the ban is pinned here
rather than left to survive on good intentions. Two prompts can put a link
into a description and both are covered:

  * the identify pass, which writes the body from scratch (LISTING_SCHEMA);
  * refine, which rewrites it — and which also has to SCRUB, because a draft
    imported from an existing eBay listing arrives carrying whatever the old
    description had in it.

The refine half is the one worth stating twice: "add my store link" is a
thing sellers really ask for, and obeying it publishes a policy violation. So
that clause deliberately outranks the seller's instruction, unlike the style
rules beside it, and the test below holds it there.

Like its sibling test_a_description_is_written_to_be_found, this imports
services.listing_prompt and nothing heavier: the Anthropic SDK is not
installed in CI, and a test that importorskips it is a test that never runs
where it matters.
"""
from __future__ import annotations

import re

from backend.services import listing_prompt


def _links_rule() -> str:
    """The NO LINKS block alone, flattened to one line.

    It is a wrapped prompt, so a phrase the model reads as a sentence is split
    across lines in the source.
    """
    text = listing_prompt.LISTING_SCHEMA
    start = text.index("\n  NO LINKS")
    return re.sub(r"\s+", " ", text[start:text.index("\n- ALWAYS estimate", start)])


def _refine_rule() -> str:
    return re.sub(r"\s+", " ", listing_prompt.REFINE_ORDER_RULE)


def test_the_description_rule_has_a_links_ban_at_all():
    """The block has to exist before anything else here means much."""
    rule = _links_rule()
    assert "NO LINKS" in rule


def test_the_ban_says_what_it_costs():
    """A rule with its own reason attached survives the next edit, and is
    followed more reliably than a bare prohibition."""
    rule = _links_rule()
    assert "REMOVES" in rule
    assert "links policy" in rule.lower()


def test_every_shape_a_url_arrives_in_is_named():
    """"No links" alone leaves a model free to read a bare domain as not a
    link. Each form is named because each one has shipped."""
    rule = _links_rule().lower()
    for form in ("http://", "https://", "www.", "bare or shortened domain",
                 "<a href>"):
        assert form in rule, f"the links ban no longer names {form!r}"


def test_the_spelled_out_dodge_is_banned_too():
    """"mystore dot com" is what a model writes when it has been told not to
    write a URL, and eBay reads it as the deliberate evasion it is."""
    rule = _links_rule().lower()
    assert "dot com" in rule
    assert "still a link" in rule


def test_contact_details_count_as_links():
    """The policy is about leaving the page, not about the protocol prefix:
    an email address or a phone number does the same thing a URL does."""
    rule = _links_rule().lower()
    for detail in ("email address", "phone number", "social account",
                   "qr code"):
        assert detail in rule, f"the links ban no longer names {detail!r}"


def test_an_invitation_to_leave_is_a_link_without_the_url():
    """"see my other items at", "check our website" and "Google the model
    number" carry no URL and are removed just the same."""
    rule = _links_rule().lower()
    assert "invitation to leave" in rule
    for phrase in ("visit our store", "check our website"):
        assert phrase in rule, f"the links ban no longer names {phrase!r}"


def test_the_ban_follows_the_fact_and_not_its_source():
    """A URL reaches this prompt from several directions — the seller's notes
    box, a distilled reference page, a research source, a watermark, the
    printing on the box. Naming them in one clause is what stops each new
    source arriving as a fresh loophole."""
    rule = _links_rule().lower()
    assert "follows the fact" in rule
    for source in ("seller's notes", "reference page", "research source",
                   "watermark"):
        assert source in rule, f"the links ban no longer covers {source!r}"


def test_a_makers_web_address_is_given_as_the_makers_name():
    """The rule has to say what to write INSTEAD, or a model that reads
    "pyrex.com" off a box either prints it or drops the maker entirely."""
    rule = _links_rule()
    assert "Name the MAKER in words" in rule


def test_the_other_fields_on_the_page_are_covered():
    """condition_description goes to eBay as its own field (ebay_trading
    sends <ConditionDescription>), and the title and subtitle are on the same
    page under the same policy. A ban scoped to the body alone leaves three
    places a link still lands."""
    rule = _links_rule()
    for field in ("title", "subtitle", "condition_description",
                  "item_specifics"):
        assert field in rule, f"the links ban no longer covers {field!r}"


def test_a_refine_cannot_put_a_link_back():
    rule = _refine_rule()
    assert "NO link" in rule
    lowered = rule.lower()
    for form in ("url", "domain", "email address", "phone number",
                 "social handle"):
        assert form in lowered, f"the refine links ban no longer names {form!r}"


def test_a_refine_scrubs_the_links_already_in_the_draft():
    """An imported eBay listing arrives with the old seller's links in it, so
    leaving them alone is not neutral — it republishes the violation."""
    rule = _refine_rule()
    assert "DROP any the draft already carries" in rule
    lowered = rule.lower()
    for field in ("title", "subtitle", "condition_description"):
        assert field in lowered


def test_the_seller_cannot_waive_the_links_ban():
    """The rules beside this one bend to "unless the seller asks" — this one
    must not, because the seller asking is precisely how the violation gets
    published."""
    rule = _refine_rule().lower()
    assert "not the seller's to waive" in rule
    assert "even when the instruction asks for one" in rule


def test_the_rest_of_the_instruction_is_still_honoured():
    """Refusing the link is not licence to refuse the edit: a seller who asks
    for a link and a price change must still get the price change."""
    rule = _refine_rule().lower()
    assert "give the seller everything else they asked for" in rule

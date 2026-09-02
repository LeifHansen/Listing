"""The pre-publish checklist, and the title ceiling it enforces.

Two things are being pinned here.

The first is the 80-character title. eBay rejects a listing whose title runs
one character over, and it rejects it *after* the photos have uploaded — so
the limit can't live only in the editor's input. Every path that builds a
Listing (an AI draft, a refine, a merge, an import, a client that never
enforced it) has to come out at or under the ceiling.

The second is which entries on the checklist actually STOP a publish. The
list deliberately carries advice as well ("no description yet"), and a seller
staring at a listing that won't go live needs the blocking ones separable
from the rest without reading prose — that's what `blocking` is for, and why
`field` names the thing to go fix.
"""
from __future__ import annotations

from backend.models import TITLE_MAX_CHARS, Listing
from backend.services import preflight

ACCOUNT_READY = {"has_fulfillment": True, "has_payment": True,
                 "has_return": True, "has_location": True, "connected": True}


def listing(**fields) -> Listing:
    """A listing eBay would accept, so a test can break exactly one thing."""
    base = {"title": "Nike Air Max 90 Men's 10.5 White Leather Sneakers",
            "condition": "USED_GOOD", "price": 60.0, "quantity": 1,
            "category_id": "15709", "description": "Lightly worn, no flaws.",
            "package_weight_lb": 2.0, "images": ["img_000.jpg"]}
    base.update(fields)
    return Listing(**base)


def targets(issues) -> list[str]:
    return [i["target"] for i in issues]


# ------------------------------------------------------------- title ceiling

def test_a_title_over_the_ceiling_is_cut_to_it():
    """The backstop for every write path that isn't a keystroke in the editor:
    an over-long title becomes a publishable one instead of a failed publish."""
    long_title = "Vintage " * 20
    assert len(long_title) > TITLE_MAX_CHARS
    assert len(Listing(title=long_title).title) == TITLE_MAX_CHARS


def test_a_title_within_the_ceiling_is_left_alone():
    assert Listing(title="Nike Air Max 90").title == "Nike Air Max 90"


def test_surrounding_whitespace_never_eats_into_the_ceiling():
    """A title padded out to 80 by spaces is a 71-character title, and has to
    be free to grow — trimming before measuring is what makes it one."""
    padded = "  " + "x" * 71 + "       "
    assert Listing(title=padded).title == "x" * 71


def test_the_checklist_agrees_with_the_model_about_the_ceiling():
    """Records written before the validator existed still reach the checklist,
    so it keeps its own check — and the two use the same number."""
    over = listing()
    over.title = "x" * (TITLE_MAX_CHARS + 1)   # assignment skips the validator
    issues = preflight.validate(over, "live", **ACCOUNT_READY)
    assert "title" in targets(preflight.errors_only(issues))


def test_a_missing_title_blocks_the_publish():
    issues = preflight.validate(listing(title=""), "live", **ACCOUNT_READY)
    assert "title" in targets(preflight.errors_only(issues))


# --------------------------------------------------- blocking vs. suggestion

def test_a_ready_listing_has_nothing_blocking():
    assert preflight.errors_only(
        preflight.validate(listing(), "live", **ACCOUNT_READY)) == []


def test_a_missing_description_is_advice_not_a_blocker():
    """The one entry a seller must be able to ignore. It rode in the same list
    as the real blockers, which is exactly the confusion `blocking` ends."""
    issues = preflight.validate(listing(description=""), "live", **ACCOUNT_READY)
    described = [i for i in issues if i["target"] == "description"]
    assert described and described[0]["blocking"] is False
    assert preflight.errors_only(issues) == []


def test_every_entry_says_whether_it_blocks_and_what_to_go_fix():
    """The contract the UI reads: no entry may leave a seller guessing which
    of the two it is, or which field it is about."""
    issues = preflight.validate(
        listing(title="", price=None, category_id="", description="",
                package_weight_lb=0.0, images=[]),
        "live", **ACCOUNT_READY)
    assert issues
    for issue in issues:
        assert issue["blocking"] is (issue["level"] != "warn")
        assert issue["field"], f"no field label on {issue['target']}"


def test_the_service_weight_cap_is_a_blocker_like_any_other():
    """Built outside add(), so it's the entry most likely to drift out of the
    shape the UI relies on."""
    heavy = listing(package_weight_lb=1.0)
    issues = preflight.check_weight_vs_services(
        heavy, [{"code": "USPSStandardEnvelope", "name": "Standard Envelope"}])
    assert issues and issues[0]["blocking"] is True and issues[0]["field"]


def test_account_gaps_are_blockers_the_seller_can_be_sent_to():
    """A publish stopped by Settings rather than by the listing still has to
    name where to go."""
    issues = preflight.errors_only(preflight.validate(
        listing(), "live", has_fulfillment=False, has_payment=False,
        has_return=False, has_location=False, connected=True))
    assert {"shipping", "policies", "location"} <= set(targets(issues))
    assert all(i["field"] for i in issues)


def test_a_required_aspect_is_answered_by_any_row_that_holds_a_value():
    """An aspect can own SEVERAL rows — eBay's multi-selects do, and a blank
    one (a cleared field, an identify pass that named the aspect and left it
    empty) can sit in front of the answer. The answer counts wherever it is:
    the browser's copy of this rule stopped at the first row, and told sellers
    a Color reading "Multi-Color" on their screen was a missing required
    field."""
    from backend.models import ItemSpecific

    filled = listing(item_specifics=[
        ItemSpecific(name="Color", value=""),
        ItemSpecific(name="Color", value="Multi-Color"),
    ])
    assert preflight.errors_only(preflight.validate(
        filled, "live", **ACCOUNT_READY, required_aspects=["Color"])) == []


def test_a_required_aspect_holding_only_whitespace_is_still_missing():
    from backend.models import ItemSpecific

    blank = listing(item_specifics=[ItemSpecific(name="Color", value="   ")])
    issues = preflight.errors_only(preflight.validate(
        blank, "live", **ACCOUNT_READY, required_aspects=["Color"]))
    assert targets(issues) == ["specifics"]
    assert "Color" in issues[0]["title"]


def test_the_missing_aspects_are_named_for_the_editor_to_ring():
    """The title lists at most six of them and the card holds forty inputs.
    `fields` is the machine-readable half — what the editor marks."""
    from backend.models import ItemSpecific

    bare = listing(item_specifics=[ItemSpecific(name="Color", value="Red")])
    issues = preflight.errors_only(preflight.validate(
        bare, "live", **ACCOUNT_READY,
        required_aspects=["Color", "Brand", "Size Type"]))
    assert [i["fields"] for i in issues] == [["Brand", "Size Type"]]


def test_draft_mode_only_checks_what_a_draft_needs():
    """Saving a draft isn't publishing: price, category and weight aren't
    blocking anything yet, so they must not be reported as if they were."""
    issues = preflight.validate(
        listing(price=None, category_id="", package_weight_lb=0.0),
        "draft", **ACCOUNT_READY)
    assert preflight.errors_only(issues) == []

"""The save path keeps the fields only the server may write.

`_restore_server_state` already refused a stale client's `marketplaces` map
and its `ebay_listing_id`, for a documented reason: honoring a stale copy
erases live listing ids, and the next publish then creates a duplicate live
listing instead of revising.

`source` had exactly that power and no protection. `source="ebay"` is the
stamp every listing this app publishes carries, and it is what routes the next
edit down the revise path. A save that blanked it — a second tab, the image
editor's auto-save, any client whose copy predates the publish — made a live
record look brand new, and the next publish took the create branch.

This is the wiring test. The field list and its semantics are covered in
test_ebay_server_owned.py, which needs no app import.
"""
from __future__ import annotations

import pytest

# Importing backend.main pulls the whole app in. `checks` has neither of these,
# so it skips the file; the smoke job's "API tests" step is where it runs, and
# that step fails on a skip so this can never quietly stop running.
pytest.importorskip("fastapi")
pytest.importorskip("anthropic")
pytest.importorskip("PIL")

from backend import main  # noqa: E402
from backend.models import Listing  # noqa: E402

LIVE_RECORD = {
    "listing": {
        "source": "ebay",
        "ebay_listing_id": "110000000001",
        "ebay_account": "seller",
        "view_url": "https://www.ebay.com/itm/110000000001",
        "marketplaces": {},
    },
    "status": "published",
}


def test_a_save_cannot_blank_the_source_that_routes_the_next_publish():
    """The defect. Fails against the old code: `source` came back "", and the
    next publish would have created a second live listing."""
    stale = Listing(title="Bowl", source="", ebay_listing_id="")
    main._restore_server_state("s1", stale, prev_rec=LIVE_RECORD)
    assert stale.source == "ebay"


def test_a_save_still_keeps_the_state_it_already_protected():
    """Guards the pre-existing behaviour the new call sits next to."""
    stale = Listing(title="Bowl", source="", ebay_listing_id="")
    main._restore_server_state("s1", stale, prev_rec=LIVE_RECORD)
    assert stale.ebay_listing_id == "110000000001"


def test_a_save_keeps_the_account_the_listing_lives_on():
    stale = Listing(title="Bowl", ebay_account="")
    main._restore_server_state("s1", stale, prev_rec=LIVE_RECORD)
    assert stale.ebay_account == "seller"


def test_a_brand_new_listing_is_left_alone():
    """Nothing stored: the client's copy is all there is, and a save of a
    never-published listing must not acquire an identity from an empty
    record."""
    fresh = Listing(title="Bowl")
    main._restore_server_state("s1", fresh, prev_rec={})
    assert fresh.source == "" and fresh.ebay_listing_id == ""

"""P1-09's rule, applied to the two marketplaces it missed.

"Published" is a claim about the listing being live on the marketplace. The
eBay provider was fixed for this: a LIVE publish with no connected account
answers ok=False, says eBay isn't connected, and adds "Nothing was listed."

Etsy and Depop kept answering:

    PublishOutcome(ok=True, dry_run=True,
                   message="Etsy dry run — connect Etsy in Settings ...")

for a live publish. The message is honest on its own, but `ok` is what the
bulk cards and the multi-marketplace fold read, and a seller who is told a
publish succeeded closes the app.

eBay's guard is keyed on `config.EBAY_ENV == "production"` because eBay HAS a
sandbox, so a dry run is a real development tool there. Etsy and Depop have no
sandbox — the Etsy provider's own comment says so — which means there is no
environment where a live dry run is a success. The payload preview still rides
along in `raw` for whoever is developing against it; what changes is the
answer to "did you list it".

A DRAFT publish is untouched: nothing is claimed to be live, and Depop's own
draft branch already explains that it has no drafts.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.marketplaces.base import PublishContext  # noqa: E402
from backend.models import Listing  # noqa: E402


def _ctx(mode: str) -> PublishContext:
    return PublishContext(
        session_id="s1",
        listing=Listing(title="Vintage Levi's 501", description="Nice.",
                        price=45.0, quantity=1),
        mode=mode, base_url="https://app.test", uid="u1", prev_record={})


def _providers():
    from backend.marketplaces.depop_provider import DepopProvider
    from backend.marketplaces.etsy_provider import EtsyProvider
    return {"etsy": EtsyProvider(), "depop": DepopProvider()}


@pytest.mark.parametrize("key", ["etsy", "depop"])
def test_a_live_publish_with_no_account_is_not_a_success(key):
    outcome = _providers()[key].publish(_ctx("live"), None)
    assert outcome.ok is False, (
        f"{key} reported a successful live publish with nothing connected")
    assert "nothing was listed" in (outcome.message or "").lower()
    assert "connect" in (outcome.message or "").lower()


@pytest.mark.parametrize("key", ["etsy", "depop"])
def test_it_says_which_marketplace_is_not_connected(key):
    outcome = _providers()[key].publish(_ctx("live"), None)
    titles = " ".join(str(i.get("title", "")) for i in outcome.issues).lower()
    assert key in titles, f"the issue does not name {key}: {outcome.issues}"
    assert all(i.get("level") == "error" for i in outcome.issues)


@pytest.mark.parametrize("key", ["etsy", "depop"])
def test_it_does_not_move_the_listing_s_lifecycle_state(key):
    """A publish that did not happen must not record one."""
    assert _providers()[key].publish(_ctx("live"), None).status == ""


def test_an_etsy_draft_with_no_account_still_previews():
    """Untouched: a draft claims nothing about being live, and the payload IS
    the test for a marketplace with no sandbox."""
    outcome = _providers()["etsy"].publish(_ctx("draft"), None)
    assert outcome.ok is True and outcome.dry_run is True
    assert "etsy_payload" in outcome.raw


def test_a_depop_draft_still_explains_depop_has_no_drafts():
    outcome = _providers()["depop"].publish(_ctx("draft"), None)
    assert outcome.ok is True
    assert "no drafts" in (outcome.message or "").lower()

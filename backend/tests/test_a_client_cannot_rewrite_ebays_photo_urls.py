"""eBay's own photo URLs are something the server observed, not something a
client asserts.

`image_urls` holds the EPS URLs of an imported listing's photos — written by
the store sync from eBay's answer, never by anything the seller does. Every
save round-trips the whole listing, so a browser copy that predates a sync,
or a second tab, sends its own version of that list back; whatever it sends
won.

Two things that costs:

  * the imported-listing revise reads `listing.image_urls` as its fallback
    ("untouched photos → reuse the live EPS URLs and skip the re-upload
    churn"), and a relist REQUIRES them — "this listing has no photos left to
    relist with" is what a seller gets when a stale tab sent an empty list for
    a listing eBay is still hosting twelve photos for;
  * it is the field the Etsy publish used to fetch server-side, which is how
    a request from inside the app could be aimed anywhere. That hole is closed
    at the fetch (image_import.fetch_ebay_image), and this is not a substitute
    for it: `restore_server_fields` only overrides a stored value that EXISTS,
    so a brand-new draft still carries whatever the client sent. Defence in
    depth, named as such.

Same rule and the same mechanism as `remote_shadow`, `sku` and
`ebay_account_id`: the stored value wins, a blank one leaves the client's
alone so a first write can still stamp it.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from backend.marketplaces import state  # noqa: E402
from backend.models import Listing  # noqa: E402

EBAY = ["https://i.ebayimg.com/images/g/aaa/s-l1600.jpg",
        "https://i.ebayimg.com/images/g/bbb/s-l1600.jpg"]


def test_ebays_photo_urls_are_server_owned():
    assert "image_urls" in state.SERVER_OWNED_FIELDS


def test_a_stale_tab_cannot_blank_them():
    """The relist path raises "no photos left to relist with" on an empty
    list, for a listing eBay is still hosting the photos of."""
    listing = Listing(title="A jacket", image_urls=[])
    state.restore_server_fields(listing, {"image_urls": EBAY})
    assert listing.image_urls == EBAY


def test_a_client_cannot_substitute_its_own():
    listing = Listing(title="A jacket",
                      image_urls=["http://169.254.169.254/latest/meta-data/"])
    changed = state.restore_server_fields(listing, {"image_urls": EBAY})
    assert listing.image_urls == EBAY
    assert "image_urls" in changed, "the override must be reported for the log"


def test_a_new_draft_still_stamps_its_own():
    """Nothing stored yet — a first publish has to be able to set them."""
    listing = Listing(title="A jacket", image_urls=EBAY)
    state.restore_server_fields(listing, {})
    assert listing.image_urls == EBAY

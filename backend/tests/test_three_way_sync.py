"""A revise must carry the seller's edits, and nothing else.

The app called its eBay integration bidirectional. It was a mirror plus a
broad replace:

  - inbound, a re-sync refreshed only price, quantity, counters and photos.
    Title, description, category, condition and specifics were kept LOCAL
    unless blank, so a change made in Seller Hub never arrived;
  - outbound, every revise sent the whole content payload regardless of what
    the seller had touched.

Put together, a seller who fixed a title in Seller Hub and later changed only
the price in this app pushed the STALE title, description, category and
specifics back over their newer work — reported as a successful update.

There was nothing to detect that with: no snapshot of what eBay last said, no
record of which fields the seller had edited, and so no way to tell "the
seller changed this" from "the app is holding an old copy of it".

The shadow is that missing piece. It is what eBay last told us, stored
alongside the record, and it turns one unanswerable question into two
answerable ones: did the seller change this field since the shadow, and did
eBay?

  seller changed, eBay did not  -> send it
  eBay changed, seller did not  -> take it
  both changed                  -> a conflict; send nothing, ask
  neither                       -> leave it alone
"""
from __future__ import annotations

from backend.models import Listing
from backend.services import sync_merge


def _shadow(**over) -> dict:
    base = {"title": "Blue lamp", "description": "A lamp.", "price": 25.0,
            "quantity": 3, "category_id": "112581",
            "condition": "USED_EXCELLENT"}
    base.update(over)
    return base


def _local(shadow: dict, **over) -> Listing:
    data = dict(shadow)
    data.update(over)
    return Listing(**data)


# ------------------------------------------------------------ the merge

def test_a_remote_only_edit_flows_into_the_app():
    """The inbound half that never worked: a title fixed in Seller Hub was
    dropped on every sync because the local copy was not blank."""
    shadow = _shadow()
    local = _local(shadow)                       # seller changed nothing here
    remote = _shadow(title="Blue ceramic lamp")  # they changed it on eBay

    merged = sync_merge.three_way(local, shadow, remote)

    assert merged.listing.title == "Blue ceramic lamp"
    assert not merged.conflicts


def test_a_local_only_edit_survives_a_sync():
    """The other direction, which must not regress: a background sync cannot
    revert what the seller just typed."""
    shadow = _shadow()
    local = _local(shadow, title="Blue lamp, rewired")
    remote = _shadow()

    merged = sync_merge.three_way(local, shadow, remote,
                                     dirty={"title"})

    assert merged.listing.title == "Blue lamp, rewired"
    assert not merged.conflicts


def test_both_sides_editing_the_same_field_is_a_conflict():
    """Neither value may be silently chosen. Picking the local one is what
    overwrote sellers' Seller Hub work; picking the remote one throws away
    what they just typed here."""
    shadow = _shadow()
    local = _local(shadow, title="Blue lamp, rewired")
    remote = _shadow(title="Blue ceramic lamp")

    merged = sync_merge.three_way(local, shadow, remote, dirty={"title"})

    assert "title" in merged.conflicts
    assert merged.conflicts["title"] == {"local": "Blue lamp, rewired",
                                         "remote": "Blue ceramic lamp"}
    # The local value is held, unchanged, until the seller decides.
    assert merged.listing.title == "Blue lamp, rewired"


def test_the_same_edit_on_both_sides_is_not_a_conflict():
    """The seller made the same change in both places, or a previous sync
    already carried it across. Nothing to ask about."""
    shadow = _shadow()
    local = _local(shadow, title="Blue ceramic lamp")
    remote = _shadow(title="Blue ceramic lamp")

    merged = sync_merge.three_way(local, shadow, remote, dirty={"title"})

    assert not merged.conflicts
    assert merged.listing.title == "Blue ceramic lamp"


def test_untracked_fields_keep_the_remote_value():
    """Counters and sale state are eBay's to report, never ours to push."""
    shadow = _shadow()
    local = _local(shadow)
    remote = _shadow(watch_count=17, sold_quantity=2)

    merged = sync_merge.three_way(local, shadow, remote)

    assert merged.listing.watch_count == 17
    assert merged.listing.sold_quantity == 2


def test_a_first_sync_with_no_shadow_keeps_the_local_copy():
    """The conservative answer, and the one that matters on deploy day.

    Every record already in the database has no shadow. A rule of "no base
    means eBay wins" would therefore overwrite every seller's local work at
    once, on the first sync after this ships. That first sync exists to
    ESTABLISH the base; reconciling starts on the second.
    """
    remote = _shadow(title="Blue ceramic lamp")
    local = _local(_shadow(), title="Blue lamp, rewired")

    merged = sync_merge.three_way(local, None, remote)

    assert merged.listing.title == "Blue lamp, rewired"
    assert not merged.conflicts


def test_ebays_own_counters_arrive_even_without_a_shadow():
    """Holding back a title is right; holding back the watch count is not —
    those are eBay's facts and the app only ever displays them."""
    merged = sync_merge.three_way(_local(_shadow()), None,
                                  _shadow(watch_count=17))
    assert merged.listing.watch_count == 17


# --------------------------------------------------- the outbound payload

def _revise_body(listing: Listing) -> str:
    from backend.services import ebay_trading

    return ebay_trading.build_revise_item(
        listing, "110000000001", image_urls=listing.image_urls or None)[1]


def test_a_price_only_revise_carries_nothing_else():
    """The data loss, stated as a payload. Every one of these fields, sent
    from a stale local copy, lands on top of whatever eBay has now."""
    listing = Listing(title="Blue lamp", description="A lamp.", price=30.0,
                      quantity=3, category_id="112581",
                      condition="USED_EXCELLENT",
                      listing_format="FIXED_PRICE").mark_dirty("price")

    body = _revise_body(listing)

    assert "<StartPrice>30.00</StartPrice>" in body
    for absent in ("<Title>", "<Description>", "<PrimaryCategory>",
                   "<ConditionID>", "<ItemSpecifics>", "<PictureDetails>",
                   "<Quantity>"):
        assert absent not in body, absent


def test_a_title_only_revise_carries_the_title():
    listing = Listing(title="Blue ceramic lamp", description="A lamp.",
                      price=25.0, listing_format="FIXED_PRICE"
                      ).mark_dirty("title")

    body = _revise_body(listing)

    assert "<Title>Blue ceramic lamp</Title>" in body
    assert "<Description>" not in body
    assert "<StartPrice>" not in body


def test_a_revise_with_nothing_dirty_sends_no_content():
    """A sync-triggered save that changed nothing must not become a write."""
    listing = Listing(title="Blue lamp", description="A lamp.", price=25.0,
                      listing_format="FIXED_PRICE")

    body = _revise_body(listing)

    for absent in ("<Title>", "<Description>", "<StartPrice>", "<Quantity>"):
        assert absent not in body, absent


def test_a_create_still_carries_everything():
    """Minimal payloads are a REVISE rule. A create has no remote state to
    overwrite, and omitting fields there would publish a blank listing."""
    from backend.services import ebay_trading

    listing = Listing(title="Blue lamp", description="A lamp.", price=25.0,
                      quantity=3, category_id="112581",
                      condition="USED_EXCELLENT", package_weight_lb=1.0,
                      listing_format="FIXED_PRICE")
    body = ebay_trading.build_add_item(
        listing, ["https://example.test/1.jpg"], {}, "97201",
        idempotency_key="")[1]

    assert "<Title>Blue lamp</Title>" in body
    assert "<Description>" in body
    assert "<PrimaryCategory>" in body
    assert "<PictureDetails>" in body


def test_photos_go_only_when_the_seller_changed_them():
    """Re-sending PictureDetails replaces the listing's photo set. Doing that
    on an unrelated edit discards anything added on eBay since."""
    listing = Listing(title="Blue lamp", price=25.0,
                      image_urls=["https://i.ebayimg.com/1.jpg"],
                      listing_format="FIXED_PRICE").mark_dirty("price")
    assert "<PictureDetails>" not in _revise_body(listing)

    listing.mark_dirty("image_urls")
    assert "<PictureDetails>" in _revise_body(listing)


# ------------------------------------------------- through the import path

def test_the_shadow_is_recorded_and_then_used():
    """End to end, and the reason the no-shadow rule is conservative.

    Sync once: nothing is reconciled, but the base is written down. Sync
    again with a title changed on eBay: it now arrives, because there is
    finally something to compare against.
    """
    from backend.services import listing_sync

    first = listing_sync._reconcile(
        prior=None,                              # no prior record at all
        merged=_shadow(),
        fresh=_shadow())
    assert first["remote_shadow"]["title"] == "Blue lamp"

    # Second sync: the seller renamed it in Seller Hub, and locally nothing
    # was touched since the shadow.
    second = listing_sync._reconcile(
        prior={"remote_shadow": first["remote_shadow"], **_shadow()},
        merged=_shadow(),                        # local copy, untouched
        fresh=_shadow(title="Blue ceramic lamp"))

    assert second["title"] == "Blue ceramic lamp"
    assert not second["conflicts"]
    assert second["remote_shadow"]["title"] == "Blue ceramic lamp"


def test_a_both_sided_edit_surfaces_as_a_conflict_on_the_record():
    """And the conflicted field is kept out of the next revise, so neither
    side is silently overwritten."""
    from backend.services import listing_sync

    shadow = _shadow()
    prior = {"remote_shadow": shadow, **shadow}
    local_edit = _shadow(title="Blue lamp, rewired")
    local_edit["dirty_fields"] = ["title"]

    out = listing_sync._reconcile(
        prior=prior, merged=local_edit,
        fresh=_shadow(title="Blue ceramic lamp"))

    assert "title" in out["conflicts"]
    assert "title" not in out["dirty_fields"]
    assert out["title"] == "Blue lamp, rewired"

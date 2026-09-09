"""The label readers filter on the owner in the query -- the far end of the
trust the routes rest on.

test_every_scoped_route_checks_the_owner sees the refund route as guarded
because it threads the caller's uid into db.get_shipping_label_by_shipment.
That is only a check if the reader honours it; and the purchase route's
`order_id` is not a scanned name at all, so labels_for_order is the ONLY
thing keeping one seller's labels out of another's dialog.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")


def test_readers_and_writers_are_scoped_to_the_owner(dbmod):
    mine = dbmod.create_shipping_label("u1", order_id="o1", shipment_id="shp_1",
                                       rate_id="r1", status="bought",
                                       tracking_number="9400")
    assert mine["order_id"] == "o1" and mine["status"] == "bought"

    assert dbmod.get_shipping_label_by_shipment("u1", "shp_1")["label_id"] == mine["label_id"]
    assert dbmod.get_shipping_label_by_shipment("u2", "shp_1") is None

    assert [r["label_id"] for r in dbmod.labels_for_order("u1", "o1")] == [mine["label_id"]]
    assert dbmod.labels_for_order("u2", "o1") == []
    assert dbmod.labels_for_orders("u2", ["o1"]) == {"o1": []}
    assert dbmod.labels_for_orders("u1", ["o1", "o2"])["o1"][0]["label_id"] == mine["label_id"]

    assert dbmod.update_shipping_label("u2", mine["label_id"], status="refund_requested") is False
    assert dbmod.get_shipping_label_by_shipment("u1", "shp_1")["status"] == "bought"
    assert dbmod.update_shipping_label("u1", mine["label_id"], status="refund_requested") is True

    assert dbmod.mark_label_ebay("u2", "o1", ok=True) == 0
    assert dbmod.get_shipping_label_by_shipment("u1", "shp_1")["ebay_marked"] is False


def test_labels_follow_the_listing_they_shipped(dbmod):
    dbmod.create_shipping_label("u1", order_id="o1", listing_record_id="rec-1",
                                shipment_id="shp_1", rate_id="r1", status="bought")
    assert [r["order_id"] for r in dbmod.labels_for_listing("u1", "rec-1")] == ["o1"]
    assert dbmod.labels_for_listing("u2", "rec-1") == []

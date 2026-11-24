"""An in-memory eBay for sale events, standing in for services/ebay_markdowns'
HTTP calls (and nothing else: the pure helpers are the real ones).

Holds promotions as eBay would return them, applies eBay's one-sale-per-
listing rule, and can be told which listings to refuse. Patched onto
services.sale_events, which looks the module up at call time."""
from __future__ import annotations

import copy
import itertools
from datetime import datetime, timezone

from backend.services import ebay_markdowns as real


class FakeMarkdowns:
    def __init__(self):
        self.promos: dict[str, dict] = {}
        self.rule_sets: dict[str, list[str]] = {}
        self.refuse: dict[str, str] = {}          # listing id -> eBay's reason
        self.calls: list[tuple] = []
        self.scope_error = False
        self.down = False
        self._ids = itertools.count(9001)

    def __getattr__(self, name):                  # the pure half is real
        return getattr(real, name)

    # -- what a test sets up -------------------------------------------------
    def seed(self, pid: str, *, name="Seller Hub sale", status="RUNNING",
             percent="20", listing_ids=("1",), tiers=None, start=None, end=None,
             **extra) -> str:
        tiers = tiers if tiers is not None else [{
            "discountBenefit": {"percentageOffItem": str(percent)},
            "inventoryCriterion": {"inventoryCriterionType": "INVENTORY_BY_VALUE",
                                   "listingIds": list(listing_ids)},
            "ruleOrder": 1}]
        self.promos[pid] = {
            "promotionId": pid, "name": name, "promotionStatus": status,
            "startDate": start or "2026-11-01T00:00:00.000Z",
            "endDate": end or "2099-12-01T00:00:00.000Z",
            "marketplaceId": "EBAY_US", "priority": "PRIORITY_1",
            "selectedInventoryDiscounts": tiers, **extra}
        return pid

    def ids_in(self, pid: str) -> list[str]:
        return real._ids_in(self.promos[pid])

    # -- eBay ----------------------------------------------------------------
    def _gate(self):
        if self.scope_error:
            raise real.ScopeError()
        if self.down:
            raise RuntimeError("eBay is down")

    def _check_ids(self, ids, pid=None):
        named = [i for i in ids if i in self.refuse]
        if named:
            raise real.MarkdownRefused(self.refuse[named[0]], 38204, named)
        busy = [i for i in ids for other, p in self.promos.items()
                if other != pid and p["promotionStatus"] in ("RUNNING", "SCHEDULED")
                and i in real._ids_in(p)]
        if busy:
            raise real.MarkdownRefused(
                f"Listing {busy[0]} is already in another promotion.", 38210, busy)

    def list_markdowns(self, creds, client=None):
        self.calls.append(("list",))
        self._gate()
        return [{"promotionId": p["promotionId"], "name": p["name"],
                 "promotionStatus": p["promotionStatus"],
                 "promotionType": "MARKDOWN_SALE"} for p in self.promos.values()]

    def get_markdown(self, creds, pid, client=None):
        self.calls.append(("get", pid))
        self._gate()
        if pid not in self.promos:
            raise real.NotFound()
        return copy.deepcopy(self.promos[pid])

    def listing_set(self, creds, pid, client=None):
        self.calls.append(("listing_set", pid))
        self._gate()
        return list(self.rule_sets.get(pid, []))

    def create(self, creds, body, client=None):
        self._gate()
        ids = real._ids_in(body)
        self.calls.append(("create", ids))
        self._check_ids(ids)
        pid = str(next(self._ids))
        start = datetime.fromisoformat(body["startDate"].replace("Z", "+00:00"))
        status = "SCHEDULED" if start > datetime.now(timezone.utc) else "RUNNING"
        self.promos[pid] = {**copy.deepcopy(body), "promotionId": pid,
                            "promotionStatus": status}
        return pid

    def update(self, creds, pid, body, client=None):
        self._gate()
        ids = real._ids_in(body)
        self.calls.append(("update", pid, ids))
        if pid not in self.promos:
            raise real.NotFound()
        self._check_ids(ids, pid)
        for key in real._RESPONSE_ONLY:
            assert key not in body, f"a PUT sent back the response-only {key}"
        kept = self.promos[pid]
        self.promos[pid] = {**copy.deepcopy(body), "promotionId": pid,
                            "promotionStatus": body.get("promotionStatus") or kept["promotionStatus"]}
        end = datetime.fromisoformat(self.promos[pid]["endDate"].replace("Z", "+00:00"))
        if end <= datetime.now(timezone.utc).replace(microsecond=0) + real.END_BUFFER:
            self.promos[pid]["promotionStatus"] = "RUNNING"

    def delete(self, creds, pid, client=None):
        self.calls.append(("delete", pid))
        self._gate()
        if pid not in self.promos:
            raise real.NotFound()
        del self.promos[pid]

    def writes(self):
        return [c for c in self.calls if c[0] in ("create", "update", "delete")]

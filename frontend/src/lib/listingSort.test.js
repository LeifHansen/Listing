/* The order the seller picks, and the two rules every order keeps: a listing
 * that cannot answer the question goes last whichever way the sort runs, and
 * ties fall back to the default's own tie-break so the grid does not shuffle
 * between renders. */
import { describe, expect, it } from "vitest";
import {
  DEFAULT_SORT, SORTS, normalizeSort, sortListings, sortMeta, watcherCount,
} from "./listingSort.js";
import { orderListings } from "./listingsView.js";

const rec = (id, listing = {}, rest = {}) => ({
  id, status: "published",
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  ...rest,
  listing: { title: `Item ${id}`, price: 10, ...listing },
});
const ids = (list) => list.map((i) => i.id);

describe("which sort is on", () => {
  it("falls back to the default for anything it does not know", () => {
    expect(normalizeSort("by-vibes")).toBe(DEFAULT_SORT);
    expect(normalizeSort(undefined)).toBe(DEFAULT_SORT);
    expect(normalizeSort("price-asc")).toBe("price-asc");
  });

  it("offers every sort it can apply", () => {
    for (const s of SORTS) expect(normalizeSort(s.id)).toBe(s.id);
  });
});

describe("Recommended", () => {
  it("is exactly the grid's old order", () => {
    const items = [
      rec("old", {}, { updated_at: "2026-01-01T00:00:00Z" }),
      rec("sold", {}, { status: "sold", updated_at: "2026-03-01T00:00:00Z" }),
      rec("new", {}, { updated_at: "2026-02-01T00:00:00Z" }),
      rec("bid", {}, { updated_at: "2025-01-01T00:00:00Z" }),
    ];
    const metrics = { bid: { bids: 2 } };
    expect(ids(sortListings(items, "recommended", metrics)))
      .toEqual(ids(orderListings(items, metrics)));
    expect(ids(sortListings(items, "recommended", metrics))[0]).toBe("bid");
  });
});

describe("by listing date", () => {
  const items = [
    rec("a", { ebay_start_time: "2026-03-01T00:00:00Z" }, { created_at: "2026-09-01T00:00:00Z" }),
    rec("b", {}, { created_at: "2026-05-01T00:00:00Z" }),
    rec("c", { ebay_start_time: "2026-01-15T00:00:00Z" }),
  ];

  it("reads eBay's start time before the import date", () => {
    expect(ids(sortListings(items, "listed-desc"))).toEqual(["b", "a", "c"]);
    expect(ids(sortListings(items, "listed-asc"))).toEqual(["c", "a", "b"]);
  });

  it("says the date on the card it is sorting by", () => {
    expect(sortMeta(items[0], "listed-desc")).toMatch(/^Listed /);
    expect(sortMeta(items[0], "price-asc")).toBe("");
  });
});

describe("by price", () => {
  const items = [
    rec("mid", { price: 20 }),
    rec("none", { price: null }),
    rec("high", { price: 90 }),
    rec("low", { price: 5 }),
  ];

  it("puts an unpriced listing last in BOTH directions", () => {
    expect(ids(sortListings(items, "price-asc"))).toEqual(["low", "mid", "high", "none"]);
    expect(ids(sortListings(items, "price-desc"))).toEqual(["high", "mid", "low", "none"]);
  });

  it("reads an auction's opening bid, and a sale at what it went for", () => {
    const auction = rec("auc", { listing_format: "AUCTION", price: null, auction_start_price: 50 });
    const sold = rec("sold", { price: 100, sold_price: 1 }, { status: "sold" });
    expect(ids(sortListings([auction, sold, items[0]], "price-asc")))
      .toEqual(["sold", "mid", "auc"]);
  });
});

describe("by title", () => {
  it("is alphabetical the way a person reads it", () => {
    const items = [rec("b", { title: "item 10" }), rec("a", { title: "Item 9" }),
      rec("c", { title: "apple" })];
    expect(ids(sortListings(items, "title-asc"))).toEqual(["c", "a", "b"]);
    expect(ids(sortListings(items, "title-desc"))).toEqual(["b", "a", "c"]);
  });
});

describe("by buyer interest", () => {
  it("ranks watchers, with an unasked listing last", () => {
    const items = [rec("one"), rec("unknown"), rec("five")];
    const metrics = { one: { watchers: 1 }, five: { watchers: 5 } };
    expect(ids(sortListings(items, "watchers-desc", metrics))).toEqual(["five", "one", "unknown"]);
  });

  it("falls back to the store sync's count on an imported listing", () => {
    expect(watcherCount(rec("x", { source: "ebay", watch_count: 4 }), null)).toBe(4);
    expect(watcherCount(rec("x", { watch_count: 4 }), null)).toBe(null);
  });

  it("puts the auction closing first under Ending soonest", () => {
    const items = [rec("later"), rec("bin"), rec("soon")];
    const metrics = { later: { ends_at: "2026-12-05T00:00:00Z" },
      soon: { ends_at: "2026-12-01T00:00:00Z" } };
    expect(ids(sortListings(items, "ending-asc", metrics))).toEqual(["soon", "later", "bin"]);
  });
});

describe("ties", () => {
  it("keep the most recently changed first, so the grid does not shuffle", () => {
    const items = [
      rec("older", { price: 10 }, { updated_at: "2026-01-01T00:00:00Z" }),
      rec("newer", { price: 10 }, { updated_at: "2026-02-01T00:00:00Z" }),
    ];
    expect(ids(sortListings(items, "price-asc"))).toEqual(["newer", "older"]);
    expect(ids(sortListings(items, "price-desc"))).toEqual(["newer", "older"]);
  });

  it("never sorts the store's own array", () => {
    const items = [rec("b", { price: 2 }), rec("a", { price: 1 })];
    sortListings(items, "price-asc");
    expect(ids(items)).toEqual(["b", "a"]);
  });
});

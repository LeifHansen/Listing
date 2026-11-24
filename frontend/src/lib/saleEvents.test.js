/* The client's half of sale events: states by the dates, groups in reading
 * order, and which card is in which sale. */
import { describe, expect, it } from "vitest";
import {
  effectiveState, groupSaleEvents, saleIndex, salePriceFor, saleProgress, saleWhen,
} from "./saleEvents.js";

const NOW = Date.parse("2026-11-24T12:00:00Z");
const at = (days) => new Date(NOW + days * 86400000).toISOString();
const ev = (id, start, end, rest = {}) => ({ id, status: "SCHEDULED", start_at: at(start),
  end_at: at(end), item_ids: [], ...rest });

describe("a sale's state", () => {
  it("is read off the dates, whatever the stored status says", () => {
    expect(effectiveState(ev("a", -1, 2), NOW)).toBe("running");
    expect(effectiveState(ev("a", 1, 2), NOW)).toBe("scheduled");
    expect(effectiveState(ev("a", -3, -1, { status: "RUNNING" }), NOW)).toBe("ended");
    expect(effectiveState(ev("a", -1, 2, { status: "ENDED" }), NOW)).toBe("ended");
    expect(effectiveState(ev("a", 1, 2, { status: "DRAFT" }), NOW)).toBe("draft");
  });

  it("is grouped the way a seller reads the list", () => {
    const g = groupSaleEvents([
      ev("late", -1, 9), ev("soon", -1, 2), ev("next", 3, 9), ev("first", 1, 9),
      ev("old", -9, -5), ev("recent", -4, -1),
    ], NOW);
    expect(g.running.map((e) => e.id)).toEqual(["soon", "late"]);
    expect(g.scheduled.map((e) => e.id)).toEqual(["first", "next"]);
    expect(g.ended.map((e) => e.id)).toEqual(["recent", "old"]);
  });

  it("says when, in a few words", () => {
    expect(saleWhen(ev("a", -1, 3), NOW)).toBe("ends in 3d");
    expect(saleWhen(ev("a", -1, 0.5), NOW)).toBe("ends today");
    expect(saleWhen(ev("a", 2, 5), NOW)).toMatch(/^starts /);
    expect(saleProgress(ev("a", -1, 1), NOW)).toBeCloseTo(0.5);
  });
});

describe("which listing is in which sale", () => {
  const items = [
    { id: "rec-a", listing: { ebay_listing_id: "111" } },
    { id: "rec-b", listing: { ebay_listing_id: "222" } },
    { id: "draft", listing: {} },
  ];

  it("joins each sale's eBay item ids to the cards", () => {
    const idx = saleIndex([ev("s1", -1, 2, { item_ids: ["111"] })], items, NOW);
    expect(idx.get("rec-a").id).toBe("s1");
    expect(idx.has("rec-b")).toBe(false);
    expect(idx.has("draft")).toBe(false);
  });

  it("holds nothing for a sale that has ended", () => {
    const idx = saleIndex([ev("s1", -5, -1, { item_ids: ["111"] })], items, NOW);
    expect(idx.size).toBe(0);
  });
});

describe("the sale price", () => {
  it("is the price less the percentage, to the cent", () => {
    expect(salePriceFor(40, 20)).toBe(32);
    expect(salePriceFor(19.99, 15)).toBe(16.99);
    expect(salePriceFor(null, 20)).toBe(null);
  });
});

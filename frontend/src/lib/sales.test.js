/* The money a sold listing actually made.
 *
 * The defect these pin down: a listing that sold through an accepted offer
 * was reported at its ASKING price everywhere in the app — the card, the
 * profit line, and now the dashboard's sold total. `price` never moves when
 * an offer is accepted, so anything reading it is reading what the seller
 * hoped for, not what the buyer paid.
 */
import { describe, expect, it } from "vitest";
import { salePrice, saleDiscount, salesSummary, soldRange } from "./sales";

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.parse("2026-08-20T12:00:00.000Z");

function sold({ id = "a", price = 89.99, sold_price = null, ago = 1,
                purchase_price = null, sold_quantity = 1, sold_at } = {}) {
  return {
    id,
    status: "sold",
    listing: {
      price, sold_price, purchase_price, sold_quantity,
      sold_at: sold_at !== undefined ? sold_at
        : new Date(NOW - ago * DAY).toISOString(),
    },
  };
}

describe("what one unit went for", () => {
  it("reports the accepted offer, not the asking price", () => {
    expect(salePrice({ price: 89.99, sold_price: 76.5 })).toBe(76.5);
  });

  it("falls back to the asking price when eBay reported no amount", () => {
    expect(salePrice({ price: 89.99, sold_price: null })).toBe(89.99);
  });

  it("keeps a free item at zero rather than reading it as missing", () => {
    expect(salePrice({ price: 12, sold_price: 0 })).toBe(0);
  });

  it("measures the discount off the asking price", () => {
    const d = saleDiscount({ price: 89.99, sold_price: 76.5 });
    expect(d.amount).toBeCloseTo(13.49);
    expect(d.percent).toBe(15);
  });

  it("has no discount to show when it sold at the asking price", () => {
    expect(saleDiscount({ price: 89.99, sold_price: 89.99 })).toBeNull();
    expect(saleDiscount({ price: 89.99, sold_price: null })).toBeNull();
  });
});

describe("the dashboard's sold total", () => {
  it("defaults to a week and adds up what sold inside it", () => {
    const s = salesSummary(
      [sold({ id: "a", sold_price: 76.5, ago: 1 }),
       sold({ id: "b", sold_price: 20, ago: 6 }),
       sold({ id: "c", sold_price: 999, ago: 30 })],
      "week", NOW);
    expect(s.count).toBe(2);
    expect(s.total).toBeCloseTo(96.5);
    expect(s.range.days).toBe(7);
  });

  it("widens with the range", () => {
    const items = [sold({ id: "a", sold_price: 10, ago: 0.5 }),
                   sold({ id: "b", sold_price: 20, ago: 5 }),
                   sold({ id: "c", sold_price: 40, ago: 45 })];
    expect(salesSummary(items, "day", NOW).total).toBe(10);
    expect(salesSummary(items, "week", NOW).total).toBe(30);
    expect(salesSummary(items, "90d", NOW).total).toBe(70);
  });

  it("counts what the buyer paid, never the asking price", () => {
    const s = salesSummary([sold({ sold_price: 76.5, price: 89.99 })], "week", NOW);
    expect(s.total).toBe(76.5);
    expect(s.approx).toBe(0);
  });

  it("flags a total that had to lean on asking prices", () => {
    const s = salesSummary([sold({ sold_price: null, price: 89.99 })], "week", NOW);
    expect(s.total).toBe(89.99);
    expect(s.approx).toBe(1);
  });

  it("multiplies out a multi-quantity sale", () => {
    const s = salesSummary(
      [sold({ sold_price: 12, sold_quantity: 3 })], "week", NOW);
    expect(s.total).toBe(36);
    expect(s.units).toBe(3);
  });

  it("subtracts the cost basis only where one was recorded", () => {
    const s = salesSummary(
      [sold({ id: "a", sold_price: 76.5, purchase_price: 4 }),
       sold({ id: "b", sold_price: 20 })], "week", NOW);
    expect(s.profit).toBeCloseTo(72.5);
    expect(s.withCost).toBe(1);
  });

  it("reports no profit line at all when nothing has a cost basis", () => {
    expect(salesSummary([sold({ sold_price: 20 })], "week", NOW).profit).toBeNull();
  });

  it("ignores anything that hasn't sold", () => {
    const live = { id: "x", status: "published", listing: { price: 50 } };
    expect(salesSummary([live], "week", NOW).count).toBe(0);
  });

  it("sets undated sales aside instead of dating them to today", () => {
    const s = salesSummary([sold({ sold_at: "" })], "week", NOW);
    expect(s.count).toBe(0);
    expect(s.total).toBe(0);
    expect(s.undated).toBe(1);
  });

  it("ignores a sale dated in the future", () => {
    expect(salesSummary([sold({ ago: -3 })], "week", NOW).count).toBe(0);
  });

  it("falls back to the default window for an unknown range id", () => {
    expect(soldRange("nonsense").id).toBe("week");
  });
});

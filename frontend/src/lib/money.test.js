/* A total has to say which money it is in.
 *
 * This branch already fixed one of these: a seller on eBay.co.uk was told
 * their £45 item sold "for $45.00", because the sold notification formatted
 * the amount without carrying `listing.currency`. The fix went in at the
 * notification. The dashboard was still doing it.
 *
 * `formatMoney(n)` defaults to USD, and four call sites take that default —
 * the Sold tile, the profit line, the "listed" total on the Active tile, and
 * the fee estimate on a listing card. `salesSummary` made it unavoidable:
 * it sums `proceeds` and returns a bare number, so there was nothing for the
 * caller to pass even if it wanted to.
 *
 * The second half is worse and quieter. Summing across currencies produces a
 * number that is wrong whatever symbol is printed next to it, and a seller
 * with one eBay.co.uk listing and one Etsy US listing gets exactly that. The
 * app's own rule for this is already written down elsewhere: say what you
 * could not work out, rather than picking one and hoping. So a mixed total is
 * reported as mixed, and the screen shows a dash — the same answer it gives
 * for a figure it could not measure.
 */
import { describe, expect, it } from "vitest";
import { salesSummary } from "./sales";

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.parse("2026-08-30T12:00:00Z");

function sold(price, currency, daysAgo = 1) {
  return {
    status: "sold",
    listing: {
      sold_price: price, sold_quantity: 1, currency,
      sold_at: new Date(NOW - daysAgo * DAY).toISOString(),
    },
  };
}

describe("what currency a sales total is in", () => {
  it("is the currency of the sales it counted", () => {
    const s = salesSummary([sold(45, "GBP"), sold(20, "GBP")], "week", NOW);
    expect(s.total).toBe(65);
    expect(s.currency).toBe("GBP");
  });

  it("is not silently dollars", () => {
    const s = salesSummary([sold(45, "GBP")], "week", NOW);
    expect(s.currency).not.toBe("USD");
  });

  it("says so when the sales are in different currencies", () => {
    // Summing these produces a number that is wrong next to any symbol.
    const s = salesSummary([sold(45, "GBP"), sold(20, "USD")], "week", NOW);
    expect(s.mixedCurrency).toBe(true);
    expect(s.currency).toBe(null);
  });

  it("is plain USD when that is what the sales actually are", () => {
    const s = salesSummary([sold(45, "USD")], "week", NOW);
    expect(s.currency).toBe("USD");
    expect(s.mixedCurrency).toBe(false);
  });

  it("falls back to the app's default when a record carries no currency", () => {
    // Imported listings always have one; a draft created before the field
    // existed may not. One unlabelled sale must not read as a second
    // currency and blank the seller's total.
    const s = salesSummary([sold(45, "USD"), sold(20, undefined)], "week", NOW);
    expect(s.currency).toBe("USD");
    expect(s.mixedCurrency).toBe(false);
  });

  it("says nothing about currency when it counted nothing", () => {
    const s = salesSummary([], "week", NOW);
    expect(s.total).toBe(0);
    expect(s.currency).toBe(null);
    expect(s.mixedCurrency).toBe(false);
  });

  it("only counts the currencies of sales inside the window", () => {
    // A GBP sale from last year must not make this week's USD total "mixed".
    const s = salesSummary([sold(45, "USD", 1), sold(20, "GBP", 300)],
                           "week", NOW);
    expect(s.currency).toBe("USD");
    expect(s.mixedCurrency).toBe(false);
  });
});

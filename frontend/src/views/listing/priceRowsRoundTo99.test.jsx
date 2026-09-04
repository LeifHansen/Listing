/* Tapping a comparable listing prices YOUR listing, so it lands on a .99.
 *
 * Reported as: "please make all prices $x.99 rather than whole numbers. So if
 * you price an item at $25, list it at $24.99 by default." The server side of
 * that rule shapes every price it picks (backend/money.py charm_price), but
 * the price card is where the seller picks one in the browser: the median row
 * and each comp row write straight into the price field, and a $25.00 median
 * used to go in as $25.00.
 *
 * The row still REPORTS the market as measured — a median shown as $24.99
 * when the median is $25.00 would be a lie about the data — and applies the
 * price we would list at. Both halves are checked here, because rounding the
 * wrong one of them is the mistake this is guarding against.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { PricingCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

const COMPS = {
  checked: true,
  sources: [{
    label: "Live asking prices on eBay",
    estimate: 25.0, low: 18.0, high: 40.0, count: 9,
    sample: [{ title: "The same jacket, other seller", price: 30.0,
               condition: "Pre-owned", url: "" }],
    search_url: "https://ebay.test/sch",
  }],
  suggestion: { price: 24.99, low: 18.0, high: 40.0, count: 9,
                basis: "Live asking prices on eBay", sold_data: false },
};

/** The slice of useListingForm PricingCard reads, recording every set(). */
function stub(sets, over = {}) {
  return {
    fixLevel: () => undefined,
    fixTarget: null,
    form: {
      title: "A jacket", price: "", quantity: 1, condition: "USED_EXCELLENT",
      listing_format: "FIXED_PRICE", currency: "USD", auction_start_price: "",
      condition_description: "", purchase_price: "", item_specifics: [],
      accept_offers: false, ...over,
    },
    completion: { pricing: "todo" },
    categoryMeta: { aspects: [], conditions: [], conditionsChecked: true },
    priceData: COMPS,
    comps: null, compsBusy: false, isLive: false, publishResult: null,
    set: (field, value) => sets.push([field, value]),
    checkMarketPrice: () => {},
    loadComps: () => {}, suggestTitle: () => {}, aiBusy: false,
  };
}

function render(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => { root.render(<ToastProvider><PricingCard w={w} /></ToastProvider>); });
  return host;
}

/** The clickable comp rows, in the order the card draws them. */
function rows() {
  return [...host.querySelectorAll('[role="button"]')];
}

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("a price taken off the market", () => {
  it("goes into the field on the nearest .99", () => {
    const sets = [];
    render(stub(sets));
    act(() => { rows()[0].click(); });     // the median row
    expect(sets).toEqual([["price", "24.99"]]);
  });

  it("does the same for one comparable listing's own asking price", () => {
    const sets = [];
    render(stub(sets));
    act(() => { rows()[1].click(); });     // the $30.00 comp
    expect(sets).toEqual([["price", "29.99"]]);
  });

  it("still reports the market as measured", () => {
    render(stub([]));
    // The median is $25 and says so; what it would SET is named beside it, so
    // the number that lands in the field is never a surprise.
    expect(host.textContent).toContain("$25");
    expect(host.textContent).toContain("Click to price at $24.99.");
  });

  it("marks the row the current price came from", () => {
    // The highlight compares against the price the row APPLIES. Comparing
    // against the raw median instead left the row a seller had just clicked
    // looking unchosen.
    render(stub([], { price: "24.99" }));
    expect(rows()[0].className).toContain("border-blue");
    expect(rows()[1].className).not.toContain("border-blue");
  });
});

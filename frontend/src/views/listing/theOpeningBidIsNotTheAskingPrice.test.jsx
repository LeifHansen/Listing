/* The editor's price card, on a listing that is an AUCTION.
 *
 * "Check market price" answers one question — what comparable items are
 * asking — and the card applied that answer to `price`. On an auction, eBay
 * does not read `price`: a plain auction is priced by `auction_start_price`
 * and nothing else (lib/listingFormat). So the market data reached a field
 * the listing does not have, and the one field an auction needs was left for
 * the seller to guess at.
 *
 * What lands there now comes from the same lookup read the other way round
 * (backend services/pricing.auction_start), because an auction is STARTED,
 * not priced, and the two are different numbers:
 *
 *   - open AT the market price and it is a Buy It Now with extra steps: no
 *     bids, no sale, relist;
 *   - open at a dollar on an item nobody is hunting for and it sells for a
 *     dollar, because a no-reserve auction with one bidder ends at the floor.
 *
 * Which of those an item is in is what the comp COUNT measures, and the
 * server says so in `basis` — a number a seller can overrule on purpose is
 * one whose evidence is on screen beside it.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { PricingCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

const MARKET = {
  checked: true,
  sources: [{
    label: "Live asking prices on eBay",
    estimate: 40.0, low: 30.0, high: 55.0, count: 30,
    sample: [{ title: "The same jacket, other seller", price: 44.0,
               condition: "Pre-owned", url: "" }],
    search_url: "https://ebay.test/sch",
  }],
  suggestion: { price: 39.99, low: 30.0, high: 55.0, count: 30,
                basis: "Live asking prices on eBay", sold_data: false },
  auction: {
    start_price: 15.99, market: 40.0, low: 30.0, high: 55.0, count: 30,
    sold_data: false, deep_market: true, strategy: "median",
    label: "Opening bid, from what comparable items are asking",
    basis: "30 comparable items are listed at around $40.00 — deep enough "
      + "that bidders find it and price it, so it opens well under the market. "
      + "Median — a standard opener for this market.",
  },
};

/** The slice of useListingForm PricingCard reads, recording every set(). */
function stub(sets, over = {}, priceData = MARKET) {
  return {
    fixLevel: () => undefined,
    fixTarget: null,
    form: {
      title: "A jacket", price: "", quantity: 1, condition: "USED_EXCELLENT",
      listing_format: "AUCTION", currency: "USD", auction_start_price: "",
      auction_duration: "DAYS_7",
      condition_description: "", purchase_price: "", item_specifics: [],
      accept_offers: false, ...over,
    },
    completion: { pricing: "todo" },
    categoryMeta: { aspects: [], conditions: [], conditionsChecked: true },
    priceData,
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

/** The rows that can actually be pressed, in the order the card draws them. */
function rows() {
  return [...host.querySelectorAll('[role="button"]')];
}

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the market, on an auction", () => {
  it("opens the bidding from eBay's comps, in the field an auction uses", () => {
    const sets = [];
    render(stub(sets));
    act(() => { rows()[0].click(); });
    expect(sets).toEqual([["auction_start_price", "15.99"]]);
  });

  it("does not offer to write the asking price into a field eBay ignores", () => {
    const sets = [];
    render(stub(sets));
    // The comps are still shown — they are what the opener was derived from,
    // and what the item is worth is worth knowing. They are just not buttons
    // on a format with no `price` field to apply them to.
    expect(host.textContent).toContain("median of 30 listings");
    expect(host.textContent)
      .toContain("what the item is worth, not where to start the bidding");
    expect(host.textContent).not.toContain("Click to price at");
    // The opener is the only actionable row, so nothing here can set `price`.
    rows().forEach((row) => act(() => { row.click(); }));
    expect(sets.map(([field]) => field)).toEqual(["auction_start_price"]);
  });

  it("says what the opener was measured from", () => {
    render(stub([]));
    expect(host.textContent).toContain("30 comparable items are listed at");
    expect(host.textContent).toContain("opens well under the market");
    expect(host.textContent).toContain("Click to open the bidding at $15.99.");
  });

  it("marks the row the starting bid already came from", () => {
    render(stub([], { auction_start_price: "15.99" }));
    expect(rows()[0].className).toContain("border-blue");
  });

  it("leaves a live auction's starting bid alone", () => {
    // eBay does not revise StartPrice (services/ebay_trading.REVISABLE_FIELDS)
    // and bids may already be against it. The field itself is shown locked;
    // a row offering to change it would be a button that cannot work.
    render({ ...stub([]), isLive: true });
    expect(host.textContent).not.toContain("Click to open the bidding at");
  });

  it("recommends nothing when eBay had nothing to measure", () => {
    render(stub([], {}, {
      checked: true,
      sources: MARKET.sources,
      suggestion: MARKET.suggestion,
      auction: null,
    }));
    expect(host.textContent).not.toContain("Opening bid");
  });
});

describe("the market, on an auction that also carries a Buy It Now", () => {
  it("sends each number to its own field", () => {
    const sets = [];
    render(stub(sets, { listing_format: "AUCTION_BIN" }));
    act(() => { rows()[0].click(); });   // the opener
    act(() => { rows()[1].click(); });   // the median comp row
    expect(sets).toEqual([
      ["auction_start_price", "15.99"],
      // AUCTION_BIN really does have a Buy It Now, so the comps apply to it
      // exactly as they do on a fixed-price listing.
      ["price", "39.99"],
    ]);
  });
});

describe("the market, on a Buy It Now", () => {
  it("is unchanged — one price, taken off the comps", () => {
    const sets = [];
    render(stub(sets, { listing_format: "FIXED_PRICE" }));
    expect(host.textContent).not.toContain("Opening bid");
    act(() => { rows()[0].click(); });
    expect(sets).toEqual([["price", "39.99"]]);
  });
});

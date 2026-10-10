/* "Check market price" is a disclosure on the price row.
 *
 * It was a button with its results stacked under the whole card. Now it is
 * one line under the price: pressed with nothing loaded it runs the lookup
 * and opens; once the comps land it opens itself and reads what the market
 * said ("Comps · $25 median of 9 listings"); pressed again it folds. The
 * rows inside are the same SuggestionRows with the same strings -- see
 * priceRowsRoundTo99 and theOpeningBidIsNotTheAskingPrice for what they do.
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
    sample: [], search_url: "",
  }],
  suggestion: { price: 24.99, low: 18.0, high: 40.0, count: 9,
                basis: "Live asking prices on eBay", sold_data: false },
};

function stub(calls, priceData) {
  return {
    fixLevel: () => undefined,
    fixTarget: null,
    form: {
      title: "A jacket", price: "", quantity: 1, condition: "USED_EXCELLENT",
      listing_format: "FIXED_PRICE", currency: "USD", auction_start_price: "",
      condition_description: "", purchase_price: "", retail_price: "",
      item_specifics: [],
    },
    completion: { price: "todo" },
    categoryMeta: { aspects: [], conditions: [], conditionsChecked: true },
    priceData,
    isLive: false, publishResult: null,
    set: () => {},
    checkMarketPrice: () => calls.push("check"),
  };
}

function render(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => { root.render(<ToastProvider><PricingCard w={w} /></ToastProvider>); });
  return host;
}

const toggle = () => host.querySelector("[data-comps] button[aria-expanded]");
const rows = () => [...host.querySelectorAll('[role="button"]')];

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the comps disclosure", () => {
  it("offers the lookup when nothing has been checked, and runs it when pressed", () => {
    const calls = [];
    render(stub(calls, null));
    expect(toggle().textContent).toContain("Check market price");
    expect(rows()).toHaveLength(0);
    act(() => toggle().click());
    expect(calls).toEqual(["check"]);
  });

  it("opens itself with the market's answer in the line, and folds on a press", () => {
    render(stub([], COMPS));
    expect(toggle().textContent).toContain("Comps · $25 median of 9 listings");
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(rows()).toHaveLength(1);
    act(() => toggle().click());
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
    expect(rows()).toHaveLength(0);
    // The line still says what the market said while folded.
    expect(toggle().textContent).toContain("median of 9 listings");
  });

  it("says while it is looking, and when it could not", () => {
    render(stub([], { loading: true }));
    expect(toggle().textContent).toContain("Finding comparable listings");
    act(() => root.unmount());
    host.remove();
    render(stub([], { error: "eBay didn't answer" }));
    expect(toggle().textContent).toContain("Couldn't check the market");
    expect(host.textContent).toContain("eBay didn't answer");
  });
});

/* The price, on the card — and for an auction, what eBay says to open at.
 *
 * Asked for as: "please make price editable from the quick view card
 * grid/list view. Also please recommend starting price for auctions via ebay
 * api".
 *
 * Price was the last thing on a draft card that could only be changed by
 * opening the editor. A seller reviewing a batch of fresh drafts is reading a
 * grid of numbers the AI chose, and the job is changing the two or three it
 * got wrong — which cost a trip into the editor and back, per item.
 *
 * The auction half is a different number, not the same one relabelled, and
 * that is the whole of why it needed the eBay lookup rather than a rule of
 * thumb:
 *
 *   - "Check market price" answers the BUY IT NOW question — the median of
 *     comparable listings. On a plain auction that number went into `price`,
 *     a field eBay does not read for that format, so the one field an auction
 *     actually needs was the one the market data never reached;
 *   - opening AT the market price is a Buy It Now with extra steps: no bids;
 *   - opening at a dollar on an item nobody is hunting for sells it for a
 *     dollar, because a no-reserve auction with one bidder ends at the floor.
 *
 * So the opener comes from the comps read the other way round
 * (backend services/pricing.auction_start), and it lands in
 * `auction_start_price`.
 *
 * Rendered with react-dom + act rather than a testing library; the repo has
 * no testing-library dependency (see MessagesInbox.test.jsx).
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PriceQuickEdit } from "./PriceQuickEdit";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const app = vi.hoisted(() => ({ current: { health: { taxonomy_configured: true } } }));
vi.mock("@/store", () => ({ useApp: () => app.current }));

const server = vi.hoisted(() => ({ posted: [], answer: null, fail: null }));
vi.mock("@/lib/api", () => ({
  patchJson: vi.fn(async () => ({ ok: true })),
  postJson: vi.fn(async (path, body) => {
    server.posted.push({ path, body });
    if (server.fail) throw new Error(server.fail);
    return server.answer;
  }),
}));

// What /api/price-suggestions answers for a deep market: a price to LIST at
// and, off the same measurement, where to OPEN the bidding.
const MARKET = {
  query: "Levi's vintage 501",
  checked: true,
  sources: [{ label: "Live asking prices on eBay", estimate: 40, low: 30, high: 55, count: 30 }],
  suggestion: {
    price: 39.99, low: 30, high: 55, count: 30, sold_data: false,
    basis: "Live asking prices on eBay · Median — typical market price",
  },
  auction: {
    start_price: 15.99, market: 40, low: 30, high: 55, count: 30,
    sold_data: false, deep_market: true, strategy: "median",
    label: "Opening bid, from what comparable items are asking",
    basis: "30 comparable items are listed at around $40.00 — deep enough "
      + "that bidders find it and price it, so it opens well under the market. "
      + "Median — a standard opener for this market.",
  },
};

let root;
let host;

function render(listing, onPick = () => {}) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => {
    root.render(<PriceQuickEdit listing={listing} onPick={onPick} />);
  });
  return host;
}

const moneyFields = () => [...host.querySelectorAll("input[type=number]")];
const labelled = (name) =>
  moneyFields().find((el) => (el.getAttribute("aria-label") || "").startsWith(name));
const buttons = () => [...host.querySelectorAll("button")];
const button = (text) =>
  buttons().find((b) => (b.textContent || "").includes(text));

function change(el, value) {
  act(() => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

// Leaving the field. React's onBlur is delivered by the native `focusout`
// (`blur` does not bubble, so React cannot delegate it from the root), which
// is why dispatching a "blur" event here reaches nothing.
function leave(el) {
  act(() => { el.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
}

async function press(el) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  app.current = { health: { taxonomy_configured: true } };
  server.posted = [];
  server.answer = MARKET;
  server.fail = null;
});

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = undefined;
  host = undefined;
  vi.restoreAllMocks();
});

describe("the price on a draft card", () => {
  it("offers the price on a Buy It Now, which is what a card used to hide", () => {
    render({ listing_format: "FIXED_PRICE", price: 24.99 });
    expect(moneyFields()).toHaveLength(1);
    expect(labelled("Price").value).toBe("24.99");
  });

  it("offers the starting bid, and only that, on a plain auction", () => {
    render({ listing_format: "AUCTION", auction_start_price: 9.99, price: 40 });
    expect(moneyFields()).toHaveLength(1);
    expect(labelled("Starting bid").value).toBe("9.99");
    // `price` is unused on a plain auction. A box for it here would invite a
    // number that never reaches eBay.
    expect(labelled("Buy It Now")).toBeUndefined();
    expect(labelled("Price (")).toBeUndefined();
  });

  it("offers both numbers on an auction with a Buy It Now", () => {
    render({ listing_format: "AUCTION_BIN", auction_start_price: 9.99, price: 40 });
    expect(labelled("Starting bid").value).toBe("9.99");
    expect(labelled("Buy It Now").value).toBe("40");
  });

  // A listing saved before the format field existed is a Buy It Now, which is
  // what the model defaults to — and it still has a price to edit.
  it("treats a listing with no stored format as a Buy It Now", () => {
    render({ price: 12 });
    expect(labelled("Price").value).toBe("12");
  });

  // One PATCH per number, when the seller leaves the box -- not one per
  // keystroke on the way to $12.50.
  it("saves a typed price once, on blur", () => {
    const picks = [];
    render({ listing_format: "FIXED_PRICE", price: null },
      (patch) => picks.push(patch));
    const field = labelled("Price");
    change(field, "1");
    change(field, "12");
    change(field, "12.5");
    expect(picks).toEqual([]);
    leave(field);
    expect(picks).toEqual([{ price: 12.5 }]);
  });

  it("patches the one number, never the listing it is holding", () => {
    const picks = [];
    render({ listing_format: "AUCTION_BIN", auction_start_price: 9.99, price: 40,
             title: "A jacket", category_id: "11450" },
      (patch) => picks.push(patch));
    change(labelled("Starting bid"), "4.99");
    leave(labelled("Starting bid"));
    expect(picks).toEqual([{ auction_start_price: 4.99 }]);
  });

  it("saves nothing when the number is left as it was", () => {
    const picks = [];
    render({ listing_format: "FIXED_PRICE", price: 24.99 },
      (patch) => picks.push(patch));
    leave(labelled("Price"));
    expect(picks).toEqual([]);
  });

  // Anything the listing model would reject puts the stored value back rather
  // than being sent -- the seller sees the box return to what is saved.
  it("refuses a negative price instead of sending it", () => {
    const picks = [];
    render({ listing_format: "FIXED_PRICE", price: 24.99 },
      (patch) => picks.push(patch));
    const field = labelled("Price");
    change(field, "-5");
    leave(field);
    expect(picks).toEqual([]);
    expect(field.value).toBe("24.99");
  });

  it("clears a price the seller emptied", () => {
    const picks = [];
    render({ listing_format: "FIXED_PRICE", price: 24.99 },
      (patch) => picks.push(patch));
    const field = labelled("Price");
    change(field, "");
    leave(field);
    expect(picks).toEqual([{ price: null }]);
  });
});

describe("what eBay says to open an auction at", () => {
  it("asks eBay for the item, and lands the opener in the starting bid", async () => {
    const picks = [];
    render({ listing_format: "AUCTION", brand: "Levi's", title: "vintage 501",
             category_id: "11483", condition: "USED_EXCELLENT" },
      (patch) => picks.push(patch));

    await press(button("Suggest an opening bid"));
    expect(server.posted).toEqual([{
      path: "/api/price-suggestions",
      body: { query: "Levi's vintage 501", category_id: "11483",
              condition: "USED_EXCELLENT" },
    }]);

    await press(button("Open the bidding at"));
    // The opening bid, not the $39.99 the same lookup says to LIST at.
    expect(picks).toEqual([{ auction_start_price: 15.99 }]);
  });

  it("names the number it is about to recommend", () => {
    render({ listing_format: "AUCTION", title: "A jacket" });
    // "Suggest a price" on a format whose only field is an opening bid is a
    // promise about the wrong number.
    expect(button("Suggest an opening bid")).toBeTruthy();
    expect(button("Suggest a price")).toBeUndefined();
  });

  it("offers the market price on a Buy It Now, and no opener", async () => {
    const picks = [];
    render({ listing_format: "FIXED_PRICE", title: "A jacket" },
      (patch) => picks.push(patch));
    await press(button("Suggest a price"));
    expect(button("Open the bidding at")).toBeUndefined();
    await press(button("Price it at"));
    expect(picks).toEqual([{ price: 39.99 }]);
  });

  it("offers both on an auction with a Buy It Now, to the field each belongs to",
    async () => {
      const picks = [];
      render({ listing_format: "AUCTION_BIN", title: "A jacket" },
        (patch) => picks.push(patch));
      await press(button("Suggest an opening bid"));
      await press(button("Open the bidding at"));
      await press(button("Buy It Now at"));
      expect(picks).toEqual([
        { auction_start_price: 15.99 },
        { price: 39.99 },
      ]);
    });

  it("shows the basis, so the number can be overruled on purpose", async () => {
    render({ listing_format: "AUCTION", title: "A jacket" });
    await press(button("Suggest an opening bid"));
    expect(host.textContent).toContain("30 comparable items are listed at");
    expect(host.textContent).toContain("opens well under the market");
  });

  // One press, one live eBay call, against an allowance shared by every
  // seller (main._taxonomy_guard). A grid of thirty draft cards that each
  // asked on sight would spend it in one screen.
  it("asks nothing until the seller asks", () => {
    render({ listing_format: "AUCTION", title: "A jacket" });
    expect(server.posted).toEqual([]);
    expect(host.textContent).not.toContain("Open the bidding at");
  });

  it("does not offer a lookup the server cannot make", () => {
    app.current = { health: { taxonomy_configured: false } };
    render({ listing_format: "AUCTION", title: "A jacket" });
    expect(button("Suggest")).toBeUndefined();
    // The field is still there: no eBay credentials is a reason not to
    // RECOMMEND a number, not a reason to stop the seller typing one.
    expect(labelled("Starting bid")).toBeTruthy();
  });

  // The distinction lib/priceLookup exists for, carried onto the card: a
  // lookup that never ran is not evidence about the market, and must not be
  // reported as one — least of all by blaming the seller's title.
  it("does not call a failed lookup an empty market", async () => {
    server.fail = "eBay returned 429";
    render({ listing_format: "AUCTION", title: "A jacket" });
    await press(button("Suggest an opening bid"));
    expect(host.textContent).toContain("couldn’t check");
    expect(host.textContent).not.toContain("simpler title");
  });

  it("says so when eBay looked and found nothing comparable", async () => {
    server.answer = { checked: true, sources: [], suggestion: null, auction: null };
    render({ listing_format: "AUCTION", title: "A one-off thing" });
    await press(button("Suggest an opening bid"));
    expect(host.textContent).toContain("No comparable listings");
  });

  it("asks for a title rather than searching eBay for nothing", async () => {
    render({ listing_format: "AUCTION", title: "" });
    await press(button("Suggest an opening bid"));
    expect(server.posted).toEqual([]);
    expect(host.textContent).toContain("Add a title first");
    // And NOT the empty-market sentence, which is a claim about eBay that
    // nobody made a request to support.
    expect(host.textContent).not.toContain("No comparable listings");
  });
});

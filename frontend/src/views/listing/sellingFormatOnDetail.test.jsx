/* The selling format on the listing detail page: offered, named, and locked
 * once eBay has the listing.
 *
 * The three formats were already here, but the card would happily let a
 * seller switch a LIVE Buy It Now to an auction, save, and be told the
 * listing was updated. It wasn't: format, starting bid and auction length are
 * all absent from services/ebay_trading.REVISABLE_FIELDS because eBay does
 * not revise them, so the revise goes through carrying everything else and
 * the listing on eBay stays exactly the format it was. The app's own origin
 * badge already told sellers this was impossible.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { PricingCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

function stub(sets, over = {}) {
  return {
    fixLevel: () => undefined,
    fixTarget: null,
    form: {
      title: "A jacket", price: "24.99", quantity: 1,
      condition: "USED_EXCELLENT", listing_format: "FIXED_PRICE",
      currency: "USD", auction_start_price: "", auction_duration: "DAYS_7",
      condition_description: "", purchase_price: "", item_specifics: [],
      accept_offers: false, ...over,
    },
    completion: { pricing: "todo" },
    categoryMeta: { aspects: [], conditions: [], conditionsChecked: true },
    priceData: { checked: false, sources: [] },
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

/** The three format buttons, by their labels. */
const formatButtons = () =>
  [...host.querySelectorAll("button[aria-pressed]")];

const field = (label) =>
  [...host.querySelectorAll("label")]
    .find((el) => (el.textContent || "").startsWith(label));

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = undefined;
  host = undefined;
});

describe("the selling format in the editor", () => {
  it("offers all three, under the names the cards use", () => {
    render(stub([]));
    expect(formatButtons().map((b) => b.textContent))
      .toEqual(["Buy It Now", "Auction", "Both"]);
  });

  it("marks the one this listing uses", () => {
    render(stub([], { listing_format: "AUCTION_BIN" }));
    const pressed = formatButtons().filter((b) => b.getAttribute("aria-pressed") === "true");
    expect(pressed.map((b) => b.textContent)).toEqual(["Both"]);
  });

  it("sets the format when another is picked", () => {
    const sets = [];
    render(stub(sets));
    expect(formatButtons().every((b) => !b.disabled)).toBe(true);
    act(() => formatButtons()[1].click());
    expect(sets).toContainEqual(["listing_format", "AUCTION"]);
  });

  // The whole point of "Both" being a word rather than "Auction + BIN".
  it("says what each format means", () => {
    render(stub([], { listing_format: "AUCTION_BIN" }));
    const both = formatButtons()[2];
    expect(both.getAttribute("title")).toContain("Buy It Now");
    expect(both.getAttribute("title")).toContain("bid");
  });

  describe("once the listing is live on eBay", () => {
    it("locks the format rather than pretending it can change", () => {
      const sets = [];
      const w = stub(sets);
      w.isLive = true;
      render(w);

      expect(formatButtons().every((b) => b.disabled)).toBe(true);
      act(() => formatButtons()[1].click());
      expect(sets).toEqual([]);
    });

    it("says why, rather than just going grey", () => {
      const w = stub([]);
      w.isLive = true;
      render(w);
      expect(formatButtons()[0].getAttribute("title")).toContain("relist");
    });

    it("locks the starting bid and the auction length too", () => {
      const w = stub([], {
        listing_format: "AUCTION", auction_start_price: "0.99", price: "",
      });
      w.isLive = true;
      render(w);
      expect(field("Starting bid").querySelector("input").disabled).toBe(true);
      expect(field("Duration").querySelector("select").disabled).toBe(true);
    });

    // A live auction's Buy It Now IS revisable (build_revise_item sends it),
    // so locking it would take away the one price the seller can still move.
    it("leaves the Buy It Now price editable", () => {
      const w = stub([], {
        listing_format: "AUCTION_BIN", auction_start_price: "0.99", price: "40",
      });
      w.isLive = true;
      render(w);
      expect(field("Buy It Now").querySelector("input").disabled).toBe(false);
    });
  });
});

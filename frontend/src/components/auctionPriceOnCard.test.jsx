/* An auction on a card is not a listing with no price.
 *
 * `price` is the Buy It Now field. A plain auction does not have one — the
 * money lives in `auction_start_price`, and an auction imported from eBay
 * arrives with `price` null outright (services/ebay_trading._item_to_listing
 * fills it from BuyItNowPrice, which a Chinese auction has none of). Every
 * card in the app read `price` and nothing else, so a seller's auctions each
 * sat on the grid saying "no price yet" beside a starting bid nobody showed
 * them, and the format was invisible.
 *
 * The card asks the format first (lib/listingFormat.askingPrice) and says
 * which number it is showing: a bare "$0.99" under a photo, in a grid of Buy
 * It Now prices, reads as a 99-cent item rather than as where the bidding
 * opens.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { ListingCard } from "@/components/ListingCard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

function render(listing, over = {}) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => {
    root.render(
      <ListingCard
        item={{ id: "s1", status: "draft", listing, ...over }}
        onOpen={() => {}}
      />);
  });
  return host;
}

const text = () => host.textContent;

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = undefined;
  host = undefined;
});

describe("what a card says a listing is asking", () => {
  it("shows the price on a Buy It Now, with nothing extra", () => {
    render({ title: "A jacket", listing_format: "FIXED_PRICE", price: 24.99,
             currency: "USD" });
    expect(text()).toContain("$24.99");
    expect(text()).not.toContain("no price yet");
    // The format chip is silent on the default: a chip on every card in the
    // store is noise that teaches the eye to skip the row auctions need read.
    expect(text()).not.toContain("Auction");
    // And so is the tooltip. A Buy It Now price IS the price; explaining that
    // on every card in the store is one more thing to read wrong.
    const priceTip = [...host.querySelectorAll("[title]")]
      .map((el) => el.getAttribute("title"))
      .find((t) => t.includes("Buy It Now"));
    expect(priceTip).toBeUndefined();
  });

  it("shows an auction's starting bid, and says that is what it is", () => {
    render({ title: "A jacket", listing_format: "AUCTION", price: null,
             auction_start_price: 0.99, currency: "USD" });
    expect(text()).toContain("$0.99");
    expect(text()).toContain("Bid");
    expect(text()).toContain("Auction");
    expect(text()).not.toContain("no price yet");
  });

  it("quotes an auction with a Buy It Now at the Buy It Now", () => {
    render({ title: "A jacket", listing_format: "AUCTION_BIN", price: 40,
             auction_start_price: 0.99, currency: "USD" });
    // What a buyer can pay right now, not where the bidding opens.
    expect(text()).toContain("$40.00");
    expect(text()).not.toContain("$0.99");
    expect(text()).toContain("Auction + BIN");
  });

  // The opening bid is not lost when the Buy It Now is the headline number:
  // the tooltip carries both, and how long the auction runs.
  it("keeps both numbers in reach on the card", () => {
    render({ title: "A jacket", listing_format: "AUCTION_BIN", price: 40,
             auction_start_price: 0.99, auction_duration: "DAYS_3",
             currency: "USD" });
    const titles = [...host.querySelectorAll("[title]")]
      .map((el) => el.getAttribute("title"));
    const summary = titles.find((t) => t.includes("Buy It Now"));
    expect(summary).toContain("$0.99");
    expect(summary).toContain("$40.00");
    expect(summary).toContain("3 days");
  });

  // "No price yet" is still the honest answer for an auction nobody has
  // priced -- it must not be replaced by a stale Buy It Now price the format
  // does not use.
  it("still says so when the format's own field is empty", () => {
    render({ title: "A jacket", listing_format: "AUCTION", price: 24.99,
             auction_start_price: null, currency: "USD" });
    expect(text()).toContain("no price yet");
    expect(text()).not.toContain("$24.99");
  });
});

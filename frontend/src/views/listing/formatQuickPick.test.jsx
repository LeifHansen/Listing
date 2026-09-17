/* A seller picks how a draft SELLS from the card, not only from the editor.
 *
 * Asked for as: "add the option for users to select between buy it now,
 * auction, or both, when viewing listing draft cards, and from listing detail
 * page. I don't think we factor that in at all."
 *
 * Two things have to hold for that to be true rather than merely present:
 *
 *   - all three formats are offered, under the names the request uses; and
 *   - choosing one PATCHES that field alone — a card holds a summary of a
 *     listing, and writing the summary back is how an edit made anywhere else
 *     gets overwritten (main.patch_listing exists for this).
 *
 * The MONEY each format needs is the price control next door, which sits on
 * every card and follows this select (PriceQuickEdit, priceQuickEdit.test).
 * It used to live in here, and the rule that made it worth showing is the one
 * that now makes it worth showing always: a draft switched to an auction is
 * priced by a starting bid it does not have, and a card that does not ask for
 * one turns a publishable draft into a blocked one with the field that fixes
 * it two screens away.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FormatQuickPick } from "./FormatQuickPick";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

function render(listing, onPick) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => {
    root.render(<FormatQuickPick listing={listing} onPick={onPick} />);
  });
  return host;
}

const select = () => host.querySelector("select");

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = undefined;
  host = undefined;
  vi.restoreAllMocks();
});

describe("the selling format on a draft card", () => {
  it("offers Buy It Now, auction and both", () => {
    render({ listing_format: "FIXED_PRICE", price: 24.99 }, () => {});
    expect([...select().options].map((o) => [o.value, o.textContent]))
      .toEqual([
        ["FIXED_PRICE", "Buy It Now"],
        ["AUCTION", "Auction"],
        ["AUCTION_BIN", "Both"],
      ]);
    expect(select().value).toBe("FIXED_PRICE");
  });

  // A listing saved before the field existed, or imported without one, is a
  // Buy It Now -- what the model defaults to. The control must show that
  // rather than rendering blank, which is what a <select> does when its value
  // is not among its options.
  it("shows a listing with no stored format as Buy It Now", () => {
    render({ price: 10 }, () => {});
    expect(select().value).toBe("FIXED_PRICE");
  });

  it("patches the format alone, never the listing it is holding", () => {
    const picks = [];
    render({ listing_format: "FIXED_PRICE", price: 24.99, title: "A jacket" },
      (patch) => picks.push(patch));
    act(() => {
      select().value = "AUCTION";
      select().dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(picks).toEqual([{ listing_format: "AUCTION" }]);
  });

  // Switching format must not move money between the two fields: a Buy It Now
  // price is what the seller wants for the item, a starting bid is where they
  // will let it open. Copying one into the other opens a $120 jacket at $120.
  it("does not turn the asking price into a starting bid", () => {
    const picks = [];
    render({ listing_format: "FIXED_PRICE", price: 120 },
      (patch) => picks.push(patch));
    act(() => {
      select().value = "AUCTION_BIN";
      select().dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(picks).toEqual([{ listing_format: "AUCTION_BIN" }]);
    expect(picks[0].auction_start_price).toBeUndefined();
  });

  // The money moved out (PriceQuickEdit). Two boxes for the same starting bid
  // on one card is two answers to the same question, and the one the seller
  // did not type into wins the next time either re-renders.
  it("asks for no money of its own — that is the price control's field", () => {
    render({ listing_format: "AUCTION", auction_start_price: 9.99 }, () => {});
    expect(host.querySelectorAll("input[type=number]")).toHaveLength(0);
  });

  it("does not re-patch the format it is already on", () => {
    const picks = [];
    render({ listing_format: "AUCTION" }, (patch) => picks.push(patch));
    act(() => {
      select().value = "AUCTION";
      select().dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(picks).toEqual([]);
  });
});

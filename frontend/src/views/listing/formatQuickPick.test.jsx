/* A seller picks how a draft SELLS from the card, not only from the editor.
 *
 * Asked for as: "add the option for users to select between buy it now,
 * auction, or both, when viewing listing draft cards, and from listing detail
 * page. I don't think we factor that in at all."
 *
 * Three things have to hold for that to be true rather than merely present:
 *
 *   - all three formats are offered, under the names the request uses;
 *   - choosing one PATCHES that field alone — a card holds a summary of a
 *     listing, and writing the summary back is how an edit made anywhere else
 *     gets overwritten (main.patch_listing exists for this); and
 *   - picking an auction offers the money that format needs. An auction is
 *     priced by its starting bid, so a pick that didn't ask for one would
 *     turn a publishable draft into a blocked one with the field that fixes
 *     it back inside the editor — the trip this control exists to save.
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
const moneyFields = () => [...host.querySelectorAll("input[type=number]")];
const labelled = (name) =>
  moneyFields().find((el) => (el.getAttribute("aria-label") || "").startsWith(name));

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

  it("asks for nothing extra on a Buy It Now", () => {
    render({ listing_format: "FIXED_PRICE", price: 24.99 }, () => {});
    expect(moneyFields()).toHaveLength(0);
  });

  it("asks for a starting bid on an auction, and only that", () => {
    render({ listing_format: "AUCTION", auction_start_price: 9.99 }, () => {});
    expect(moneyFields()).toHaveLength(1);
    expect(labelled("Starting bid")).toBeTruthy();
    expect(labelled("Starting bid").value).toBe("9.99");
    // `price` is unused on a plain auction. A box for it here would invite a
    // number that never reaches eBay.
    expect(labelled("Buy It Now")).toBeUndefined();
  });

  it("asks for both numbers on an auction with a Buy It Now", () => {
    render({ listing_format: "AUCTION_BIN", auction_start_price: 9.99, price: 40 },
      () => {});
    expect(labelled("Starting bid").value).toBe("9.99");
    expect(labelled("Buy It Now").value).toBe("40");
  });

  // One PATCH per number, when the seller leaves the box -- not one per
  // keystroke on the way to $12.50.
  it("saves a typed starting bid once, on blur", () => {
    const picks = [];
    render({ listing_format: "AUCTION", auction_start_price: null },
      (patch) => picks.push(patch));
    const field = labelled("Starting bid");
    change(field, "1");
    change(field, "12");
    change(field, "12.5");
    expect(picks).toEqual([]);
    leave(field);
    expect(picks).toEqual([{ auction_start_price: 12.5 }]);
  });

  it("saves nothing when the number is left as it was", () => {
    const picks = [];
    render({ listing_format: "AUCTION", auction_start_price: 9.99 },
      (patch) => picks.push(patch));
    const field = labelled("Starting bid");
    leave(field);
    expect(picks).toEqual([]);
  });

  // Anything the listing model would reject puts the stored value back rather
  // than being sent -- the seller sees the box return to what is saved.
  it("refuses a negative starting bid instead of sending it", () => {
    const picks = [];
    render({ listing_format: "AUCTION", auction_start_price: 9.99 },
      (patch) => picks.push(patch));
    const field = labelled("Starting bid");
    change(field, "-5");
    leave(field);
    expect(picks).toEqual([]);
    expect(field.value).toBe("9.99");
  });

  it("clears a starting bid the seller emptied", () => {
    const picks = [];
    render({ listing_format: "AUCTION", auction_start_price: 9.99 },
      (patch) => picks.push(patch));
    const field = labelled("Starting bid");
    change(field, "");
    leave(field);
    expect(picks).toEqual([{ auction_start_price: null }]);
  });
});

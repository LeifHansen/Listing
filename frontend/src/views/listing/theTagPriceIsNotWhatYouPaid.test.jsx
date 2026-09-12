/* Two prices can be printed on one item and they mean opposite things.
 *
 * Reported as a Scotch & Soda shirt with the brand's own $130 swing ticket
 * still attached, drafted as "Pre-owned - Good" at $49. Part of what made that
 * possible was that the app had exactly one field for a printed price —
 * "You paid" — so the MSRP off a brand's hang tag and a thrift sticker went to
 * the same place. The tag went in as a cost basis, the profit forecast read
 * the seller had spent $130 on a $49 shirt, and the one number in the photo
 * that said this was not a $12 shirt never reached the pricing decision.
 *
 * So the card carries both, and it does the division the seller was left to do
 * in their head: what percentage of the tag is this listed at. That ratio is
 * the whole judgment on something nobody has worn, and a draft at a third of
 * its own tag reads as a bargain until the number is on the screen.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { PricingCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

/** The slice of useListingForm PricingCard reads, recording every set(). */
function stub(sets, over = {}) {
  return {
    fixLevel: () => undefined,
    fixTarget: null,
    form: {
      title: "Scotch & Soda Amsterdam Oxford Shirt Mens L NWT",
      price: "", quantity: 1, condition: "NEW",
      listing_format: "FIXED_PRICE", currency: "USD", auction_start_price: "",
      condition_description: "", purchase_price: "", retail_price: "",
      item_specifics: [], accept_offers: false, ...over,
    },
    completion: { pricing: "todo" },
    categoryMeta: { aspects: [], conditions: [], conditionsChecked: true },
    priceData: null,
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

/** The number input under a given field label. */
function fieldInput(label) {
  const field = [...host.querySelectorAll("label")]
    .find((l) => l.textContent.includes(label));
  return field?.querySelector("input");
}

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the price on the tag", () => {
  it("has a field of its own, apart from what the seller paid", () => {
    render(stub([], { retail_price: 130, purchase_price: 6.99 }));
    expect(fieldInput("Retail on tag").value).toBe("130");
    expect(fieldInput("You paid").value).toBe("6.99");
  });

  it("is the seller's to correct — a mis-read tag is theirs to see", () => {
    const sets = [];
    render(stub(sets, { retail_price: 130 }));
    const input = fieldInput("Retail on tag");
    // Setting .value directly is invisible to React — it tracks the last value
    // it wrote and skips the change — so the native setter has to do it.
    act(() => {
      Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value").set.call(input, "89.50");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(sets).toEqual([["retail_price", "89.50"]]);
  });

  it("says what fraction of the tag the listing price is", () => {
    // The reported draft, exactly: $130 on the tag, $49 in the field.
    render(stub([], { retail_price: 130, price: "49" }));
    expect(host.textContent).toContain("38%");
    expect(host.textContent).toContain("of the $130.00 on the tag");
  });

  it("flags a fraction no new item should be listed at", () => {
    render(stub([], { retail_price: 130, price: "49" }));
    const ratio = [...host.querySelectorAll("strong")]
      .find((s) => s.textContent.includes("%"));
    expect(ratio.className).toContain("text-warning");
  });

  it("does not flag a defensible one", () => {
    render(stub([], { retail_price: 130, price: "79.99" }));
    const ratio = [...host.querySelectorAll("strong")]
      .find((s) => s.textContent.includes("%"));
    expect(ratio.textContent).toBe("62%");
    expect(ratio.className).not.toContain("text-warning");
  });

  it("says nothing at all when there is no tag to divide by", () => {
    // Most items have no readable retail price. The line must not appear as
    // a 0%, an Infinity or a NaN for any of them.
    for (const over of [{ price: "49" },
                        { retail_price: 0, price: "49" },
                        { retail_price: 130, price: "" }]) {
      render(stub([], over));
      expect(host.textContent).not.toContain("on the tag.");
      act(() => root.unmount());
      host.remove();
      root = null;
    }
  });

  it("keeps the profit forecast on what was actually paid", () => {
    // The bug in miniature: the tag must never reach the cost basis, or the
    // forecast reports a loss on a listing that is making money.
    render(stub([], { retail_price: 130, purchase_price: 6.99, price: "79.99" }));
    expect(host.textContent).toContain("$73.00");   // 79.99 - 6.99
  });
});

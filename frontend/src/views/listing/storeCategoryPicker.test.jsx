/* The listing says which shelf of the seller's OWN store it belongs on.
 *
 * Asked for as: "in addition to ebay category, assign proper store category
 * (if present in user's ebay account)". The server matches one from the draft
 * and sends it at publish; this is the half the seller sees — the picker in
 * the Category card, which has to do three things and one of them is nothing:
 *
 *   - offer the store's own shelves, by the path the seller reads in their
 *     store's menu, and write BOTH the id (what eBay is sent) and the name
 *     (what the editor shows) when one is chosen;
 *   - draw NOTHING AT ALL for the seller with no eBay Store — a labelled row
 *     with an empty control is how they would learn there is a setting here
 *     that they cannot have; and
 *   - say so, rather than showing an empty store, when the lookup could not
 *     run. "You have no shelves" and "we couldn't ask" are different answers
 *     (the same distinction lib/priceLookup draws for prices).
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { CategoryCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const SHELVES = [
  { id: "11", name: "Clothing", path: "Clothing", level: 1 },
  { id: "12", name: "Vintage Tees", path: "Clothing > Vintage Tees", level: 2 },
];

function ok(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

/** Every request answered; `store` is what /api/ebay/store-categories says. */
function serve(store, { connected = true } = {}) {
  vi.stubGlobal("fetch", vi.fn((url) => {
    const u = String(url);
    if (u.includes("/api/ebay/status")) return ok({ connected });
    if (u.includes("/api/ebay/store-categories")) return ok(store);
    return ok({});
  }));
}

let root;
let host;

/** The Category card with the slice of the form it reads. */
function stub(sets, over = {}) {
  return {
    fixLevel: () => undefined,
    fixTarget: null,
    form: {
      title: "A jacket", category_id: "15687",
      category_suggestion: "Clothing > Men > T-Shirts",
      store_category_id: "", store_category_name: "", ...over,
    },
    completion: { category: "todo" },
    catSuggestions: null,
    categoryMeta: { aspects: [], conditions: [], conditionsChecked: true },
    set: (field, value) => sets.push([field, value]),
    suggestCategories: () => {},
    chooseCategory: () => {},
    loadCategoryMeta: () => {},
  };
}

async function render(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><CategoryCard w={w} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return host;
}

/** The store-category dropdown, or null when the card drew none. */
function picker() {
  return host.querySelector('select[aria-label="Store category"]');
}

beforeEach(() => { localStorage.clear(); });

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("the store category picker", () => {
  it("offers the seller's own shelves, by their full path", async () => {
    serve({ store: true, checked: true, categories: SHELVES });
    await render(stub([]));
    expect(picker()).toBeTruthy();
    const options = [...picker().options].map((o) => o.textContent);
    expect(options).toEqual(
      ["Top level of your store", "Clothing", "Clothing > Vintage Tees"]);
  });

  it("writes the id eBay is sent and the name the editor shows", async () => {
    const sets = [];
    serve({ store: true, checked: true, categories: SHELVES });
    await render(stub(sets));
    await act(async () => {
      picker().value = "12";
      picker().dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(sets).toEqual([
      ["store_category_id", "12"], ["store_category_name", "Vintage Tees"],
    ]);
  });

  it("shows the shelf the draft was already filed on", async () => {
    serve({ store: true, checked: true, categories: SHELVES });
    await render(stub([], { store_category_id: "12",
                            store_category_name: "Vintage Tees" }));
    expect(picker().value).toBe("12");
  });

  it("keeps a shelf that is no longer in the store rather than swallowing it",
    async () => {
      // A deleted shelf, or another eBay account's id. Silently showing the
      // first option instead would tell the seller their listing is filed
      // somewhere it is not.
      serve({ store: true, checked: true, categories: SHELVES });
      await render(stub([], { store_category_id: "77",
                              store_category_name: "Old Shelf" }));
      expect(picker().value).toBe("77");
      expect(host.textContent).toContain("not in your store any more");
    });

  it("draws nothing at all for a seller with no eBay Store", async () => {
    serve({ store: false, checked: true, categories: [] });
    await render(stub([]));
    expect(picker()).toBeNull();
    expect(host.textContent).not.toContain("Store category");
  });

  it("draws nothing when eBay isn't connected", async () => {
    serve({ store: false, checked: true, categories: [] }, { connected: false });
    await render(stub([]));
    expect(picker()).toBeNull();
  });

  it("says when it couldn't ask, instead of reporting an empty store", async () => {
    serve({ store: false, checked: false, categories: [], error: "rate limited" });
    await render(stub([]));
    expect(picker()).toBeNull();
    expect(host.textContent).toContain("couldn’t read your eBay Store categories");
  });
});

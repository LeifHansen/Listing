/* Move to store category, from the Manage tab.
 *
 * Under More: pick a shelf of the seller's own eBay Store, and every ticked
 * listing goes on it — the request names the shelf's id and the ticks, and
 * the report says where they went by the shelf's name. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { job, live, mountManage } from "@/test/manageHarness";

const SHELVES = {
  store: true, checked: true,
  categories: [
    { id: 11, name: "Vintage Tees", path: "Vintage Tees", level: 1 },
    { id: 12, name: "Beanies", path: "Beanies", level: 1 },
  ],
};

describe("Move to store category from the Manage tab", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("moves the ticked listings to the shelf picked", async () => {
    const run = job("shelf-1", { changed: 2, skipped: 0, failed: 0,
      store_category_name: "Vintage Tees", results: { changed: [], skipped: [], failed: [] } });
    m = await mountManage(
      [live("a", "Tee one"), live("b", "Tee two"), live("c", "Not ticked")],
      { "/api/ebay/move-store-category": run.start, "/api/bulk/status/shelf-1": run.status },
      { base: { "/api/ebay/store-categories": SHELVES } });
    await m.click(m.ticks()[0]);
    await m.click(m.ticks()[1]);
    await m.openMore();
    await m.click(m.menuItem("Move to store category"));
    await m.settle();

    const select = m.host.querySelector('select[aria-label="Store category"]');
    expect([...select.options].map((o) => o.textContent))
      .toEqual(["Pick a store category…", "Vintage Tees", "Beanies"]);
    // Nothing to send until a shelf is picked.
    expect(m.button("Move 2").disabled).toBe(true);
    await act(async () => {
      select.value = "11";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await m.click(m.button("Move 2 to “Vintage Tees”"));
    await m.settle(20);

    expect(m.state.asked["/api/ebay/move-store-category"])
      .toEqual({ store_category_id: "11", listing_ids: ["a", "b"] });
    expect(m.text()).toContain("Moved 2 listings to “Vintage Tees”");
  });

  it("says so, rather than offering an empty list, to an account with no Store", async () => {
    m = await mountManage([live("a", "Tee")], {},
      { base: { "/api/ebay/store-categories": { store: false, checked: true, categories: [] } } });
    await m.click(m.ticks()[0]);
    await m.openMore();
    await m.click(m.menuItem("Move to store category"));
    await m.settle();
    expect(m.text()).toContain("doesn’t have one");
    expect(m.button("Move 1").disabled).toBe(true);
  });
});

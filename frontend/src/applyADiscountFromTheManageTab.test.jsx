/* Apply discount, from the Manage tab: a quick eBay sale over the ticks.
 *
 * Pick how much off and for how long; one request starts an eBay sale of
 * exactly the ticked listings, the store takes the server's list of sales, and
 * the cards show the discount. The asking prices never move — that is Lower
 * prices' job, not this one's. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { live, mountManage } from "@/test/manageHarness";

const day = 86400000;

describe("Apply discount from the Manage tab", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("starts one eBay sale of the ticks, at the percentage and length picked", async () => {
    const made = {
      id: "se_9", name: "25% off · 14 days · Nov 24", percent_off: 25, status: "SCHEDULED",
      start_at: new Date(Date.now() - 1000).toISOString(),
      end_at: new Date(Date.now() + 14 * day).toISOString(),
      item_ids: ["1a", "1b"], editable: true, source: "app", count: 2, state: "running",
    };
    m = await mountManage(
      [live("a", "Jacket"), live("b", "Scarf"), live("c", "Not ticked")],
      { "/api/sale-events": () => ({ events: [made], event: made, connected: true,
        report: { changed: 2, skipped: 0, failed: 0, results: { changed: [], skipped: [], failed: [] } } }) },
      { base: { "/api/sale-events?": { events: [], connected: true, checked: true } } });
    await m.click(m.ticks()[0]);
    await m.click(m.ticks()[1]);
    await m.click(m.button("Apply discount…"));
    await m.settle();
    expect(m.text()).toContain("Your asking prices don’t change");

    await m.click(m.button("25%"));
    await m.click(m.button("14 days"));
    await m.click(m.button("Put 2 on sale at 25% off"));
    await m.settle(20);

    expect(m.state.asked["/api/sale-events"])
      .toEqual({ percent: 25, days: 14, move: false, listing_ids: ["a", "b"] });
    expect(m.text()).toContain("2 listings on sale at 25% off");
    // The cards pick the sale up from the adopted list.
    expect(m.text()).toMatch(/−25% · ends in 1[34]d/);
  });

  it("offers to move listings out of another sale only when some are in one", async () => {
    const other = { id: "se_1", name: "Spring", percent_off: 10, status: "RUNNING",
      start_at: new Date(Date.now() - day).toISOString(),
      end_at: new Date(Date.now() + day).toISOString(),
      item_ids: ["1a"], editable: true, source: "app", count: 1 };
    m = await mountManage([live("a", "In Spring"), live("b", "Free")], {},
      { base: { "/api/sale-events": { events: [other], connected: true, checked: true } }, whole: true });
    await m.click(m.ticks()[1]);
    await m.click(m.button("Apply discount…"));
    await m.settle();
    expect(m.text()).not.toContain("already in another sale");
    await m.click(m.ticks()[0]);
    expect(m.text()).toContain("Move the 1 already in another sale");
  });
});

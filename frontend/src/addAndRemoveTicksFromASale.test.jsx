/* Add to sale, and Remove from sale, from the Manage tab.
 *
 * Add to sale lists the sales the seller can still add to — running or to
 * come, and made the way this app can edit — and puts the ticks in the one
 * picked. Remove from sale takes the ticks out of whichever sales they are in,
 * after saying they go back to full price. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { live, mountManage } from "@/test/manageHarness";

const day = 86400000;
const ev = (id, name, over = {}) => ({
  id, name, percent_off: 20, status: "RUNNING",
  start_at: new Date(Date.now() - day).toISOString(),
  end_at: new Date(Date.now() + 5 * day).toISOString(),
  item_ids: [], editable: true, source: "app", count: 0, ...over,
});

describe("adding ticked listings to a sale", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("lists only the sales it can add to, and adds to the one picked", async () => {
    const spring = ev("se_1", "Spring");
    const hub = ev("se_2", "Hub tiers", { editable: false, read_only_reason: "Different discounts for different listings" });
    const done = ev("se_3", "Last month", { end_at: new Date(Date.now() - day).toISOString() });
    const after = { ...spring, item_ids: ["1a"], count: 1 };
    m = await mountManage([live("a", "Jacket")], {
      "/api/sale-events/se_1/listings": () => ({ events: [after, hub, done], event: after,
        report: { changed: 1, skipped: 0, failed: 0, results: { changed: [], skipped: [], failed: [] } } }),
    }, { base: { "/api/sale-events": { events: [spring, hub, done], connected: true, checked: true } }, whole: true });
    await m.click(m.ticks()[0]);
    await m.click(m.button("Add to sale…"));
    await m.settle();
    const options = [...m.host.querySelectorAll('[role="radiogroup"] label')].map((l) => l.textContent);
    expect(options).toHaveLength(1);
    expect(options[0]).toContain("Spring");

    await m.click(m.button("Add 1 to “Spring”"));
    await m.settle(20);
    expect(m.state.asked["/api/sale-events/se_1/listings"]).toEqual({ move: false, listing_ids: ["a"] });
    expect(m.text()).toContain("Added 1 listing to “Spring”");
  });

  it("offers a new sale when there is none to add to", async () => {
    m = await mountManage([live("a", "Jacket")], {},
      { base: { "/api/sale-events": { events: [], connected: true, checked: true } }, whole: true });
    await m.click(m.ticks()[0]);
    await m.click(m.button("Add to sale…"));
    await m.settle();
    expect(m.text()).toContain("No sale to add to yet");
    await m.click(m.button("New sale…"));
    await m.settle();
    expect(document.body.querySelector('[role="dialog"]').textContent).toContain("The 1 you ticked on Listings");
  });
});

describe("taking ticked listings out of their sales", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("is offered only for ticks that are in a sale, and asks first", async () => {
    const spring = ev("se_1", "Spring", { item_ids: ["1a"], count: 1 });
    m = await mountManage([live("a", "On sale"), live("b", "Not")], {
      "/api/sale-events/remove-listings": () => ({ events: [], report: { changed: 1, skipped: 0,
        failed: 0, results: { changed: [], skipped: [], failed: [] } } }),
    }, { base: { "/api/sale-events": { events: [spring], connected: true, checked: true } }, whole: true });
    await m.click(m.ticks()[1]);
    await m.openMore();
    expect(m.menuItem("Remove from sale").disabled).toBe(true);
    await m.click(document.body.querySelector('button[aria-label="More bulk actions"]'));
    await m.click(m.ticks()[0]);
    await m.openMore();
    await m.click(m.menuItem("Remove from sale (1)"));
    await m.settle();
    expect(m.text()).toContain("back to full price on eBay");
    const confirmBtn = [...document.body.querySelectorAll('[role="dialog"] button')]
      .find((b) => b.textContent.trim() === "Take out of sale");
    await m.click(confirmBtn);
    await m.settle(20);
    expect(m.state.asked["/api/sale-events/remove-listings"].listing_ids).toEqual(["a", "b"]);
    expect(m.text()).toContain("Took 1 listing out of its sale");
  });
});

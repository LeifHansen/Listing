/* A listing in a running eBay sale shows what buyers pay, with its own price
 * struck through and how long the sale lasts — worked out from the sale, never
 * written onto the listing. One still to come says when it starts. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { live, mountManage } from "@/test/manageHarness";

const day = 86400000;
const sale = (over) => ({
  id: "se_1", name: "Spring", percent_off: 20, status: "RUNNING",
  start_at: new Date(Date.now() - day).toISOString(),
  end_at: new Date(Date.now() + 3 * day).toISOString(),
  item_ids: ["1a"], editable: true, source: "app", count: 1, ...over,
});

describe("a card on sale", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("leads with the sale price, the asking price struck through", async () => {
    m = await mountManage([live("a", "Denim jacket", { price: 40 }), live("b", "Not on sale")], {},
      { base: { "/api/sale-events": { events: [sale()], connected: true, checked: true } }, whole: true });
    const struck = [...m.host.querySelectorAll(".line-through")].map((n) => n.textContent);
    expect(struck).toContain("$40.00");
    expect(m.text()).toContain("$32.00");
    expect(m.text()).toMatch(/−20% · ends in 3d/);
  });

  it("says when a sale still to come starts, and leaves the price alone", async () => {
    const later = sale({ status: "SCHEDULED", start_at: new Date(Date.now() + 2 * day).toISOString(),
      end_at: new Date(Date.now() + 9 * day).toISOString() });
    m = await mountManage([live("a", "Denim jacket", { price: 40 })], {},
      { base: { "/api/sale-events": { events: [later], connected: true, checked: true } }, whole: true });
    expect(m.text()).toMatch(/−20% · starts /);
    expect(m.host.querySelectorAll(".line-through").length).toBe(0);
  });
});

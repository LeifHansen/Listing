/* Quick edit on a listing in an eBay sale warns before a price change: eBay
 * takes a listing out of its sale when its price is revised. The seller may
 * mean it — the warning does not stop the save — but should know. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { live, mountManage } from "@/test/manageHarness";

const day = 86400000;
const spring = {
  id: "se_1", name: "Spring", percent_off: 20, status: "RUNNING",
  start_at: new Date(Date.now() - day).toISOString(),
  end_at: new Date(Date.now() + 5 * day).toISOString(),
  item_ids: ["1a"], editable: true, source: "app", count: 1,
};

describe("quick edit on a listing in a sale", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("warns once the price is changed, not before", async () => {
    m = await mountManage([live("a", "Jacket", { price: 40 })], {},
      { base: { "/api/sale-events": { events: [spring], connected: true, checked: true } }, whole: true });
    await m.click(m.button("Quick edit"));
    await m.settle();
    expect(m.text()).not.toContain("takes it out of the sale");
    const box = [...m.host.querySelectorAll("input[type=number]")].find((i) => i.value === "40");
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(box, "35");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(m.text()).toContain("It’s in “Spring” (−20%). Changing the price takes it out of the sale on eBay.");
  });
});

/* Send offers to watchers, from the Manage tab: the dashboard's "Send
 * offers", over the ticks — eBay's 5% floor and this app's 50% ceiling on the
 * amount, and the listed price untouched. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { job, live, mountManage } from "@/test/manageHarness";

describe("Send offers from the Manage tab", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("offers the ticked listings' watchers the percentage typed", async () => {
    const run = job("offers-1", { percent: 15, changed: 1, skipped: 1, failed: 0,
      results: { changed: [], failed: [],
        skipped: [{ listing_id: "b", title: "Quiet one", message: "eBay has no interested buyers for it right now." }] } });
    m = await mountManage([live("a", "Watched"), live("b", "Quiet one")], {
      "/api/ebay/send-offers-ticked": run.start, "/api/bulk/status/offers-1": run.status,
    });
    await m.click(m.ticks()[0]);
    await m.click(m.ticks()[1]);
    await m.openMore();
    await m.click(m.menuItem("Send offers to watchers"));
    await m.settle();
    expect(m.text()).toContain("Your listed price doesn't change");

    const box = m.host.querySelector("input[type=number]");
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(box, "15");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await m.click(m.button("Send 15% off"));
    await m.settle(20);
    expect(m.state.asked["/api/ebay/send-offers-ticked"]).toEqual({ percent: 15, listing_ids: ["a", "b"] });
    expect(m.text()).toContain("Sent 15% off to the watchers of 1 listing");
    expect(m.text()).toContain("Quiet one: eBay has no interested buyers");
  });

  it("will not send below eBay's floor", async () => {
    m = await mountManage([live("a", "Watched")], {});
    await m.click(m.ticks()[0]);
    await m.openMore();
    await m.click(m.menuItem("Send offers to watchers"));
    await m.settle();
    const box = m.host.querySelector("input[type=number]");
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(box, "3");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(m.text()).toContain("Enter a number between 5 and 50");
    expect(m.button("Send 3% off").disabled).toBe(true);
  });
});

/* The selection bar holds the weekly actions; the rest are under More.
 *
 * With eight things a tick can lead to, a bar of buttons wraps into a wall on
 * a phone. What this pins: End & relist and Lower prices stay on the bar,
 * the rarer and the irreversible ones are under More, only one action's
 * panel is open at a time, nothing can start while a run is going, and
 * "Select all" says when it means "all shown". */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { live, mountManage } from "@/test/manageHarness";

describe("the Manage selection bar", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("keeps the weekly actions on the bar and the rest under More", async () => {
    m = await mountManage([live("a", "One"), live("b", "Two")]);
    await m.click(m.ticks()[0]);
    expect(m.button("End & relist (1)")).toBeTruthy();
    expect(m.button("Lower prices…")).toBeTruthy();
    expect(m.button("End listings")).toBeFalsy();

    await m.openMore();
    const labels = [...document.body.querySelectorAll('[role="menuitem"]')].map((b) => b.textContent);
    // "Add to sale…" is on the bar from small screens up and under More on a
    // phone — both are in the DOM, the breakpoint decides which shows.
    expect(labels).toEqual(["Add to sale…", "Remove from sale",
      "Move to store category…", "Send offers to watchers…", "End listings"]);
    expect(m.button("Apply discount…")).toBeTruthy();
    const end = m.menuItem("End listings");
    expect(end.className).toContain("text-error");
  });

  it("opens one action's panel at a time", async () => {
    m = await mountManage([live("a", "One")]);
    await m.click(m.ticks()[0]);
    await m.click(m.button("Lower prices…"));
    await m.settle();
    expect(m.text()).toContain("Lower each ticked price by");
    await m.openMore();
    await m.click(m.menuItem("Send offers to watchers"));
    await m.settle(250);
    expect(m.text()).toContain("Offer each ticked listing's watchers");
    expect(m.text()).not.toContain("Lower each ticked price by");
  });

  it("says Select all means what is shown while a filter is on", async () => {
    m = await mountManage([live("a", "Nike tee", { brand: "Nike" }), live("b", "Adidas", { brand: "Adidas" })]);
    await m.click(m.ticks()[0]);
    expect(m.text()).toContain("Select all(1 of 2)");
    // Filter down via the panel's brand box.
    await m.click(m.button("Filters"));
    const brand = [...m.host.querySelectorAll("input")].find((i) => i.placeholder === "Nike");
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      set.call(brand, "nike");
      brand.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(m.text()).toContain("Select all 1 shown(1 of 1)");
  });

  it("starts nothing new while a run is going", async () => {
    let release;
    m = await mountManage([live("a", "One")], {
      "/api/ebay/end-and-relist": () => new Promise((r) => { release = r; }),
    });
    await m.click(m.ticks()[0]);
    await m.click(m.button("End & relist (1)"));
    await m.settle();
    const confirmBtn = [...document.body.querySelectorAll('[role="dialog"] button')]
      .find((b) => b.textContent.trim() === "End & relist");
    await m.click(confirmBtn);
    await m.settle();
    expect(m.button("Lower prices…").disabled).toBe(true);
    expect(document.body.querySelector('button[aria-label="More bulk actions"]').disabled).toBe(true);
    release && release({ ok: true, status: 500, headers: { get: () => "application/json" },
      json: () => Promise.resolve({ detail: "stop" }), text: () => Promise.resolve("") });
    await m.settle(20);
  });
});

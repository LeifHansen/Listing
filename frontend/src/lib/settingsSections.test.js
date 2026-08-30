/**
 * Settings touches two different systems, and used to report one answer.
 *
 * Saving wrote the seller's local packing defaults to this app AND their
 * publish defaults to eBay, in one action, inside one try. When the eBay half
 * failed the whole thing said "Couldn't save" — so a seller whose packing
 * defaults had just been committed was told their work had not been saved,
 * and the natural response is to type it again. The reverse is worse: nothing
 * distinguished "eBay refused" from "the app is down".
 *
 * Loading had the mirror-image bug. A policies fetch that FAILED left the
 * dropdowns empty, and empty dropdowns render as "you don't have business
 * policies, here's a button to create them" — the app stating something about
 * the seller's eBay account that it had just failed to find out.
 */
import { describe, expect, it, vi } from "vitest";

import { saveSections, policyView } from "./settingsSections.js";

const ok = (name) => ({ name, run: () => Promise.resolve() });
const fails = (name, message) => ({
  name, run: () => Promise.reject(new Error(message)),
});

describe("saving two systems at once", () => {
  it("still saves the second when the first fails", async () => {
    const ran = [];
    const r = await saveSections([
      { name: "a", run: () => { ran.push("a"); return Promise.reject(new Error("no")); } },
      { name: "b", run: () => { ran.push("b"); return Promise.resolve(); } },
    ]);
    expect(ran).toEqual(["a", "b"]);
    expect(r.saved).toEqual(["b"]);
    expect(r.failed.map((f) => f.name)).toEqual(["a"]);
  });

  it("reports the half that committed rather than one verdict for both", async () => {
    // The finding: this said "Couldn't save" and named nothing.
    const r = await saveSections([ok("Your packing defaults"),
                                  fails("Your eBay defaults", "eBay timed out")]);

    expect(r.ok).toBe(false);
    expect(r.message).toContain("Your packing defaults");
    expect(r.message).toContain("saved");
    expect(r.message).toContain("Your eBay defaults");
    expect(r.message).toContain("eBay timed out");
  });

  it("says everything saved when everything did", async () => {
    const r = await saveSections([ok("A"), ok("B")]);
    expect(r.ok).toBe(true);
    expect(r.failed).toEqual([]);
  });

  it("does not claim a save when every part failed", async () => {
    const r = await saveSections([fails("A", "x"), fails("B", "y")]);
    expect(r.ok).toBe(false);
    expect(r.saved).toEqual([]);
    expect(r.message.toLowerCase()).not.toContain("saved");
  });

  it("skips sections that have nothing to send", async () => {
    const spy = vi.fn(() => Promise.resolve());
    const r = await saveSections([{ name: "A", when: false, run: spy },
                                  { name: "B", run: () => Promise.resolve() }]);
    expect(spy).not.toHaveBeenCalled();
    expect(r.saved).toEqual(["B"]);
  });

  // The screen this runs behind can be in a state where NOTHING is sendable:
  // the defaults read failed (so `prefs` is null and that section is skipped
  // to stop the app's fallbacks being posted as the seller's choices) and the
  // eBay half is skipped too, because it is either not connected or its own
  // load failed. Nothing failed, because nothing was tried -- and the report
  // read "Defaults saved", on a screen already saying it could not read them.
  it("does not report a save when every section was skipped", async () => {
    const r = await saveSections([{ name: "A", when: false, run: () => Promise.resolve() },
                                  { name: "B", when: false, run: () => Promise.resolve() }]);
    expect(r.saved).toEqual([]);
    expect(r.message.toLowerCase()).not.toContain("defaults saved");
    // And it has to be a failure, because the toast picks its colour off this
    // and a green tick is the whole claim.
    expect(r.ok).toBe(false);
  });

  it("says why nothing could be sent, not just that nothing was", async () => {
    const r = await saveSections([{ name: "Your listing defaults", when: false,
                                    run: () => Promise.resolve() }]);
    // Actionable and implementation-neutral: the seller's next move is to
    // wait and press it again, not to retype settings they cannot even see.
    expect(r.message).toMatch(/couldn.t/i);
    expect(r.message.toLowerCase()).toContain("nothing was saved");
  });

  it("survives a rejection that is not an Error", async () => {
    // A fetch layer that throws a string must not take the reporting with it.
    const r = await saveSections([{ name: "A", run: () => Promise.reject("nope") }]);
    expect(r.ok).toBe(false);
    expect(r.failed[0].message).toBeTruthy();
  });
});

describe("what the policies panel is allowed to say", () => {
  const three = { fulfillment: [{ id: "1" }], payment: [{ id: "2" }], return: [{ id: "3" }] };

  it("says nothing about the account while it is still loading", () => {
    expect(policyView({ status: "loading" }).kind).toBe("loading");
  });

  it("does not report a failed load as an account with no policies", () => {
    // The finding. This rendered the "you have no business policies" tile.
    const v = policyView({ status: "unavailable", error: "network" });
    expect(v.kind).toBe("unavailable");
    expect(v.message).toMatch(/couldn.t|could not/i);
    expect(v.message.toLowerCase()).not.toContain("you don't have");
  });

  it("reports genuinely missing policies once eBay has actually answered", () => {
    const v = policyView({ status: "ready", policies: { ...three, payment: [] } });
    expect(v.kind).toBe("missing");
    expect(v.missing).toEqual(["payment"]);
  });

  it("is quiet when all three exist", () => {
    expect(policyView({ status: "ready", policies: three }).kind).toBe("ok");
  });

  it("treats a missing policies object as unknown, not as empty", () => {
    // `data` is seeded from a shared cache, so it can be truthy having loaded
    // nothing here. Absent is not the same as answered-with-none.
    expect(policyView({ status: "ready" }).kind).toBe("unavailable");
  });
});
